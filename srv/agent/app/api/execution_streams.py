"""
Server-Sent Events (SSE) streaming endpoints for real-time workflow/task execution updates.

Provides:
- GET /streams/executions/{execution_id}: Stream execution status, step progress, and outputs
- GET /streams/task-executions/{execution_id}: Stream task execution status and step progress

Mirrors the pattern from /streams/runs/{run_id} but for workflow executions.
"""

import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.models.domain import WorkflowExecution, StepExecution, TaskExecution
from app.schemas.auth import Principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/streams", tags=["streams"])

# SSE configuration
POLL_INTERVAL_SECONDS = 1.0  # Poll database every 1s (workflows change slower than chat)
MAX_POLL_DURATION_SECONDS = 600  # Max 10 minutes of streaming (workflows can be long)
TERMINAL_STATUSES = {"completed", "succeeded", "failed", "stopped", "timeout"}


@router.get("/executions/{execution_id}")
async def stream_workflow_execution(
    execution_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    """
    Stream workflow execution status and step progress via Server-Sent Events (SSE).
    
    Polls the database for execution updates and streams:
    - Execution status changes (pending -> running -> completed/failed)
    - Step start/complete events as they progress
    - Step output data when steps complete
    - Final execution result
    
    Events:
        - status: {"status": "running", "execution_id": "...", "current_step_id": "..."}
        - step_start: {"step_id": "...", "step_index": 0, "total_steps": 5}
        - step_complete: {"step_id": "...", "status": "completed", "duration_seconds": 12.3, "output_preview": "..."}
        - step_failed: {"step_id": "...", "error": "..."}
        - output: {"step_outputs": {...}, "duration_seconds": 45.2}
        - complete: {"status": "completed", "execution_id": "...", "duration_seconds": 45.2}
        - error: {"error": "...", "error_type": "..."}
    """
    # Verify execution exists
    execution = await session.get(WorkflowExecution, execution_id)
    
    if not execution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    
    # Check access control
    if execution.created_by != principal.sub and "admin" not in principal.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    logger.info(
        f"Starting SSE stream for workflow execution {execution_id}",
        extra={"execution_id": str(execution_id), "user_sub": principal.sub},
    )

    async def event_generator() -> AsyncGenerator[Dict[str, Any], None]:
        last_status = None
        last_step_statuses: Dict[str, str] = {}
        start_time = asyncio.get_event_loop().time()
        
        try:
            while True:
                # Check timeout
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > MAX_POLL_DURATION_SECONDS:
                    logger.warning(f"SSE stream for execution {execution_id} exceeded max duration")
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": "Stream timeout", "max_duration": MAX_POLL_DURATION_SECONDS}),
                    }
                    break
                
                # Refresh execution state from DB
                await session.refresh(execution)
                
                # Emit status change
                if execution.status != last_status:
                    yield {
                        "event": "status",
                        "data": json.dumps({
                            "status": execution.status,
                            "execution_id": str(execution.id),
                            "workflow_id": str(execution.workflow_id),
                            "current_step_id": execution.current_step_id,
                            "timestamp": execution.updated_at.isoformat() if execution.updated_at else None,
                        }),
                    }
                    last_status = execution.status
                
                # Check for step changes
                stmt = (
                    select(StepExecution)
                    .where(StepExecution.execution_id == execution_id)
                    .order_by(StepExecution.created_at)
                )
                result = await session.execute(stmt)
                steps = result.scalars().all()
                
                # Get total steps from workflow definition if available
                total_steps = len(steps) if steps else 0
                
                # Emit events for new or changed steps
                for idx, step in enumerate(steps):
                    prev_status = last_step_statuses.get(str(step.id))
                    
                    if prev_status is None:
                        # New step appeared
                        if step.status == "running":
                            yield {
                                "event": "step_start",
                                "data": json.dumps({
                                    "step_id": step.step_id,
                                    "step_index": idx,
                                    "total_steps": total_steps,
                                    "status": step.status,
                                    "timestamp": step.created_at.isoformat() if step.created_at else None,
                                }),
                            }
                    
                    if prev_status != step.status and step.status in ("completed", "succeeded"):
                        # Step completed
                        output_preview = None
                        if step.output_data:
                            # Create a brief preview of the output
                            preview = str(step.output_data)
                            output_preview = preview[:200] + "..." if len(preview) > 200 else preview
                        
                        yield {
                            "event": "step_complete",
                            "data": json.dumps({
                                "step_id": step.step_id,
                                "step_index": idx,
                                "status": step.status,
                                "duration_seconds": step.duration_seconds,
                                "output_preview": output_preview,
                                "usage_requests": step.usage_requests,
                                "usage_input_tokens": step.usage_input_tokens,
                                "usage_output_tokens": step.usage_output_tokens,
                                "timestamp": step.completed_at.isoformat() if step.completed_at else None,
                            }),
                        }
                    
                    if prev_status != step.status and step.status == "failed":
                        yield {
                            "event": "step_failed",
                            "data": json.dumps({
                                "step_id": step.step_id,
                                "step_index": idx,
                                "error": step.error,
                                "timestamp": step.completed_at.isoformat() if step.completed_at else None,
                            }),
                        }
                    
                    last_step_statuses[str(step.id)] = step.status
                
                # Check if execution is complete
                if execution.status in TERMINAL_STATUSES:
                    logger.info(
                        f"Workflow execution {execution_id} completed with status {execution.status}",
                        extra={"execution_id": str(execution_id), "status": execution.status},
                    )
                    
                    # Emit final output
                    if execution.step_outputs:
                        yield {
                            "event": "output",
                            "data": json.dumps({
                                "step_outputs": execution.step_outputs,
                                "duration_seconds": execution.duration_seconds,
                                "usage_requests": execution.usage_requests,
                                "usage_input_tokens": execution.usage_input_tokens,
                                "usage_output_tokens": execution.usage_output_tokens,
                                "estimated_cost_dollars": execution.estimated_cost_dollars,
                            }),
                        }
                    
                    # Emit completion event
                    yield {
                        "event": "complete",
                        "data": json.dumps({
                            "status": execution.status,
                            "execution_id": str(execution.id),
                            "duration_seconds": execution.duration_seconds,
                            "error": execution.error,
                            "timestamp": execution.completed_at.isoformat() if execution.completed_at else None,
                        }),
                    }
                    break
                
                # Wait before next poll
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        
        except asyncio.CancelledError:
            logger.info(f"SSE stream for execution {execution_id} cancelled by client")
            raise
        
        except Exception as e:
            logger.error(f"Error in SSE stream for execution {execution_id}: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e), "error_type": type(e).__name__}),
            }
    
    return EventSourceResponse(event_generator())


