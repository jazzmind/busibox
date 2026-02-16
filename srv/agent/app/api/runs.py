"""
API endpoints for agent run management.

Provides:
- POST /runs: Execute an agent run
- GET /runs/{run_id}: Retrieve run details
- GET /runs: List runs with filtering
- POST /runs/schedule: Schedule cron-based runs
"""

import logging
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.db.session import SessionLocal, get_session
from app.models.domain import AgentDefinition
from app.schemas.auth import Principal
from app.schemas.run import RunCreate, RunInvoke, RunInvokeResponse, RunRead, ScheduleCreate, ScheduleRead
from app.services.run_service import create_run, get_run_by_id, list_runs
from app.services.builtin_agents import BUILTIN_AGENT_METADATA
from app.services.scheduler import run_scheduler
from app.workflows.engine import execute_workflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


def _parse_structured_output(raw: Any) -> Any:
    """Best-effort normalization for workflow output to structured JSON."""
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return raw

    text = raw.strip()
    if not text:
        return raw

    # 1) Direct JSON
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2) JSON fenced code blocks
    fenced_matches = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    for block in fenced_matches:
        try:
            return json.loads(block.strip())
        except Exception:
            continue

    # 3) First balanced-looking object slice (best effort)
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        candidate = text[first : last + 1]
        try:
            return json.loads(candidate)
        except Exception:
            return raw

    return raw


