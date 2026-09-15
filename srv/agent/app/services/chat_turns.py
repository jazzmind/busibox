"""
Detached chat turns.

A chat request used to run inside its own HTTP response: the agent, the tool
calls and the final database commit all lived in the SSE generator, so when
the browser went away (laptop sleep, closed tab, navigation) the generator
was cancelled and the whole turn — user message included — rolled back.
Deep research turns lost minutes of work and their credits that way.

Now a request *starts a turn* and returns a subscription to it:

    POST /chat/message/stream/agentic
        └─ start_turn():  commit conversation + user message,
                          create chat_turns row (running),
                          asyncio.create_task(_run_turn(...))
        └─ subscribe():   replay + follow the turn's event log

The runner writes every event to the event log (Redis Streams, memory
fallback) *before* any client sees it, commits the assistant message when the
agent finishes whether or not anyone is listening, and emails the user if
they were not attached when it ended. Disconnecting only unsubscribes; the
explicit Stop button is the one thing that cancels.

Everything the old handler persisted (routing thoughts, citations, run
record, insights, online eval sampling) is preserved here — the code moved,
it did not change shape.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.db.session import get_session_context
from app.models.domain import ChatAttachment, ChatSettings, ChatTurn, Conversation, Message
from app.schemas.auth import Principal
from app.services.turn_events import TERMINAL_EVENT, EventLog, LoggedEvent, get_event_log

logger = logging.getLogger(__name__)

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"
TERMINAL_STATUSES = {COMPLETED, FAILED, CANCELLED, INTERRUPTED}

# Thought events persisted verbatim on the assistant message (same set the
# inline handler stored), so the UI can re-render the thinking section.
_PERSISTED_THOUGHT_TYPES = ("thought", "tool_start", "tool_result", "plan", "progress")


class TurnLimitError(Exception):
    """The user already has the maximum number of running turns."""


class ConversationBusyError(Exception):
    """A turn is already running in this conversation."""


@dataclass
class RunningTurn:
    turn_id: uuid.UUID
    user_id: str
    conversation_id: uuid.UUID
    task: asyncio.Task
    cancel: asyncio.Event
    started: float = field(default_factory=time.monotonic)
    subscribers: int = 0
    stop_requested: bool = False


class TurnRegistry:
    """In-process table of running turns (one agent-api process today)."""

    def __init__(self) -> None:
        self._turns: Dict[uuid.UUID, RunningTurn] = {}

    def add(self, rt: RunningTurn) -> None:
        self._turns[rt.turn_id] = rt

    def get(self, turn_id: uuid.UUID) -> Optional[RunningTurn]:
        return self._turns.get(turn_id)

    def remove(self, turn_id: uuid.UUID) -> None:
        self._turns.pop(turn_id, None)

    def running_for_user(self, user_id: str) -> List[RunningTurn]:
        return [t for t in self._turns.values() if t.user_id == user_id]

    def running_in_conversation(self, conversation_id: uuid.UUID) -> Optional[RunningTurn]:
        for t in self._turns.values():
            if t.conversation_id == conversation_id:
                return t
        return None

    def all(self) -> List[RunningTurn]:
        return list(self._turns.values())


_registry = TurnRegistry()


def get_registry() -> TurnRegistry:
    return _registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_bridge_request_metadata(metadata: Optional[Dict[str, Any]]) -> bool:
    from app.api.chat import _is_bridge_request

    return _is_bridge_request(metadata)


def _title_for(message: str) -> str:
    return message[:50] + "..." if len(message) > 50 else message


class _Emitter:
    """Appends SSE frames to the event log for one turn."""

    def __init__(self, log: EventLog, turn_id: uuid.UUID):
        self.log = log
        self.turn_id = str(turn_id)
        self.count = 0
        self.last_id: Optional[str] = None

    async def emit(self, event_type: str, data: Any) -> str:
        payload = data if isinstance(data, str) else json.dumps(data, default=str)
        self.last_id = await self.log.append(self.turn_id, event_type, payload)
        self.count += 1
        return self.last_id


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


async def start_turn(
    payload: Any,
    principal: Principal,
    *,
    use_test_db: bool = False,
) -> ChatTurn:
    """Persist the request and launch the agent as a background task.

    Returns the ``ChatTurn`` row (status ``running``). Raises
    ``TurnLimitError`` / ``ConversationBusyError`` / ``LookupError`` before
    anything is written.
    """
    settings = get_settings()
    registry = get_registry()
    if len(registry.running_for_user(principal.sub)) >= int(settings.chat_max_running_turns_per_user):
        raise TurnLimitError(f"You already have {settings.chat_max_running_turns_per_user} responses in progress; wait for one to finish.")
    if payload.conversation_id and registry.running_in_conversation(payload.conversation_id):
        raise ConversationBusyError("A response is still being written in this conversation; wait for it or stop it first.")

    log = await get_event_log()
    created_conversation = False
    title_updated = False

    async with get_session_context(use_test_db) as session:
        if payload.conversation_id:
            result = await session.execute(
                select(Conversation).where(
                    Conversation.id == payload.conversation_id,
                    Conversation.user_id == principal.sub,
                )
            )
            conversation = result.scalar_one_or_none()
            if not conversation:
                raise LookupError("Conversation not found")
            if conversation.title == "New Conversation":
                conversation.title = _title_for(payload.message)
                title_updated = True
        else:
            conversation = Conversation(title=_title_for(payload.message), user_id=principal.sub)
            session.add(conversation)
            await session.flush()
            created_conversation = True

        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=payload.message,
            attachments=[att.model_dump() for att in payload.attachments] if payload.attachments else None,
        )
        session.add(user_message)
        await session.flush()

        if payload.attachment_ids:
            attachment_result = await session.execute(
                select(ChatAttachment).where(ChatAttachment.id.in_(payload.attachment_ids))
            )
            for attachment in attachment_result.scalars().all():
                attachment.message_id = user_message.id

        turn = ChatTurn(
            conversation_id=conversation.id,
            user_id=principal.sub,
            status=RUNNING,
            query=payload.message,
            user_message_id=user_message.id,
        )
        session.add(turn)
        conversation.updated_at = _now()
        # The request is on disk before the agent starts: an interrupted turn
        # can never again take the question with it.
        await session.commit()
        await session.refresh(turn)
        turn_id, conversation_id, user_message_id = turn.id, conversation.id, user_message.id
        conversation_title = conversation.title

    emitter = _Emitter(log, turn_id)
    await emitter.emit("turn_started", {
        "turn_id": str(turn_id),
        "conversation_id": str(conversation_id),
        "started_at": _now().isoformat(),
    })
    if created_conversation:
        await emitter.emit("conversation_created", {"conversation_id": str(conversation_id), "title": conversation_title})
    elif title_updated:
        await emitter.emit("title_update", {"conversation_id": str(conversation_id), "title": conversation_title})

    cancel = asyncio.Event()
    task = asyncio.create_task(
        _run_turn(
            turn_id=turn_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            payload=payload,
            principal=principal,
            cancel=cancel,
            emitter=emitter,
            use_test_db=use_test_db,
        ),
        name=f"chat-turn-{turn_id}",
    )
    registry.add(RunningTurn(turn_id=turn_id, user_id=principal.sub, conversation_id=conversation_id, task=task, cancel=cancel))
    logger.info("chat turn started", extra={"turn_id": str(turn_id), "conversation_id": str(conversation_id), "user_id": principal.sub})
    return turn


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def _load_history(session: AsyncSession, conversation_id: uuid.UUID, user_message_id: uuid.UUID, payload: Any):
    """The 20 most recent prior messages as prompt dicts, plus attachment
    metadata for this turn (own files, else the most recent turn's carried
    forward). Lifted unchanged from the inline handler."""
    from app.api.chat import _extract_file_id_from_url  # lazy: chat.py imports this module

    attachment_metadata: List[Dict[str, Any]] = []
    if payload.attachment_ids:
        attachment_result = await session.execute(
            select(ChatAttachment).where(ChatAttachment.id.in_(payload.attachment_ids))
        )
        for attachment in attachment_result.scalars().all():
            attachment_metadata.append({
                "id": str(attachment.id),
                "file_id": _extract_file_id_from_url(attachment.file_url),
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "file_url": attachment.file_url,
                "parsed_content": attachment.parsed_content,
            })

    recent_ids_subq = (
        select(Message.id)
        .where(Message.conversation_id == conversation_id)
        .where(Message.id != user_message_id)
        .order_by(desc(Message.created_at))
        .limit(20)
        .scalar_subquery()
    )
    history_result = await session.execute(
        select(Message).where(Message.id.in_(recent_ids_subq)).order_by(Message.created_at.asc())
    )
    history_messages = list(history_result.scalars().all())

    prior_files_by_message: Dict[uuid.UUID, List[ChatAttachment]] = {}
    prior_user_ids = [m.id for m in history_messages if m.role == "user"]
    if prior_user_ids:
        prior_result = await session.execute(
            select(ChatAttachment).where(ChatAttachment.message_id.in_(prior_user_ids))
        )
        for prior in prior_result.scalars().all():
            prior_files_by_message.setdefault(prior.message_id, []).append(prior)

    history_dicts: List[Dict[str, Any]] = []
    for msg in history_messages:
        content_text = msg.content or ""
        prior_files = prior_files_by_message.get(msg.id)
        if prior_files:
            content_text = f"{content_text}\n[Attached: {', '.join(p.filename for p in prior_files)}]".strip()
        entry: Dict[str, Any] = {"role": msg.role, "content": content_text}
        if msg.role == "assistant" and isinstance(msg.routing_decision, dict):
            for t in msg.routing_decision.get("thoughts") or []:
                data = t.get("data") if isinstance(t, dict) else None
                if isinstance(data, dict) and data.get("phase") == "intent_routing":
                    entry["action_type"] = data.get("action_type")
                    if data.get("pending_research"):
                        entry["pending_research"] = data["pending_research"]
                    break
        history_dicts.append(entry)

    if not attachment_metadata and prior_files_by_message:
        for msg in reversed(history_messages):
            prior_files = prior_files_by_message.get(msg.id)
            if not prior_files:
                continue
            for prior in prior_files:
                attachment_metadata.append({
                    "id": str(prior.id),
                    "file_id": _extract_file_id_from_url(prior.file_url),
                    "filename": prior.filename,
                    "mime_type": prior.mime_type,
                    "file_url": prior.file_url,
                    "parsed_content": prior.parsed_content,
                    "carried_forward": True,
                })
            break
    return history_messages, history_dicts, attachment_metadata


async def _run_turn(
    *,
    turn_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_message_id: uuid.UUID,
    payload: Any,
    principal: Principal,
    cancel: asyncio.Event,
    emitter: _Emitter,
    use_test_db: bool,
) -> None:
    from app.api.chat import BRIDGE_FILTERED_AGENTIC_EVENTS, _is_bridge_request
    from app.services.agentic_dispatcher import run_agentic_dispatcher
    from app.services.platform_config import get_platform_insights_enabled

    t0 = time.monotonic()
    status = COMPLETED
    error_text: Optional[str] = None
    full_content: List[str] = []
    fast_ack_content: Optional[str] = None
    thoughts: List[Dict[str, Any]] = []
    run_events: List[Dict[str, Any]] = []
    citations_by_file: Dict[str, Any] = {}
    selected_agent_id: Optional[str] = None
    assistant_message_id: Optional[uuid.UUID] = None
    conversation_title = ""
    suppress_thinking = _is_bridge_request(payload.metadata)
    available_agents = payload.selected_agents or ["chat"]

    try:
        async with get_session_context(use_test_db) as session:
            conversation = (await session.execute(select(Conversation).where(Conversation.id == conversation_id))).scalar_one()
            conversation_title = conversation.title
            user_message = (await session.execute(select(Message).where(Message.id == user_message_id))).scalar_one()
            history_messages, history_dicts, attachment_metadata = await _load_history(session, conversation_id, user_message_id, payload)

            dispatcher_metadata: Dict[str, Any] = dict(payload.metadata or {})
            dispatcher_metadata["conversation_id"] = str(conversation_id)
            dispatcher_metadata["turn_id"] = str(turn_id)

            try:
                async for event in run_agentic_dispatcher(
                    query=payload.message,
                    user_id=principal.sub,
                    session=session,
                    cancel=cancel,
                    available_agents=available_agents,
                    conversation_history=history_dicts,
                    principal=principal,
                    metadata=dispatcher_metadata,
                    attachment_metadata=attachment_metadata,
                    insights_enabled=get_platform_insights_enabled(),
                ):
                    if not (suppress_thinking and event.type in BRIDGE_FILTERED_AGENTIC_EVENTS):
                        await emitter.emit(event.type, event.model_dump_json())

                    if event.type == "content":
                        phase = event.data.get("phase") if isinstance(event.data, dict) else None
                        if phase == "fast_ack":
                            fast_ack_content = event.message
                        elif phase == "interim":
                            pass
                        else:
                            full_content.append(event.message)
                    elif event.type in _PERSISTED_THOUGHT_TYPES:
                        item: Dict[str, Any] = {"type": event.type, "source": event.source, "message": event.message}
                        if event.type == "thought" and isinstance(event.data, dict):
                            phase = event.data.get("phase")
                            if phase == "intent_routing":
                                item["data"] = {
                                    "phase": "intent_routing",
                                    "action_type": event.data.get("action_type"),
                                    "needs_tools": event.data.get("needs_tools"),
                                    "confidence": event.data.get("confidence"),
                                    "routing_source": event.data.get("routing_source"),
                                    "follow_up_question": event.data.get("follow_up_question"),
                                    "preferred_tool": event.data.get("preferred_tool"),
                                    "pending_research": event.data.get("pending_research"),
                                }
                            elif phase:
                                item["data"] = {"phase": phase}
                        thoughts.append(item)

                    if event.type == "tool_result" and event.source == "document_search" and isinstance(event.data, dict):
                        for item in event.data.get("results", []):
                            fid = item.get("file_id") or item.get("fileId")
                            if not fid:
                                continue
                            new_score = float(item.get("score", 0.0))
                            page = item.get("page_number") or item.get("pageNumber") or None
                            if fid not in citations_by_file or new_score > citations_by_file[fid]["score"]:
                                citations_by_file[fid] = {"file_id": fid, "filename": item.get("filename", ""), "page_number": page, "score": new_score}

                    run_events.append({
                        "type": event.type, "source": event.source,
                        "message": event.message[:500] if event.message else "",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    if event.data and isinstance(event.data, dict) and "selected_agent" in event.data:
                        selected_agent_id = event.data["selected_agent"]

                    if cancel.is_set():
                        status = CANCELLED
                        break
            except asyncio.CancelledError:
                # Hard cancel (stop grace expired, or process shutdown). Persist what we have.
                status = INTERRUPTED if not cancel.is_set() else CANCELLED
                raise
            except Exception as exc:  # noqa: BLE001 — the turn must still be recorded
                status = FAILED
                error_text = str(exc)
                logger.error("chat turn failed", extra={"turn_id": str(turn_id)}, exc_info=True)
                await emitter.emit("error", {"error": error_text, "source": "dispatcher"})
            finally:
                if cancel.is_set() and status == COMPLETED:
                    status = CANCELLED
                assistant_message_id = await _persist_result(
                    session=session,
                    conversation=conversation,
                    turn_id=turn_id,
                    status=status,
                    error_text=error_text,
                    full_content=full_content,
                    fast_ack_content=fast_ack_content,
                    thoughts=thoughts,
                    run_events=run_events,
                    citations_by_file=citations_by_file,
                    available_agents=available_agents,
                    selected_agent_id=selected_agent_id,
                    payload=payload,
                    principal=principal,
                    elapsed_s=time.monotonic() - t0,
                    emitter=emitter,
                )

            if status == COMPLETED:
                await _after_success(session, conversation, history_messages, user_message, assistant_message_id, payload, principal, emitter, selected_agent_id, full_content, use_test_db)
    except asyncio.CancelledError:
        pass  # already persisted in the inner finally
    except Exception as exc:  # noqa: BLE001
        status = FAILED
        error_text = error_text or str(exc)
        logger.error("chat turn: unrecoverable failure", extra={"turn_id": str(turn_id)}, exc_info=True)
    finally:
        rt = get_registry().get(turn_id)
        subscribers = rt.subscribers if rt else 0
        get_registry().remove(turn_id)
        elapsed = time.monotonic() - t0
        try:
            await emitter.emit(TERMINAL_EVENT, {
                "turn_id": str(turn_id), "status": status, "error": error_text,
                "assistant_message_id": str(assistant_message_id) if assistant_message_id else None,
                "elapsed_ms": round(elapsed * 1000),
            })
        except Exception:  # noqa: BLE001
            logger.warning("chat turn: could not write terminal event", extra={"turn_id": str(turn_id)})
        await _mark_finished(turn_id, status, error_text, assistant_message_id, emitter, use_test_db)
        logger.info(
            "chat turn finished",
            extra={"turn_id": str(turn_id), "status": status, "elapsed_ms": round(elapsed * 1000), "subscribers": subscribers, "events": emitter.count},
        )
        try:
            from app.services.turn_notifications import maybe_notify

            await maybe_notify(
                turn_id=turn_id, status=status, principal=principal, conversation_id=conversation_id,
                conversation_title=conversation_title, answer_text="".join(full_content) or (fast_ack_content or ""),
                error_text=error_text, elapsed_s=elapsed, subscribers=subscribers, use_test_db=use_test_db,
            )
        except Exception:  # noqa: BLE001
            logger.warning("chat turn: notification failed", extra={"turn_id": str(turn_id)}, exc_info=True)


async def _persist_result(
    *, session: AsyncSession, conversation: Conversation, turn_id: uuid.UUID, status: str, error_text: Optional[str],
    full_content: List[str], fast_ack_content: Optional[str], thoughts: List[Dict[str, Any]], run_events: List[Dict[str, Any]],
    citations_by_file: Dict[str, Any], available_agents: List[str], selected_agent_id: Optional[str], payload: Any,
    principal: Principal, elapsed_s: float, emitter: _Emitter,
) -> Optional[uuid.UUID]:
    """Commit the assistant message (complete, partial, or failure notice) and
    the run record. Runs in a fresh transaction so a failure mid-turn cannot
    take the user's message with it."""
    text = "".join(full_content) if full_content else (fast_ack_content or "")
    if status == COMPLETED:
        response_text = text or "No response generated."
    elif status == CANCELLED:
        response_text = (text + "\n\n*[Response stopped]*").strip()
    elif status == INTERRUPTED:
        response_text = (text + f"\n\n*[Response interrupted by a server restart after {int(elapsed_s // 60)} min — ask again to rerun]*").strip()
    else:
        response_text = (text + f"\n\n**Error:** {error_text or 'the response could not be completed'}").strip()

    collected_citations = sorted(citations_by_file.values(), key=lambda c: -c["score"])
    routing_payload: Dict[str, Any] = {"citations": collected_citations, "turn_id": str(turn_id), "turn_status": status}
    if thoughts or available_agents:
        routing_payload["thoughts"] = thoughts
        routing_payload["selected_agents"] = available_agents

    try:
        await session.rollback()  # discard anything a failed dispatcher left half-done
        assistant_message = Message(
            conversation_id=conversation.id, role="assistant", content=response_text, routing_decision=routing_payload,
        )
        session.add(assistant_message)
        conversation.updated_at = _now()
        session.add(conversation)
        try:
            from app.models.domain import AgentDefinition, RunRecord

            agent_uuid = None
            if selected_agent_id:
                try:
                    agent_uuid = uuid.UUID(selected_agent_id)
                except (ValueError, TypeError):
                    row = (await session.execute(select(AgentDefinition).where(AgentDefinition.name == selected_agent_id))).scalar_one_or_none()
                    agent_uuid = row.id if row else None
            if not agent_uuid:
                row = (await session.execute(select(AgentDefinition).where(AgentDefinition.name == "chat"))).scalar_one_or_none()
                agent_uuid = row.id if row else None
            if agent_uuid:
                session.add(RunRecord(
                    agent_id=agent_uuid,
                    status="completed" if status == COMPLETED else status,
                    input={"prompt": payload.message, "source": "chat", "conversation_id": str(conversation.id), "turn_id": str(turn_id)},
                    output={"response": response_text[:2000]},
                    events=run_events[-50:],
                    created_by=principal.sub,
                ))
        except Exception as run_err:  # noqa: BLE001
            logger.warning("chat turn: RunRecord skipped: %s", run_err)
        await session.commit()
        await session.refresh(assistant_message)
        await emitter.emit("message_complete", {
            "message_id": str(assistant_message.id), "conversation_id": str(conversation.id),
            "citations": collected_citations, "turn_id": str(turn_id), "status": status,
        })
        return assistant_message.id
    except Exception:  # noqa: BLE001
        logger.error("chat turn: could not persist assistant message", extra={"turn_id": str(turn_id)}, exc_info=True)
        return None


