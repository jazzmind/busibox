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
from typing import Any, Dict, List, Optional

from opentelemetry import trace
from pydantic_ai import Agent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from app.agents.core import BusiboxDeps
from app.agents.base_agent import BaseStreamingAgent
from app.clients.busibox import BusiboxClient
from app.models.domain import RunRecord
from app.schemas.auth import Principal
from app.services.agent_registry import agent_registry
from app.services.token_service import get_or_exchange_token
from app.services.version_isolation import capture_definition_snapshot

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Tiered execution limits (timeout in seconds, memory in MB)
AGENT_LIMITS = {
    "simple": {"timeout": 30, "memory_mb": 512},
    "complex": {"timeout": 300, "memory_mb": 2048},  # 5 minutes, 2GB
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
    
    # Create run record with initial event and snapshot
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
            # Get token for downstream services
            if scopes:
                # Check if principal already has a token (from API auth)
                if principal.token:
                    # Use the token that was provided in the API request
                    # (already has necessary scopes from frontend token exchange)
                    logger.info(f"Using provided token for run {run_record.id}")
                    client = BusiboxClient(principal.token)
                    add_run_event(run_record, "token_provided")
                else:
                    # No token provided - perform token exchange
                    logger.info(f"Exchanging token for run {run_record.id}")
                    add_run_event(run_record, "token_exchange_started")
                    
                    token = await get_or_exchange_token(session, principal, scopes=scopes, purpose=purpose)
                    client = BusiboxClient(token.access_token)
                    
                    add_run_event(run_record, "token_exchange_completed")
            else:
                # No scopes needed - agent doesn't use downstream services
                # Create a dummy client that won't be used
                logger.info(f"Skipping token exchange for run {run_record.id} (no scopes required)")
                client = BusiboxClient("dummy-token-not-used")
                add_run_event(run_record, "token_exchange_skipped")
            
            # Get agent from registry (with on-demand loading)
            try:
                agent = await agent_registry.get_or_load(agent_id, session)
                add_run_event(run_record, "agent_loaded", data={"agent_id": str(agent_id)})
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
                    # BaseStreamingAgent uses context dict
                    context = {
                        "principal": principal,
                        "session": session,
                        "user_id": principal.sub,
                        "agent_id": str(agent_id),
                    }
                    result = await asyncio.wait_for(
                        agent.run(prompt, context=context), timeout=timeout
                    )
                else:
                    # PydanticAI Agent uses deps
                    deps = BusiboxDeps(principal=principal, busibox_client=client)
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
