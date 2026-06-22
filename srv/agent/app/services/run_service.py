"""
Run service for executing agent runs with token exchange and persistence.

Provides:
- Agent execution with tiered timeout limits
- Token exchange and caching for downstream services
- Event tracking for run lifecycle
- Error handling and recovery
- OpenTelemetry tracing integration
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from opentelemetry import trace
from pydantic_ai import Agent
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from app.agents.core import BusiboxDeps
from app.agents.base_agent import BaseStreamingAgent, TOOL_SCOPES
from app.auth.tokens import _audience_for_purpose
from app.clients.busibox import BusiboxClient
from app.models.domain import AgentDefinition, RunRecord
from app.schemas.auth import Principal
from app.services.agent_registry import agent_registry
from app.services.token_service import get_or_exchange_token
from app.services.version_isolation import capture_definition_snapshot
from app.services.run_provenance import build_run_provenance
from app.services.load_monitor import get_load_monitor

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Tiered execution limits (timeout in seconds, memory in MB)
AGENT_LIMITS = {
    "simple": {"timeout": 120, "memory_mb": 512},  # 2 minutes for quick tasks
    "complex": {"timeout": 600, "memory_mb": 2048},  # 10 minutes, 2GB
    "batch": {"timeout": 1800, "memory_mb": 4096},  # 30 minutes, 4GB
}


def get_agent_timeout(agent_tier: str = "simple") -> int:
    """Get timeout for agent tier (Simple: 30s, Complex: 5min, Batch: 30min)."""
    return AGENT_LIMITS.get(agent_tier, AGENT_LIMITS["simple"])["timeout"]


def get_agent_memory_limit(agent_tier: str = "simple") -> int:
    """Get memory limit for agent tier in MB."""
    return AGENT_LIMITS.get(agent_tier, AGENT_LIMITS["simple"])["memory_mb"]


def add_run_event(
    run_record: RunRecord,
    event_type: str,
    data: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """
    Add an event to the run record's event log.
    
    Args:
        run_record: Run record to update
        event_type: Event type (started, tool_call, completion, error, timeout)
        data: Optional event data
        error: Optional error message
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
    }
    
    if data:
        event["data"] = data
    
    if error:
        event["error"] = error
    
    if not isinstance(run_record.events, list):
        run_record.events = []
    
    run_record.events.append(event)
    # Mark the events field as modified so SQLAlchemy persists the change
    attributes.flag_modified(run_record, "events")