async def _after_success(session, conversation, history_messages, user_message, assistant_message_id, payload, principal, emitter, selected_agent_id, full_content, use_test_db: bool = False) -> None:
    """Insights follow-up question and online-eval sampling — exactly what the
    inline handler did after commit, all best-effort."""
    from app.api.chat import _generate_insights_and_pending_question
    from app.services.insights_generator import should_generate_insights
    from app.services.platform_config import get_platform_insights_enabled

    assistant_message = (await session.execute(select(Message).where(Message.id == assistant_message_id))).scalar_one_or_none() if assistant_message_id else None
    if assistant_message is None:
        return
    try:
        from sqlalchemy import func

        count = (await session.execute(select(func.count()).select_from(Message).where(Message.conversation_id == conversation.id))).scalar_one()
        if get_platform_insights_enabled() and should_generate_insights(conversation, count):
            question = await _generate_insights_and_pending_question(
                conversation=conversation, messages=history_messages + [user_message, assistant_message],
                user_id=principal.sub, user_token=principal.token,
            )
            if question:
                await emitter.emit("interim", {
                    "type": "interim", "source": "insights", "message": question,
                    "data": {"kind": "profile_follow_up", "bridge_channels": (payload.metadata or {}).get("bridge_channels", [])},
                })
    except Exception as exc:  # noqa: BLE001
        logger.error("chat turn: insights failed: %s", exc, exc_info=True)
    # Personal memory: curate in the background under the user's own
    # principal. The turn does not wait, and the curator sees only this
    # exchange (services/memory_curator.py).
    try:
        from app.services.memory_curator import schedule_curation

        if not _is_bridge_request_metadata(payload.metadata):
            schedule_curation(principal, payload.message, "".join(full_content), use_test_db=use_test_db)
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory curation not scheduled: %s", exc)
    try:
        from app.services.eval_runner import sample_online_eval

        asyncio.ensure_future(sample_online_eval(
            session=session, conversation_id=conversation.id, message_id=assistant_message.id,
            query=payload.message, response="".join(full_content), agent_id=selected_agent_id, user_id=principal.sub,
        ))
    except Exception as exc:  # noqa: BLE001
        logger.debug("online eval hook skipped: %s", exc)