@router.get("/task-executions/{execution_id}")
async def stream_task_execution(
    execution_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    """
    Stream task execution progress via SSE.
    
    Monitors a TaskExecution record and its linked workflow execution or run.
    Provides a unified view of task progress regardless of whether it runs
    an agent or a workflow.
    
    Events:
        - status: {"status": "running", "task_execution_id": "..."}
        - step_start/step_complete/step_failed: (for workflow executions)
        - output: final output
        - complete: {"status": "completed", ...}
        - error: {"error": "..."}
    """
    # Verify task execution exists
    task_exec = await session.get(TaskExecution, execution_id)
    
    if not task_exec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task execution not found")
    
    logger.info(
        f"Starting SSE stream for task execution {execution_id}",
        extra={"execution_id": str(execution_id), "user_sub": principal.sub},
    )

    async def event_generator() -> AsyncGenerator[Dict[str, Any], None]:
        last_status = None
        last_step_statuses: Dict[str, str] = {}
        start_time = asyncio.get_event_loop().time()
        
        try:
            while True:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > MAX_POLL_DURATION_SECONDS:
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": "Stream timeout", "max_duration": MAX_POLL_DURATION_SECONDS}),
                    }
                    break
                
                # Refresh task execution
                await session.refresh(task_exec)
                
                # Emit task execution status change
                if task_exec.status != last_status:
                    yield {
                        "event": "status",
                        "data": json.dumps({
                            "status": task_exec.status,
                            "task_execution_id": str(task_exec.id),
                            "task_id": str(task_exec.task_id),
                            "timestamp": (task_exec.started_at or task_exec.created_at).isoformat(),
                        }),
                    }
                    last_status = task_exec.status
                
                # If it's a workflow execution, also stream step progress
                if task_exec.output_data and task_exec.output_data.get("workflow_execution_id"):
                    wf_exec_id = uuid.UUID(task_exec.output_data["workflow_execution_id"])
                    
                    # Get step executions
                    stmt = (
                        select(StepExecution)
                        .where(StepExecution.execution_id == wf_exec_id)
                        .order_by(StepExecution.created_at)
                    )
                    result = await session.execute(stmt)
                    steps = result.scalars().all()
                    
                    for idx, step in enumerate(steps):
                        prev_status = last_step_statuses.get(str(step.id))
                        
                        if prev_status is None and step.status == "running":
                            yield {
                                "event": "step_start",
                                "data": json.dumps({
                                    "step_id": step.step_id,
                                    "step_index": idx,
                                    "total_steps": len(steps),
                                    "status": step.status,
                                }),
                            }
                        
                        if prev_status != step.status and step.status in ("completed", "succeeded"):
                            output_preview = None
                            if step.output_data:
                                preview = str(step.output_data)
                                output_preview = preview[:200] + "..." if len(preview) > 200 else preview
                            
                            yield {
                                "event": "step_complete",
                                "data": json.dumps({
                                    "step_id": step.step_id,
                                    "step_index": idx,
                                    "status": step.status,
                                    "duration_seconds": step.duration_seconds,
                                    "output_preview": output_preview,
                                }),
                            }
                        
                        if prev_status != step.status and step.status == "failed":
                            yield {
                                "event": "step_failed",
                                "data": json.dumps({
                                    "step_id": step.step_id,
                                    "step_index": idx,
                                    "error": step.error,
                                }),
                            }
                        
                        last_step_statuses[str(step.id)] = step.status
                
                # Check if task execution is complete
                if task_exec.status in TERMINAL_STATUSES:
                    # Emit output
                    if task_exec.output_summary:
                        yield {
                            "event": "output",
                            "data": json.dumps({
                                "output_summary": task_exec.output_summary,
                                "output_data": task_exec.output_data,
                                "duration_seconds": task_exec.duration_seconds,
                            }),
                        }
                    
                    yield {
                        "event": "complete",
                        "data": json.dumps({
                            "status": task_exec.status,
                            "task_execution_id": str(task_exec.id),
                            "duration_seconds": task_exec.duration_seconds,
                            "error": task_exec.error,
                        }),
                    }
                    break
                
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        
        except asyncio.CancelledError:
            logger.info(f"SSE stream for task execution {execution_id} cancelled by client")
            raise
        
        except Exception as e:
            logger.error(f"Error in SSE stream for task execution {execution_id}: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e), "error_type": type(e).__name__}),
            }
    
    return EventSourceResponse(event_generator())
