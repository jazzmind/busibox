"""Lifecycle tests for detached chat turns (services/chat_turns.py).

These run the real ``start_turn`` / ``_run_turn`` / ``subscribe`` /
``stop_turn`` code against the test database, with only the agent itself
(``run_agentic_dispatcher``) replaced by a scripted generator and the email
transport replaced by a recorder. Run with::

    make test-docker SERVICE=agent ARGS="tests/unit/test_chat_turns.py"

What they pin down, in the order a laptop-sleep incident plays out:

1. the user message and the ``chat_turns`` row are committed before the
   agent produces anything;
2. a subscriber going away does not cancel the turn;
3. the answer is committed and the turn marked ``completed`` with nobody
   attached, and the completion-email policy is consulted with
   ``subscribers=0``;
4. a late subscriber replays the whole event log and sees the terminal event;
5. Stop keeps the partial answer (cooperative and hard-cancel paths);
6. a dispatcher exception becomes ``failed`` with an error marker;
7. per-user and per-conversation limits refuse a new turn before anything is
   written.
"""

from __future__ import annotations

import asyncio
import types
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.chat import ChatMessageRequest
from app.models.domain import ChatTurn, Conversation, Message
from app.schemas.auth import Principal
from app.schemas.streaming import StreamEvent
from app.services import chat_turns as ct
from app.services.turn_events import TERMINAL_EVENT, MemoryEventLog, set_event_log


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class TurnEnv:
    def __init__(self, factory, log: MemoryEventLog, settings: types.SimpleNamespace):
        self.factory = factory
        self.log = log
        self.settings = settings
        self.notifications: List[Dict[str, Any]] = []
        self.conversation_ids: List[uuid.UUID] = []

    @asynccontextmanager
    async def session(self):
        async with self.factory() as s:
            yield s

    async def turn_row(self, turn_id: uuid.UUID) -> Optional[ChatTurn]:
        async with self.session() as s:
            return (await s.execute(select(ChatTurn).where(ChatTurn.id == turn_id))).scalar_one_or_none()

    async def messages(self, conversation_id: uuid.UUID) -> List[Message]:
        async with self.session() as s:
            rows = (await s.execute(
                select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())
            )).scalars().all()
            return list(rows)

    async def events(self, turn_id: uuid.UUID):
        return await self.log.replay(str(turn_id))

    async def wait_finished(self, turn_id: uuid.UUID, timeout: float = 10.0) -> None:
        async def _wait():
            while not await self.log.finished(str(turn_id)):
                await asyncio.sleep(0.02)

        await asyncio.wait_for(_wait(), timeout=timeout)

    async def cleanup(self) -> None:
        async with self.session() as s:
            for cid in self.conversation_ids:
                await s.execute(delete(ChatTurn).where(ChatTurn.conversation_id == cid))
                await s.execute(delete(Message).where(Message.conversation_id == cid))
                await s.execute(delete(Conversation).where(Conversation.id == cid))
            await s.commit()