async def _mark_finished(turn_id: uuid.UUID, status: str, error_text: Optional[str], assistant_message_id: Optional[uuid.UUID], emitter: _Emitter, use_test_db: bool) -> None:
    try:
        async with get_session_context(use_test_db) as session:
            turn = (await session.execute(select(ChatTurn).where(ChatTurn.id == turn_id))).scalar_one_or_none()
            if turn is None:
                return
            turn.status = status
            turn.error = (error_text or "")[:4000] or None
            turn.assistant_message_id = assistant_message_id
            turn.event_count = emitter.count
            turn.last_event_id = emitter.last_id
            turn.finished_at = _now()
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.error("chat turn: could not mark finished", extra={"turn_id": str(turn_id)}, exc_info=True)


# ---------------------------------------------------------------------------
# Subscribe / stop / lookup / shutdown
# ---------------------------------------------------------------------------


async def subscribe(turn_id: uuid.UUID, after: Optional[str] = None) -> AsyncGenerator[str, None]:
    """SSE frames for a turn: backlog after ``after``, then live until the
    terminal event. Attached-subscriber counting feeds the email policy."""
    log = await get_event_log()
    rt = get_registry().get(turn_id)
    if rt:
        rt.subscribers += 1
    stop = asyncio.Event()
    try:
        async for ev in log.follow(str(turn_id), after, stop):
            yield ev.sse()
    finally:
        stop.set()
        rt = get_registry().get(turn_id)
        if rt:
            rt.subscribers = max(0, rt.subscribers - 1)


