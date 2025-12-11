"""
Server-Sent Events (SSE) streaming endpoints for real-time run updates.

Provides:
- GET /streams/runs/{run_id}: Stream run status and events in real-time
"""

import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.services.run_service import get_run_by_id
from app.schemas.auth import Principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/streams", tags=["streams"])

# SSE configuration
POLL_INTERVAL_SECONDS = 0.5  # Poll database every 500ms
MAX_POLL_DURATION_SECONDS = 300  # Max 5 minutes of streaming
TERMINAL_STATUSES = {"succeeded", "failed", "timeout"}


@router.get("/runs/{run_id}")
async def stream_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    """
    Stream run status and events via Server-Sent Events (SSE).
    
    Polls the database for run updates and streams:
    - Status changes (pending → running → succeeded/failed/timeout)
    - New events as they're added to the run record
    - Final output when run completes
    
    Args:
        run_id: Run UUID to stream
        principal: Authenticated user principal
        session: Database session
        
    Returns:
        EventSourceResponse: SSE stream of run updates
        
    Raises:
        HTTPException: 404 if run not found, 403 if access denied
        
    Events:
        - status: {"status": "running", "timestamp": "..."}
        - event: {"type": "tool_call", "data": {...}, "timestamp": "..."}
        - output: {"message": "...", "tool_calls": [...]}
        - error: {"error": "...", "error_type": "..."}
        - complete: {"status": "succeeded", "output": {...}}
    """
    # Verify run exists and user has access
    run = await get_run_by_id(session, run_id)
    
    if not run:
        logger.warning(f"Stream requested for non-existent run {run_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    
    # Check access control
    if run.created_by != principal.sub and "admin" not in principal.roles:
        logger.warning(
            f"User {principal.sub} denied stream access to run {run_id} (owner: {run.created_by})"
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    logger.info(
        f"Starting SSE stream for run {run_id}",
        extra={"run_id": str(run_id), "user_sub": principal.sub},
    )

    async def event_generator() -> AsyncGenerator[Dict[str, Any], None]:
        """
        Generate SSE events by polling the database for run updates.
        
        Yields events for:
        - Status changes
        - New events in the event log
        - Final output
        - Errors
        """
        last_status = None
        last_event_count = 0
        start_time = asyncio.get_event_loop().time()
        
        try:
            while True:
                # Check timeout
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > MAX_POLL_DURATION_SECONDS:
                    logger.warning(f"SSE stream for run {run_id} exceeded max duration")
                    yield {
                        "event": "error",
                        "data": json.dumps(
                            {"error": "Stream timeout", "max_duration": MAX_POLL_DURATION_SECONDS}
                        ),
                    }
                    break
                
                # Fetch latest run state
                run = await get_run_by_id(session, run_id)
                
                if not run:
                    yield {"event": "error", "data": json.dumps({"error": "Run not found"})}
                    break
                
                # Emit status change
                if run.status != last_status:
                    logger.debug(f"Run {run_id} status changed: {last_status} → {run.status}")
                    yield {
                        "event": "status",
                        "data": json.dumps(
                            {
                                "status": run.status,
                                "timestamp": run.updated_at.isoformat(),
                                "run_id": str(run.id),
                            }
                        ),
                    }
                    last_status = run.status
                
                # Emit new events
                if run.events and len(run.events) > last_event_count:
                    new_events = run.events[last_event_count:]
                    for event in new_events:
                        logger.debug(f"Run {run_id} new event: {event.get('type')}")
                        yield {
                            "event": "event",
                            "data": json.dumps(event),
                        }
                    last_event_count = len(run.events)
                
                # Check if run is complete
                if run.status in TERMINAL_STATUSES:
                    logger.info(
                        f"Run {run_id} completed with status {run.status}",
                        extra={"run_id": str(run_id), "status": run.status},
                    )
                    
                    # Emit final output
                    if run.output:
                        yield {
                            "event": "output",
                            "data": json.dumps(run.output),
                        }
                    
                    # Emit completion event
                    yield {
                        "event": "complete",
                        "data": json.dumps(
                            {
                                "status": run.status,
                                "run_id": str(run.id),
                                "timestamp": run.updated_at.isoformat(),
                            }
                        ),
                    }
                    break
                
                # Wait before next poll
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        
        except asyncio.CancelledError:
            logger.info(f"SSE stream for run {run_id} cancelled by client")
            raise
        
        except Exception as e:
            logger.error(f"Error in SSE stream for run {run_id}: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e), "error_type": type(e).__name__}),
            }
    
    return EventSourceResponse(event_generator())