@router.post("", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def run_agent(
    payload: RunCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> RunRead:
    """
    Execute an agent run asynchronously.
    
    Args:
        payload: Run creation payload with agent_id, input, and optional tier
        principal: Authenticated user principal
        session: Database session
        
    Returns:
        RunRead: Created run record with initial status
        
    Raises:
        HTTPException: 400 if validation fails, 404 if agent not found
    """
    try:
        logger.info(
            f"Creating run for agent {payload.agent_id} by user {principal.sub}",
            extra={
                "agent_id": str(payload.agent_id),
                "user_sub": principal.sub,
                "agent_tier": payload.agent_tier,
            },
        )
        
        run_record = await create_run(
            session=session,
            principal=principal,
            agent_id=payload.agent_id,
            payload=payload.input,
            scopes=["search.read", "data.write", "rag.query"],
            purpose="agent-run",
            agent_tier=payload.agent_tier,
        )
        
        logger.info(
            f"Run {run_record.id} created with status {run_record.status}",
            extra={"run_id": str(run_record.id), "status": run_record.status},
        )
        
        return RunRead.model_validate(run_record)
        
    except ValueError as e:
        logger.warning(f"Invalid run request: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create run: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create run",
        )


async def _resolve_agent_id(
    *,
    session: AsyncSession,
    principal: Principal,
    agent_id: Optional[uuid.UUID],
    agent_name: Optional[str],
) -> uuid.UUID:
    """Resolve agent by id or name with user visibility checks."""
    if agent_id:
        return agent_id

    if not agent_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either agent_id or agent_name is required",
        )

    # Built-in code agents are reserved and resolved deterministically by name.
    for metadata in BUILTIN_AGENT_METADATA.values():
        if metadata["name"] == agent_name:
            return uuid.uuid5(uuid.NAMESPACE_DNS, f"busibox.builtin.{agent_name}")

    # Fallback to DB agent by name: owner or DB-level built-in.
    stmt = select(AgentDefinition).where(
        AgentDefinition.name == agent_name,
        AgentDefinition.is_active.is_(True),
    )
    result = await session.execute(stmt)
    definition = result.scalar_one_or_none()

    if not definition:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    if not definition.is_builtin and definition.created_by != principal.sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    return definition.id


@router.post("/invoke", response_model=RunInvokeResponse, status_code=status.HTTP_200_OK)
async def invoke_agent(
    payload: RunInvoke,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> RunInvokeResponse:
    """
    Invoke an agent synchronously for deterministic workflow/programmatic calls.

    This endpoint waits for completion and returns output directly.
    """
    try:
        resolved_agent_id = await _resolve_agent_id(
            session=session,
            principal=principal,
            agent_id=payload.agent_id,
            agent_name=payload.agent_name,
        )

        run_payload = dict(payload.input or {})
        if payload.response_schema is not None:
            run_payload["response_schema"] = payload.response_schema

        run_record = await create_run(
            session=session,
            principal=principal,
            agent_id=resolved_agent_id,
            payload=run_payload,
            # Keep scope list minimal; specific tools can still perform exchange.
            scopes=[],
            purpose="agent-invoke",
            agent_tier=payload.agent_tier,
        )

        output_data = None
        error_message = None
        if run_record.output:
            if "result" in run_record.output:
                output_data = run_record.output.get("result")
            elif "data" in run_record.output:
                output_data = run_record.output.get("data")
            elif "error" in run_record.output:
                error_message = str(run_record.output.get("error"))
            else:
                output_data = run_record.output

        if payload.response_schema is not None:
            output_data = _parse_structured_output(output_data)

        if run_record.status in {"failed", "timeout"} and not error_message:
            error_message = str((run_record.output or {}).get("error", "Invocation failed"))

        return RunInvokeResponse(
            run_id=run_record.id,
            status=run_record.status,
            output=output_data,
            error=error_message,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to invoke run: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to invoke run",
        )


@router.get("/{run_id}", response_model=RunRead)
async def get_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> RunRead:
    """
    Retrieve a run by ID with full execution history.
    
    Args:
        run_id: Run UUID
        principal: Authenticated user principal
        session: Database session
        
    Returns:
        RunRead: Run record with output, events, and status
        
    Raises:
        HTTPException: 404 if run not found, 403 if access denied
    """
    run_record = await get_run_by_id(session, run_id)
    
    if not run_record:
        logger.warning(f"Run {run_id} not found")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    
    # Check if user has permission to view this run
    if run_record.created_by != principal.sub and "admin" not in principal.roles:
        logger.warning(
            f"User {principal.sub} denied access to run {run_id} (owner: {run_record.created_by})"
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    return RunRead.model_validate(run_record)


@router.get("", response_model=List[RunRead])
async def list_agent_runs(
    agent_id: Optional[uuid.UUID] = Query(None, description="Filter by agent ID"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[RunRead]:
    """
    List runs with optional filtering.
    
    Args:
        agent_id: Optional filter by agent ID
        status_filter: Optional filter by status (pending/running/succeeded/failed/timeout)
        limit: Maximum number of results (1-100)
        offset: Pagination offset
        principal: Authenticated user principal
        session: Database session
        
    Returns:
        List[RunRead]: List of run records
        
    Note:
        Non-admin users only see their own runs.
    """
    # Non-admin users can only see their own runs
    created_by = None if "admin" in principal.roles else principal.sub
    
    logger.info(
        f"Listing runs for user {principal.sub}",
        extra={
            "user_sub": principal.sub,
            "agent_id": str(agent_id) if agent_id else None,
            "status": status_filter,
            "limit": limit,
            "offset": offset,
        },
    )
    
    runs = await list_runs(
        session=session,
        agent_id=agent_id,
        created_by=created_by,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    
    return [RunRead.model_validate(run) for run in runs]


@router.post("/schedule", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED)
async def schedule_run(
    payload: ScheduleCreate,
    principal: Principal = Depends(get_principal),
) -> ScheduleRead:
    """
    Schedule a cron-based agent run with automatic token refresh.
    
    The scheduler will:
    1. Refresh the authentication token before each execution
    2. Execute the agent run at the specified cron schedule
    3. Persist run results to the database
    
    Args:
        payload: Schedule creation payload with agent_id, input, cron, and tier
        principal: Authenticated user principal
        
    Returns:
        ScheduleRead: Created schedule with job_id and next_run_time
        
    Raises:
        HTTPException: 400 if cron expression is invalid, 404 if agent not found
    """
    try:
        # Default scopes if not provided
        scopes = payload.scopes if payload.scopes else ["agent.execute", "search.read"]
        
        # Schedule the job
        job_id = run_scheduler.schedule_agent_run(
            session_factory=SessionLocal,
            principal=principal,
            agent_id=payload.agent_id,
            payload=payload.input,
            scopes=scopes,
            purpose=payload.purpose,
            cron=payload.cron,
            agent_tier=payload.agent_tier,
        )
        
        # Get job metadata
        job_metadata = run_scheduler.get_job(job_id)
        if not job_metadata:
            raise HTTPException(
                status_code=500,
                detail="Failed to retrieve scheduled job metadata"
            )
        
        logger.info(
            f"Scheduled job {job_id} for agent {payload.agent_id} by {principal.sub}, "
            f"next run: {job_metadata.next_run_time}"
        )
        
        return ScheduleRead(
            job_id=job_metadata.job_id,
            agent_id=job_metadata.agent_id,
            cron=job_metadata.cron,
            principal_sub=job_metadata.principal_sub,
            next_run_time=job_metadata.next_run_time,
        )
        
    except ValueError as e:
        logger.error(f"Invalid schedule request: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid schedule configuration: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Failed to schedule run: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to schedule run: {str(e)}"
        )


@router.get("/schedule", response_model=List[ScheduleRead])
async def list_schedules(
    principal: Principal = Depends(get_principal),
) -> List[ScheduleRead]:
    """
    List all scheduled jobs.
    
    Returns:
        List of scheduled jobs with metadata
    """
    jobs = run_scheduler.list_jobs()
    return [
        ScheduleRead(
            job_id=job.job_id,
            agent_id=job.agent_id,
            cron=job.cron,
            principal_sub=job.principal_sub,
            next_run_time=job.next_run_time,
        )
        for job in jobs
    ]


@router.delete("/schedule/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_schedule(
    job_id: str,
    principal: Principal = Depends(get_principal),
) -> None:
    """
    Cancel a scheduled job.
    
    Args:
        job_id: Job identifier to cancel
        principal: Authenticated user principal
        
    Raises:
        HTTPException: 404 if job not found
    """
    # Check if job exists and belongs to user (or user is admin)
    job_metadata = run_scheduler.get_job(job_id)
    if not job_metadata:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scheduled job {job_id} not found"
        )
    
    # Authorization: only owner or admin can cancel
    if job_metadata.principal_sub != principal.sub and "admin" not in principal.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only cancel your own scheduled jobs"
        )
    
    # Cancel the job
    success = run_scheduler.cancel_job(job_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel job {job_id}"
        )
    
    logger.info(f"Cancelled scheduled job {job_id} by {principal.sub}")


@router.post("/workflow", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def execute_workflow_run(
    workflow_id: uuid.UUID,
    input_data: Dict[str, Any],
    scopes: Optional[List[str]] = None,
    purpose: str = "workflow-execution",
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> RunRead:
    """
    Execute a multi-step workflow.
    
    The workflow will:
    1. Execute steps sequentially
    2. Pass outputs between steps
    3. Persist step events to run record
    4. Handle errors gracefully
    
    Args:
        workflow_id: Workflow definition UUID
        input_data: Initial workflow input
        scopes: Required scopes (defaults to agent.execute)
        purpose: Purpose for token exchange
        principal: Authenticated user principal
        session: Database session
        
    Returns:
        RunRead: Run record with workflow execution results
        
    Raises:
        HTTPException: 404 if workflow not found, 400 if validation fails
    """
    try:
        # Default scopes
        if not scopes:
            scopes = ["agent.execute", "search.read", "data.write"]
        
        # Execute workflow
        run_record = await execute_workflow(
            session=session,
            principal=principal,
            workflow_id=workflow_id,
            input_data=input_data,
            scopes=scopes,
            purpose=purpose,
        )
        
        logger.info(
            f"Workflow execution completed, run {run_record.id}, status: {run_record.status}",
            extra={
                "run_id": str(run_record.id),
                "workflow_id": str(workflow_id),
                "status": run_record.status,
                "created_by": principal.sub,
            },
        )
        
        return RunRead.model_validate(run_record)
        
    except Exception as e:
        logger.error(f"Workflow execution failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Workflow execution failed: {str(e)}"
        )