async def stop_turn(turn_id: uuid.UUID) -> bool:
    """Cooperative stop, then a hard cancel after the grace period."""
    rt = get_registry().get(turn_id)
    if rt is None:
        return False
    rt.stop_requested = True
    rt.cancel.set()
    grace = float(get_settings().chat_turn_stop_grace_seconds)

    async def _hard_cancel():
        try:
            await asyncio.wait_for(asyncio.shield(rt.task), timeout=grace)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if not rt.task.done():
                rt.task.cancel()
        except Exception:  # noqa: BLE001
            pass

    asyncio.create_task(_hard_cancel())
    return True


async def get_turn(turn_id: uuid.UUID, use_test_db: bool = False) -> Optional[ChatTurn]:
    async with get_session_context(use_test_db) as session:
        return (await session.execute(select(ChatTurn).where(ChatTurn.id == turn_id))).scalar_one_or_none()


async def active_turn_for_conversation(conversation_id: uuid.UUID, user_id: str, use_test_db: bool = False) -> Optional[ChatTurn]:
    async with get_session_context(use_test_db) as session:
        return (await session.execute(
            select(ChatTurn)
            .where(ChatTurn.conversation_id == conversation_id, ChatTurn.user_id == user_id, ChatTurn.status == RUNNING)
            .order_by(desc(ChatTurn.started_at))
            .limit(1)
        )).scalar_one_or_none()