@pytest.fixture
async def env(session_engine, monkeypatch) -> TurnEnv:
    factory = async_sessionmaker(session_engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _ctx(use_test_db: bool = False):
        async with factory() as session:
            try:
                yield session
            finally:
                await session.close()

    import app.db.session as dbs
    import app.services.eval_runner as eval_runner
    import app.services.platform_config as platform_config
    import app.services.turn_notifications as tn

    monkeypatch.setattr(ct, "get_session_context", _ctx)
    monkeypatch.setattr(dbs, "get_session_context", _ctx)
    monkeypatch.setattr(platform_config, "get_platform_insights_enabled", lambda: False)
    monkeypatch.setattr(eval_runner, "sample_online_eval", AsyncMock(return_value=None))

    settings = types.SimpleNamespace(
        chat_max_running_turns_per_user=3,
        chat_turn_stop_grace_seconds=0.3,
        chat_notify_email_enabled=True,
        chat_notify_min_seconds=120,
        portal_base_url="https://busibox.example.com",
    )
    monkeypatch.setattr(ct, "get_settings", lambda: settings)

    log = MemoryEventLog()
    set_event_log(log)
    ct.get_registry()._turns.clear()

    env = TurnEnv(factory, log, settings)

    async def _record_notify(**kwargs):
        env.notifications.append(kwargs)
        return False

    monkeypatch.setattr(tn, "maybe_notify", _record_notify)

    try:
        yield env
    finally:
        # Never leave a scripted turn running into the next test.
        for rt in ct.get_registry().all():
            rt.task.cancel()
        await asyncio.sleep(0)
        ct.get_registry()._turns.clear()
        set_event_log(None)
        await env.cleanup()


def _principal() -> Principal:
    return Principal(sub=f"turn-test-{uuid.uuid4().hex[:8]}", email="turn-test@example.com", roles=["User"], scopes=["read"])


def _content(text: str) -> StreamEvent:
    return StreamEvent(type="content", source="chat", message=text)


def _thought(text: str) -> StreamEvent:
    return StreamEvent(type="thought", source="dispatcher", message=text, data={"phase": "intent_routing", "action_type": "answer"})


def _script_dispatcher(monkeypatch, script: List[Any], *, started: Optional[asyncio.Event] = None):
    """Install a fake ``run_agentic_dispatcher`` that plays ``script``.

    Items: a ``StreamEvent`` (yielded), an ``Exception`` (raised), ``"wait_cancel"``
    (spin until the turn's cancel event is set, then yield one more chunk),
    ``"hang"`` (ignore cancel — forces the hard-cancel path), or a coroutine
    function called with the dispatcher kwargs (for mid-run assertions).
    """
    import app.services.agentic_dispatcher as dispatcher_mod

    async def run_agentic_dispatcher(**kwargs):
        if started is not None:
            started.set()
        for item in script:
            if isinstance(item, StreamEvent):
                yield item
            elif isinstance(item, Exception):
                raise item
            elif item == "wait_cancel":
                cancel: asyncio.Event = kwargs["cancel"]
                await asyncio.wait_for(cancel.wait(), timeout=10)
                yield _content(" (late chunk)")
                return
            elif item == "hang":
                await asyncio.sleep(3600)  # ignores cancel; only task.cancel() ends it
            elif callable(item):
                await item(kwargs)
            else:  # pragma: no cover - script typo
                raise AssertionError(f"unknown script item {item!r}")

    monkeypatch.setattr(dispatcher_mod, "run_agentic_dispatcher", run_agentic_dispatcher)


async def _start(env: TurnEnv, principal: Principal, message: str = "What is the tide schedule?", **payload_kwargs) -> ChatTurn:
    payload = ChatMessageRequest(message=message, **payload_kwargs)
    turn = await ct.start_turn(payload, principal)
    env.conversation_ids.append(turn.conversation_id)
    return turn


async def _drain(turn_id: uuid.UUID, after: Optional[str] = None, timeout: float = 10.0) -> List[str]:
    frames: List[str] = []

    async def _go():
        async for frame in ct.subscribe(turn_id, after):
            frames.append(frame)

    await asyncio.wait_for(_go(), timeout=timeout)
    return frames


# ---------------------------------------------------------------------------
# 1. The request is durable before the agent runs
# ---------------------------------------------------------------------------


async def test_user_message_and_turn_row_are_committed_before_the_agent_starts(env, monkeypatch):
    seen: Dict[str, Any] = {}

    async def _inspect(kwargs):
        # Runs inside the dispatcher, i.e. after start_turn returned. A
        # separate session must already see the committed request.
        async with env.session() as s:
            turn = (await s.execute(select(ChatTurn).where(ChatTurn.id == uuid.UUID(kwargs["metadata"]["turn_id"])))).scalar_one()
            msgs = (await s.execute(select(Message).where(Message.conversation_id == turn.conversation_id))).scalars().all()
        seen["status"] = turn.status
        seen["roles"] = [m.role for m in msgs]
        seen["history"] = kwargs["conversation_history"]

    _script_dispatcher(monkeypatch, [_inspect, _content("42")])
    turn = await _start(env, _principal(), "How deep is the channel?")

    assert turn.status == ct.RUNNING and turn.user_message_id is not None
    await env.wait_finished(turn.id)

    assert seen["status"] == ct.RUNNING
    assert seen["roles"] == ["user"]
    assert seen["history"] == []  # the current question is not fed back as history


async def test_start_turn_emits_turn_started_then_conversation_created(env, monkeypatch):
    _script_dispatcher(monkeypatch, [_content("ok")])
    turn = await _start(env, _principal(), "Hello there")
    await env.wait_finished(turn.id)

    events = await env.events(turn.id)
    assert [e.type for e in events[:2]] == ["turn_started", "conversation_created"]
    assert '"turn_id": "%s"' % turn.id in events[0].data
    assert "Hello there" in events[1].data


# ---------------------------------------------------------------------------
# 2 + 3. Disconnecting does not cancel; the answer lands with nobody attached
# ---------------------------------------------------------------------------


async def test_subscriber_disconnect_does_not_cancel_and_answer_is_persisted(env, monkeypatch):
    gate = asyncio.Event()

    async def _wait_for_gate(_kwargs):
        await asyncio.wait_for(gate.wait(), timeout=10)

    _script_dispatcher(monkeypatch, [_content("Part one. "), _wait_for_gate, _content("Part two."), _thought("routing")])
    principal = _principal()
    turn = await _start(env, principal)

    # A browser attaches, reads the first frames, then vanishes (laptop sleep).
    sub = ct.subscribe(turn.id)
    first = await sub.__anext__()
    assert first.startswith("id: 1\nevent: turn_started\n")
    assert ct.get_registry().get(turn.id).subscribers == 1
    await sub.aclose()
    assert ct.get_registry().get(turn.id).subscribers == 0

    # The turn is still running on the server.
    assert ct.get_registry().get(turn.id) is not None
    assert not ct.get_registry().get(turn.id).task.done()

    gate.set()
    await env.wait_finished(turn.id)

    row = await env.turn_row(turn.id)
    assert row.status == ct.COMPLETED and row.error is None
    assert row.assistant_message_id is not None and row.finished_at is not None
    assert row.event_count >= 5 and row.last_event_id is not None

    msgs = await env.messages(turn.conversation_id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "Part one. Part two."
    assert msgs[1].routing_decision["turn_id"] == str(turn.id)
    assert msgs[1].routing_decision["turn_status"] == ct.COMPLETED
    assert msgs[1].routing_decision["thoughts"][0]["data"]["phase"] == "intent_routing"

    # Registry is clean and the email policy was consulted with nobody attached.
    assert ct.get_registry().get(turn.id) is None
    assert len(env.notifications) == 1
    note = env.notifications[0]
    assert note["turn_id"] == turn.id and note["status"] == ct.COMPLETED
    assert note["subscribers"] == 0 and note["answer_text"] == "Part one. Part two."
    assert note["principal"].email == "turn-test@example.com"


# ---------------------------------------------------------------------------
# 4. Late subscribers replay from the log
# ---------------------------------------------------------------------------


async def test_late_subscriber_replays_full_log_and_reaches_terminal_event(env, monkeypatch):
    _script_dispatcher(monkeypatch, [_content("alpha "), _content("beta")])
    turn = await _start(env, _principal())
    await env.wait_finished(turn.id)

    frames = await _drain(turn.id)
    types_seen = [line.split("event: ", 1)[1].split("\n", 1)[0] for line in frames]
    assert types_seen[0] == "turn_started"
    assert types_seen.count("content") == 2
    assert "message_complete" in types_seen
    assert types_seen[-1] == TERMINAL_EVENT
    assert '"status": "completed"' in frames[-1]

    # Resuming from a cursor skips what was already seen.
    resumed = await _drain(turn.id, after="2")
    assert len(resumed) == len(frames) - 2
    assert resumed[0].startswith("id: 3\n")


async def test_subscriber_attached_before_events_receives_them_live(env, monkeypatch):
    gate = asyncio.Event()

    async def _wait_for_gate(_kwargs):
        await asyncio.wait_for(gate.wait(), timeout=10)

    _script_dispatcher(monkeypatch, [_wait_for_gate, _content("live")])
    turn = await _start(env, _principal())

    drain_task = asyncio.create_task(_drain(turn.id))
    await asyncio.sleep(0.05)
    gate.set()
    frames = await asyncio.wait_for(drain_task, timeout=10)
    assert any("event: content" in f and "live" in f for f in frames)
    assert frames[-1].split("event: ", 1)[1].startswith(TERMINAL_EVENT)


# ---------------------------------------------------------------------------
# 5. Stop
# ---------------------------------------------------------------------------


async def test_stop_turn_cooperative_keeps_partial_answer(env, monkeypatch):
    started = asyncio.Event()
    _script_dispatcher(monkeypatch, [_content("So far... "), "wait_cancel"], started=started)
    turn = await _start(env, _principal())
    await asyncio.wait_for(started.wait(), timeout=5)
    await asyncio.sleep(0.05)  # let the first chunk land

    assert await ct.stop_turn(turn.id) is True
    await env.wait_finished(turn.id)

    row = await env.turn_row(turn.id)
    assert row.status == ct.CANCELLED
    msgs = await env.messages(turn.conversation_id)
    assert msgs[-1].role == "assistant"
    assert msgs[-1].content.startswith("So far...") and msgs[-1].content.endswith("*[Response stopped]*")
    assert env.notifications[0]["status"] == ct.CANCELLED  # policy decides (and declines) for Stop
    assert await ct.stop_turn(turn.id) is False  # already gone


async def test_stop_turn_hard_cancels_an_unresponsive_agent_after_grace(env, monkeypatch):
    started = asyncio.Event()
    _script_dispatcher(monkeypatch, [_content("Working"), "hang"], started=started)
    turn = await _start(env, _principal())
    await asyncio.wait_for(started.wait(), timeout=5)
    await asyncio.sleep(0.05)

    await ct.stop_turn(turn.id)
    await env.wait_finished(turn.id, timeout=5)  # grace is 0.3 s in this env

    row = await env.turn_row(turn.id)
    assert row.status == ct.CANCELLED
    msgs = await env.messages(turn.conversation_id)
    assert msgs[-1].content == "Working\n\n*[Response stopped]*"
    assert ct.get_registry().get(turn.id) is None


async def test_shutdown_interrupts_running_turns_and_records_them(env, monkeypatch):
    started = asyncio.Event()
    _script_dispatcher(monkeypatch, [_content("Half"), "hang"], started=started)
    turn = await _start(env, _principal())
    await asyncio.wait_for(started.wait(), timeout=5)
    await asyncio.sleep(0.05)

    await ct.shutdown(timeout=5)
    await env.wait_finished(turn.id, timeout=5)

    row = await env.turn_row(turn.id)
    assert row.status == ct.INTERRUPTED
    msgs = await env.messages(turn.conversation_id)
    assert msgs[-1].content.startswith("Half\n\n*[Response interrupted by a server restart")
    assert env.notifications[0]["status"] == ct.INTERRUPTED


# ---------------------------------------------------------------------------
# 6. Failure
# ---------------------------------------------------------------------------


async def test_dispatcher_exception_is_recorded_as_failed_with_error_marker(env, monkeypatch):
    _script_dispatcher(monkeypatch, [_content("Before the crash."), RuntimeError("model timeout")])
    turn = await _start(env, _principal())
    await env.wait_finished(turn.id)

    row = await env.turn_row(turn.id)
    assert row.status == ct.FAILED and row.error == "model timeout"

    msgs = await env.messages(turn.conversation_id)
    assert msgs[-1].content == "Before the crash.\n\n**Error:** model timeout"

    events = await env.events(turn.id)
    kinds = [e.type for e in events]
    assert "error" in kinds and kinds[-1] == TERMINAL_EVENT
    assert '"status": "failed"' in events[-1].data and '"error": "model timeout"' in events[-1].data
    assert env.notifications[0]["status"] == ct.FAILED


async def test_failure_before_any_content_still_stores_a_notice(env, monkeypatch):
    _script_dispatcher(monkeypatch, [ValueError("bad request")])
    turn = await _start(env, _principal())
    await env.wait_finished(turn.id)
    msgs = await env.messages(turn.conversation_id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[-1].content == "**Error:** bad request"


# ---------------------------------------------------------------------------
# 7. Limits and lookups
# ---------------------------------------------------------------------------


async def test_per_user_limit_refuses_before_writing(env, monkeypatch):
    env.settings.chat_max_running_turns_per_user = 1
    started = asyncio.Event()
    _script_dispatcher(monkeypatch, ["wait_cancel"], started=started)
    principal = _principal()
    first = await _start(env, principal)
    await asyncio.wait_for(started.wait(), timeout=5)

    with pytest.raises(ct.TurnLimitError):
        await ct.start_turn(ChatMessageRequest(message="another one"), principal)

    # Nothing was written for the refused request.
    async with env.session() as s:
        convs = (await s.execute(select(Conversation).where(Conversation.user_id == principal.sub))).scalars().all()
    assert [c.id for c in convs] == [first.conversation_id]

    # A different user is unaffected.
    other = await _start(env, _principal())
    assert other.status == ct.RUNNING

    await ct.stop_turn(first.id)
    await ct.stop_turn(other.id)
    await env.wait_finished(first.id)
    await env.wait_finished(other.id)


async def test_per_conversation_lock_and_active_turn_lookup(env, monkeypatch):
    started = asyncio.Event()
    _script_dispatcher(monkeypatch, ["wait_cancel"], started=started)
    principal = _principal()
    turn = await _start(env, principal)
    await asyncio.wait_for(started.wait(), timeout=5)

    with pytest.raises(ct.ConversationBusyError):
        await ct.start_turn(ChatMessageRequest(message="follow-up", conversation_id=turn.conversation_id), principal)

    active = await ct.active_turn_for_conversation(turn.conversation_id, principal.sub)
    assert active is not None and active.id == turn.id
    assert await ct.active_turn_for_conversation(turn.conversation_id, "someone-else") is None

    await ct.stop_turn(turn.id)
    await env.wait_finished(turn.id)
    assert await ct.active_turn_for_conversation(turn.conversation_id, principal.sub) is None

    # A follow-up in the same conversation now starts and sees the history.
    seen: Dict[str, Any] = {}

    async def _inspect(kwargs):
        seen["history"] = kwargs["conversation_history"]

    _script_dispatcher(monkeypatch, [_inspect, _content("second answer")])
    follow = await ct.start_turn(ChatMessageRequest(message="follow-up", conversation_id=turn.conversation_id), principal)
    await env.wait_finished(follow.id)
    assert [h["role"] for h in seen["history"]] == ["user", "assistant"]
    assert (await env.turn_row(follow.id)).status == ct.COMPLETED


async def test_unknown_conversation_raises_lookup_error(env, monkeypatch):
    _script_dispatcher(monkeypatch, [_content("never")])
    with pytest.raises(LookupError):
        await ct.start_turn(ChatMessageRequest(message="hi", conversation_id=uuid.uuid4()), _principal())


async def test_sweep_orphans_marks_stale_running_rows_interrupted(env):
    principal = _principal()
    async with env.session() as s:
        conv = Conversation(title="orphan", user_id=principal.sub)
        s.add(conv)
        await s.flush()
        env.conversation_ids.append(conv.id)
        s.add(ChatTurn(conversation_id=conv.id, user_id=principal.sub, status=ct.RUNNING, query="lost"))
        await s.commit()
        orphan_conv_id = conv.id

    swept = await ct.sweep_orphans()
    assert swept >= 1
    async with env.session() as s:
        rows = (await s.execute(select(ChatTurn).where(ChatTurn.conversation_id == orphan_conv_id))).scalars().all()
    assert rows[0].status == ct.INTERRUPTED and "restarted" in rows[0].error