async def get_run_by_id(session: AsyncSession, run_id: uuid.UUID) -> Optional[RunRecord]:
    """
    Retrieve a run record by ID.
    
    Args:
        session: Database session
        run_id: Run UUID
        
    Returns:
        RunRecord if found, None otherwise
    """
    stmt = select(RunRecord).where(RunRecord.id == run_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def list_runs(
    session: AsyncSession,
    agent_id: Optional[uuid.UUID] = None,
    created_by: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[RunRecord]:
    """
    List run records with optional filtering.
    
    Args:
        session: Database session
        agent_id: Filter by agent ID
        created_by: Filter by user subject
        status: Filter by status
        limit: Maximum number of results
        offset: Pagination offset
        
    Returns:
        List of RunRecord objects
    """
    stmt = select(RunRecord).order_by(RunRecord.created_at.desc())
    
    if agent_id:
        stmt = stmt.where(RunRecord.agent_id == agent_id)
    
    if created_by:
        stmt = stmt.where(RunRecord.created_by == created_by)
    
    if status:
        stmt = stmt.where(RunRecord.status == status)
    
    stmt = stmt.limit(limit).offset(offset)
    
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def create_run(
    session: AsyncSession,
    principal: Principal,
    agent_id: uuid.UUID,
    payload: Dict[str, Any],
    scopes: list[str],
    purpose: str,
    agent_tier: str = "simple",
    existing_run_id: Optional[uuid.UUID] = None,
) -> RunRecord:
    """
    Execute an agent run with error handling and tiered timeouts.
    
    Args:
        session: Database session
        principal: Authenticated user principal
        agent_id: Agent UUID to execute
        payload: Input payload with 'prompt' and other fields
        scopes: OAuth scopes for token exchange
        purpose: Token purpose for exchange
        agent_tier: Execution tier (simple/complex/batch) for timeout limits
    
    Returns:
        RunRecord with execution results
        
    Raises:
        ValueError: If agent_tier is invalid or payload is malformed
    """
    # Validate inputs
    if agent_tier not in AGENT_LIMITS:
        raise ValueError(f"Invalid agent_tier: {agent_tier}. Must be one of: {list(AGENT_LIMITS.keys())}")
    
    if not payload.get("prompt"):
        raise ValueError("Payload must contain 'prompt' field")
    
    # Capture definition snapshot for version isolation
    try:
        definition_snapshot = await capture_definition_snapshot(
            agent_id=agent_id,
            workflow_id=None,  # TODO: Add workflow_id parameter when workflow support added
            session=session
        )
    except ValueError as e:
        logger.error(f"Failed to capture definition snapshot: {e}")
        definition_snapshot = None
    
    # Create (or reuse) a run record
    if existing_run_id is not None:
        # Background mode: update the pre-created "pending" record rather than
        # creating a duplicate.
        stmt = select(RunRecord).where(RunRecord.id == existing_run_id)
        result = await session.execute(stmt)
        run_record = result.scalar_one_or_none()
        if run_record is None:
            logger.warning(
                f"[create_run] existing_run_id={existing_run_id} not found; creating fresh record."
            )
            existing_run_id = None  # fall through to creation

    if existing_run_id is None:
        run_record = RunRecord(
            agent_id=agent_id,
            status="pending",
            input=payload,
            created_by=principal.sub,
            definition_snapshot=definition_snapshot,
            events=[],
        )
        add_run_event(run_record, "created", data={"agent_tier": agent_tier})
        session.add(run_record)
        await session.commit()
        await session.refresh(run_record)
    
    # Start tracing span for run execution
    with tracer.start_as_current_span(
        "agent_run",
        attributes={
            "run.id": str(run_record.id),
            "agent.id": str(agent_id),
            "agent.tier": agent_tier,
            "user.sub": principal.sub,
        },
    ) as span:
        try:
            # Get token for downstream services.
            # When scopes are explicitly provided, use them. Otherwise, infer
            # required scopes from the agent's tool definitions so that
            # PydanticAI Agents (not just BaseStreamingAgent) get real tokens.
            effective_scopes = list(scopes)
            if not effective_scopes:
                try:
                    stmt = select(AgentDefinition.tools).where(AgentDefinition.id == agent_id)
                    row = await session.execute(stmt)
                    tools_config = row.scalar()
                    if tools_config and isinstance(tools_config, dict):
                        tool_names = tools_config.get("names", [])
                        for tn in tool_names:
                            effective_scopes.extend(TOOL_SCOPES.get(tn, []))
                        effective_scopes = list(set(effective_scopes))
                        if effective_scopes:
                            logger.info(
                                f"Inferred scopes from agent tools for run {run_record.id}: {effective_scopes}"
                            )
                except Exception as e:
                    logger.warning(f"Could not infer tool scopes for agent {agent_id}: {e}")

            if effective_scopes:
                logger.info(f"Exchanging token for run {run_record.id} (scopes={effective_scopes}, purpose={purpose})")
                add_run_event(run_record, "token_exchange_started")

                # Exchange per-audience so the BusiboxClient can route tokens
                audience_scopes: Dict[str, List[str]] = {}
                for scope in effective_scopes:
                    scope_purpose = scope.split(".")[0]
                    aud = _audience_for_purpose(scope_purpose, [scope])
                    audience_scopes.setdefault(aud, []).append(scope)

                tokens_by_audience: Dict[str, str] = {}
                first_token = None
                for aud, aud_scope_list in audience_scopes.items():
                    exchanged = await get_or_exchange_token(
                        session, principal, scopes=aud_scope_list, purpose=aud_scope_list[0].split(".")[0]
                    )
                    tokens_by_audience[aud] = exchanged.access_token
                    if first_token is None:
                        first_token = exchanged.access_token

                client = BusiboxClient(
                    access_token=first_token or "no-token",
                    tokens_by_audience=tokens_by_audience,
                )
                add_run_event(run_record, "token_exchange_completed", data={"audiences": list(tokens_by_audience.keys())})
            else:
                # No scopes needed - agent doesn't use downstream services
                logger.info(f"Skipping token exchange for run {run_record.id} (no scopes required)")
                client = BusiboxClient("dummy-token-not-used")
                add_run_event(run_record, "token_exchange_skipped")
            
            # Get agent from registry (with on-demand loading)
            try:
                agent = await agent_registry.get_or_load(agent_id, session)
                add_run_event(run_record, "agent_loaded", data={"agent_id": str(agent_id)})
                
                # Debug logging for agent type
                agent_type = type(agent).__name__
                agent_class = agent.__class__.__mro__
                logger.info(
                    f"Agent loaded: id={agent_id}, type={agent_type}, "
                    f"is_BaseStreamingAgent={isinstance(agent, BaseStreamingAgent)}, "
                    f"class_hierarchy={[c.__name__ for c in agent_class[:3]]}"
                )
            except (KeyError, ValueError) as e:
                logger.error(f"Agent {agent_id} not found or inactive: {e}")
                run_record.status = "failed"
                run_record.output = {"error": f"Agent error: {str(e)}"}
                add_run_event(run_record, "error", error=str(e))
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Agent not found or inactive"))
                await session.commit()
                await session.refresh(run_record)
                return run_record
            
            # Execute agent with timeout
            timeout = get_agent_timeout(agent_tier)
            memory_limit = get_agent_memory_limit(agent_tier)
            
            logger.info(
                f"Executing agent {agent_id} with {timeout}s timeout, "
                f"{memory_limit}MB memory limit (tier: {agent_tier})"
            )
            
            run_record.status = "running"
            add_run_event(
                run_record,
                "execution_started",
                data={"timeout": timeout, "memory_limit_mb": memory_limit},
            )
            await session.commit()
            
            span.set_attribute("run.timeout", timeout)
            span.set_attribute("run.memory_limit_mb", memory_limit)
            
            try:
                # Handle both PydanticAI Agent and BaseStreamingAgent
                prompt = payload.get("prompt", "")
                
                if isinstance(agent, BaseStreamingAgent):
                    # BaseStreamingAgent uses context dict.
                    # IMPORTANT: task/run identifiers go in both the top-level keys
                    # AND the "metadata" sub-dict so that base_agent.py populates
                    # BusiboxDeps.metadata correctly (it reads context.get("metadata")).
                    # The metadata is also injected into the system prompt as
                    # "## Application Context" so the agent can reference task_id directly.
                    #
                    # System keys (starting with "_") and "prompt" are excluded.
                    # All other payload keys (e.g. mode, documentIdSites, or any
                    # input_override fields) are forwarded so the agent can read them.
                    _SYSTEM_KEYS = {"prompt", "response_schema", "max_tokens"}
                    _task_meta = {
                        "task_id": payload.get("_task_id"),
                        "execution_id": payload.get("_execution_id"),
                        "continuation_depth": payload.get("_continuation_depth", 0),
                        "run_id": str(run_record.id),
                        **{
                            k: v for k, v in payload.items()
                            if not k.startswith("_") and k not in _SYSTEM_KEYS
                        },
                    }
                    context = {
                        "principal": principal,
                        "session": session,
                        "user_id": principal.sub,
                        "agent_id": str(agent_id),
                        "run_id": str(run_record.id),
                        "task_id": payload.get("_task_id"),
                        "execution_id": payload.get("_execution_id"),
                        "continuation_depth": payload.get("_continuation_depth", 0),
                        "metadata": _task_meta,
                        "response_schema": payload.get("response_schema"),
                        "max_tokens": payload.get("max_tokens"),
                    }
                    logger.info(
                        f"Calling BaseStreamingAgent.run() with context: "
                        f"principal={principal.sub}, has_token={principal.token is not None}, "
                        f"prompt_length={len(prompt)}"
                    )
                    result = await asyncio.wait_for(
                        agent.run(prompt, context=context), timeout=timeout
                    )
                    logger.info(
                        f"BaseStreamingAgent.run() completed: "
                        f"result_type={type(result).__name__}, "
                        f"has_data={hasattr(result, 'data')}, "
                        f"has_output={hasattr(result, 'output')}"
                    )
                else:
                    # PydanticAI Agent uses deps
                    deps = BusiboxDeps(
                        principal=principal,
                        busibox_client=client,
                        metadata={
                            "run_id": str(run_record.id),
                            "task_id": payload.get("_task_id"),
                            "execution_id": payload.get("_execution_id"),
                            "continuation_depth": payload.get("_continuation_depth", 0),
                        },
                    )
                    logger.info(f"Calling PydanticAI Agent.run() with deps")
                    result = await asyncio.wait_for(
                        agent.run(prompt, deps=deps), timeout=timeout
                    )
                
                # Success - extract output
                run_record.status = "succeeded"
                
                # Extract output based on result type
                # Both PydanticAI and BaseStreamingAgent return objects with .data or .output
                if hasattr(result, "data"):
                    output_data = result.data
                    if hasattr(output_data, "model_dump"):
                        run_record.output = output_data.model_dump()
                    elif hasattr(output_data, "dict"):
                        run_record.output = output_data.dict()
                    elif isinstance(output_data, str):
                        run_record.output = {"result": output_data}
                    else:
                        run_record.output = {"data": output_data}
                elif hasattr(result, "output"):
                    run_record.output = {"result": result.output}
                elif isinstance(result, str):
                    run_record.output = {"result": result}
                else:
                    run_record.output = {"result": str(result)}
                
                add_run_event(run_record, "execution_completed", data={"status": "succeeded"})
                
                # Build provenance chain for the completed run
                try:
                    tool_calls = [
                        e.get("data", {})
                        for e in (run_record.events or [])
                        if e.get("type") == "tool_call"
                    ]
                    raw_model = getattr(agent, 'model', None) if agent else None
                    model_str = str(raw_model) if raw_model is not None and not isinstance(raw_model, str) else raw_model
                    provenance_event = build_run_provenance(
                        run_input=payload,
                        tool_calls=tool_calls,
                        run_output=run_record.output,
                        agent_id=str(agent_id),
                        model_version=model_str,
                    )
                    run_record.events.append(provenance_event)
                    attributes.flag_modified(run_record, "events")
                except Exception as prov_err:
                    logger.warning(f"Failed to build run provenance (non-fatal): {prov_err}")

                logger.info(f"Agent {agent_id} run {run_record.id} succeeded")
                span.set_status(trace.Status(trace.StatusCode.OK))
                
            except asyncio.TimeoutError:
                logger.warning(f"Agent {agent_id} run {run_record.id} timed out after {timeout}s")
                run_record.status = "timeout"
                run_record.output = {
                    "error": f"Execution exceeded {timeout}s timeout limit for {agent_tier} tier",
                    "timeout": timeout,
                    "tier": agent_tier,
                }
                add_run_event(
                    run_record,
                    "timeout",
                    error=f"Exceeded {timeout}s timeout",
                    data={"timeout": timeout, "tier": agent_tier},
                )
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Timeout"))
            
            except Exception as e:
                # Tool call failure or agent execution error
                logger.error(f"Agent {agent_id} run {run_record.id} failed: {e}", exc_info=True)
                run_record.status = "failed"
                run_record.output = {
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
                add_run_event(
                    run_record,
                    "execution_failed",
                    error=str(e),
                    data={"error_type": type(e).__name__},
                )
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
        
        except Exception as e:
            # Token exchange or setup failure
            logger.error(f"Run {run_record.id} setup failed: {e}", exc_info=True)
            run_record.status = "failed"
            run_record.output = {
                "error": f"Setup failed: {str(e)}",
                "error_type": type(e).__name__,
            }
            add_run_event(
                run_record, "setup_failed", error=str(e), data={"error_type": type(e).__name__}
            )
            span.set_status(trace.Status(trace.StatusCode.ERROR, f"Setup failed: {str(e)}"))
        
        # Persist final state
        await session.commit()
        await session.refresh(run_record)
        
        logger.info(
            f"Run {run_record.id} completed with status: {run_record.status}",
            extra={
                "run_id": str(run_record.id),
                "agent_id": str(agent_id),
                "status": run_record.status,
                "created_by": principal.sub,
            },
        )
        
        return run_record


async def create_run_background(
    principal: Principal,
    agent_id: uuid.UUID,
    payload: Dict[str, Any],
    scopes: list[str],
    purpose: str,
    agent_tier: str = "complex",
    on_complete: Optional[Callable] = None,
) -> RunRecord:
    """
    Create a run record immediately (in "pending" state) and execute the agent
    asynchronously in the background.

    Returns the pre-created RunRecord so callers can return `run_id` to the
    client immediately, allowing the UI to open the activity sidebar before the
    agent finishes.

    The background task will update the RunRecord to "running" then
    "succeeded"/"failed" when complete.

    Args:
        principal: Authenticated user
        agent_id: Agent to execute
        payload: Input payload with 'prompt'
        scopes: OAuth scopes for token exchange
        purpose: Token purpose
        agent_tier: Execution tier (simple/complex/batch)
        on_complete: Optional async callback(run_id, status, output_summary) called after run finishes

    Returns:
        Pre-created RunRecord (status="pending")
    """
    from app.db.session import SessionLocal

    if agent_tier not in AGENT_LIMITS:
        raise ValueError(f"Invalid agent_tier: {agent_tier}. Must be one of: {list(AGENT_LIMITS.keys())}")
    if not payload.get("prompt"):
        raise ValueError("Payload must contain 'prompt' field")

    async with SessionLocal() as session:
        # Capture definition snapshot
        try:
            definition_snapshot = await capture_definition_snapshot(
                agent_id=agent_id,
                workflow_id=None,
                session=session,
            )
        except ValueError:
            definition_snapshot = None

        # Pre-create the run record in pending state
        run_record = RunRecord(
            agent_id=agent_id,
            status="pending",
            input=payload,
            created_by=principal.sub,
            definition_snapshot=definition_snapshot,
            events=[],
        )
        add_run_event(run_record, "created", data={"agent_tier": agent_tier, "mode": "background"})
        session.add(run_record)
        await session.commit()
        await session.refresh(run_record)

    run_id = run_record.id

    async def _bg_execute():
        final_status = "failed"
        output_summary = None
        try:
            async with SessionLocal() as bg_session:
                result = await create_run(
                    session=bg_session,
                    principal=principal,
                    agent_id=agent_id,
                    payload=payload,
                    scopes=scopes,
                    purpose=purpose,
                    agent_tier=agent_tier,
                    existing_run_id=run_id,
                )
                final_status = getattr(result, "status", "succeeded") or "succeeded"
                output_summary = str(getattr(result, "output", ""))[:500] if getattr(result, "output", None) else None
        except Exception as exc:
            logger.exception(
                f"[bg_execute] Background run {run_id} failed with exception: {exc}"
            )
            output_summary = str(exc)[:500]
            try:
                async with SessionLocal() as err_session:
                    await err_session.execute(
                        update(RunRecord)
                        .where(RunRecord.id == run_id)
                        .values(status="failed", output={"error": str(exc)})
                    )
                    await err_session.commit()
            except Exception:
                pass
        finally:
            if on_complete:
                # #region agent log
                logger.info("DEBUG[fce93e] _bg_execute finally: calling on_complete for run=%s status=%s", run_id, final_status)
                # #endregion
                try:
                    await on_complete(run_id, final_status, output_summary)
                except Exception as cb_err:
                    logger.warning(f"[bg_execute] on_complete callback failed: {cb_err}")

    asyncio.create_task(_bg_execute())

    return run_record
