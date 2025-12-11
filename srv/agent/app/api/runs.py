"""
API endpoints for agent run management.

Provides:
- POST /runs: Execute an agent run
- GET /runs/{run_id}: Retrieve run details
- GET /runs: List runs with filtering
- POST /runs/schedule: Schedule cron-based runs
"""

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.db.session import SessionLocal, get_session
from app.schemas.auth import Principal
from app.schemas.run import RunCreate, RunRead
from app.services.run_service import create_run, get_run_by_id, list_runs
from app.services.scheduler import run_scheduler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


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
            scopes=["search.read", "ingest.write", "rag.query"],
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


@router.post("/schedule", status_code=status.HTTP_202_ACCEPTED)
async def schedule_run(
    payload: RunCreate,
    cron: str,
    principal: Principal = Depends(get_principal),
):
    """
    Schedule a cron-based agent run. Uses shared scheduler.
    """
    run_scheduler.schedule_agent_run(
        session_factory=SessionLocal,
        principal=principal,
        agent_id=payload.agent_id,
        payload=payload.input,
        scopes=["search.read", "ingest.write", "rag.query"],
        purpose="agent-run",
        cron=cron,
    )
    return {"status": "scheduled", "cron": cron}