async def shutdown(timeout: float = 10.0) -> None:
    """On process shutdown, interrupt running turns so they are recorded (and
    the user emailed) instead of vanishing with the process."""
    running = get_registry().all()
    if not running:
        return
    logger.warning("chat turns: interrupting %d running turn(s) for shutdown", len(running))
    for rt in running:
        rt.task.cancel()
    await asyncio.wait([rt.task for rt in running], timeout=timeout)


async def sweep_orphans(use_test_db: bool = False) -> int:
    """At startup, any ``running`` row belongs to a previous process that died
    without a clean shutdown (OOM, kill -9). Mark them interrupted."""
    try:
        async with get_session_context(use_test_db) as session:
            rows = (await session.execute(select(ChatTurn).where(ChatTurn.status == RUNNING))).scalars().all()
            for turn in rows:
                turn.status = INTERRUPTED
                turn.error = "agent-api restarted while this response was being written"
                turn.finished_at = _now()
            await session.commit()
            if rows:
                logger.warning("chat turns: %d orphaned running turn(s) marked interrupted", len(rows))
            return len(rows)
    except Exception:  # noqa: BLE001
        logger.warning("chat turns: orphan sweep failed", exc_info=True)
        return 0


async def user_notify_preference(user_id: str, use_test_db: bool = False) -> bool:
    async with get_session_context(use_test_db) as session:
        row = (await session.execute(select(ChatSettings).where(ChatSettings.user_id == user_id))).scalar_one_or_none()
        return True if row is None else bool(getattr(row, "notify_email_on_completion", True))
