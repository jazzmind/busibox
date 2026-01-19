"""
API endpoints for Agent Tasks.

Provides CRUD operations and execution control for event-driven agent tasks.
"""

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.schemas.auth import Principal
from app.schemas.task import (
    TaskCreate,
    TaskExecutionRead,
    TaskListResponse,
    TaskRead,
    TaskRunRequest,
    TaskRunResponse,
    TaskUpdate,
)
from app.services.task_service import (
    create_task,
    create_task_execution,
    delete_task,
    get_task,
    list_task_executions,
    list_tasks,
    pause_task,
    resume_task,
    task_to_read,
    update_task,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _get_base_url(request: Request) -> str:
    """Get base URL from request."""
    return str(request.base_url).rstrip("/")


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_agent_task(
    task_data: TaskCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TaskRead:
    """
    Create a new agent task.
    
    Agent tasks are event-driven actions that execute agents on a schedule,
    via webhooks, or as one-time executions. Each task has:
    - A target agent to execute
    - A trigger configuration (cron, webhook, or one-time)
    - Optional notification settings
    - Task-specific insights/memories
    
    Args:
        task_data: Task creation payload
        request: HTTP request for URL generation
        principal: Authenticated user
        session: Database session
        
    Returns:
        Created task with generated IDs and webhook URLs
    """
    try:
        task = await create_task(session, principal, task_data)
        base_url = _get_base_url(request)
        
        logger.info(
            f"Created task {task.id} for user {principal.sub}",
            extra={
                "task_id": str(task.id),
                "user_sub": principal.sub,
                "trigger_type": task.trigger_type,
            },
        )
        
        # Include webhook_secret on creation so client can store it
        return task_to_read(task, base_url, include_secret=True)
        
    except ValueError as e:
        logger.warning(f"Invalid task creation request: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to create task: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create task",
        )


@router.get("", response_model=TaskListResponse)
async def list_agent_tasks(
    request: Request,
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    trigger_type: Optional[str] = Query(None, description="Filter by trigger type"),
    limit: int = Query(50, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TaskListResponse:
    """
    List tasks for the current user.
    
    Args:
        status_filter: Optional filter by status (active, paused, completed, failed)
        trigger_type: Optional filter by trigger type (cron, webhook, one_time)
        limit: Maximum number of results (1-100)
        offset: Pagination offset
        principal: Authenticated user
        session: Database session
        
    Returns:
        Paginated list of tasks
    """
    tasks, total = await list_tasks(
        session=session,
        user_id=principal.sub,
        status=status_filter,
        trigger_type=trigger_type,
        limit=limit,
        offset=offset,
    )
    
    base_url = _get_base_url(request)
    task_reads = [task_to_read(task, base_url) for task in tasks]
    
    return TaskListResponse(
        tasks=task_reads,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{task_id}", response_model=TaskRead)
async def get_agent_task(
    task_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TaskRead:
    """
    Get a task by ID.
    
    Args:
        task_id: Task UUID
        request: HTTP request for URL generation
        principal: Authenticated user
        session: Database session
        
    Returns:
        Task details
        
    Raises:
        HTTPException: 404 if task not found
    """
    task = await get_task(session, task_id, principal.sub)
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    
    base_url = _get_base_url(request)
    return task_to_read(task, base_url)


@router.patch("/{task_id}", response_model=TaskRead)
async def update_agent_task(
    task_id: uuid.UUID,
    update_data: TaskUpdate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TaskRead:
    """
    Update a task.
    
    Args:
        task_id: Task UUID
        update_data: Fields to update
        request: HTTP request for URL generation
        principal: Authenticated user
        session: Database session
        
    Returns:
        Updated task
        
    Raises:
        HTTPException: 404 if task not found
    """
    task = await update_task(session, task_id, principal.sub, update_data)
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    
    logger.info(f"Updated task {task_id}")
    
    base_url = _get_base_url(request)
    return task_to_read(task, base_url)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_task(
    task_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """
    Delete a task.
    
    Args:
        task_id: Task UUID
        principal: Authenticated user
        session: Database session
        
    Raises:
        HTTPException: 404 if task not found
    """
    success = await delete_task(session, task_id, principal.sub)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    
    logger.info(f"Deleted task {task_id}")


@router.post("/{task_id}/pause", response_model=TaskRead)
async def pause_agent_task(
    task_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TaskRead:
    """
    Pause a task.
    
    Pausing a task stops scheduled executions until resumed.
    
    Args:
        task_id: Task UUID
        request: HTTP request for URL generation
        principal: Authenticated user
        session: Database session
        
    Returns:
        Updated task with paused status
    """
    task = await pause_task(session, task_id, principal.sub)
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    
    logger.info(f"Paused task {task_id}")
    
    base_url = _get_base_url(request)
    return task_to_read(task, base_url)


@router.post("/{task_id}/resume", response_model=TaskRead)
async def resume_agent_task(
    task_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TaskRead:
    """
    Resume a paused task.
    
    Args:
        task_id: Task UUID
        request: HTTP request for URL generation
        principal: Authenticated user
        session: Database session
        
    Returns:
        Updated task with active status
    """
    task = await resume_task(session, task_id, principal.sub)
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    
    logger.info(f"Resumed task {task_id}")
    
    base_url = _get_base_url(request)
    return task_to_read(task, base_url)


@router.post("/{task_id}/run", response_model=TaskRunResponse)
async def run_agent_task(
    task_id: uuid.UUID,
    run_request: Optional[TaskRunRequest] = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TaskRunResponse:
    """
    Manually trigger a task execution.
    
    This creates a new execution regardless of the task's schedule.
    
    Args:
        task_id: Task UUID
        run_request: Optional input overrides
        principal: Authenticated user
        session: Database session
        
    Returns:
        Execution details with status
    """
    task = await get_task(session, task_id, principal.sub)
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    
    if task.status not in ("active", "paused"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot run task with status '{task.status}'",
        )
    
    # Create execution record
    input_data = None
    if run_request and run_request.input_override:
        input_data = run_request.input_override
    
    execution = await create_task_execution(
        session=session,
        task=task,
        trigger_source="manual",
        input_data=input_data,
    )
    
    logger.info(
        f"Manually triggered task {task_id}, execution {execution.id}",
        extra={
            "task_id": str(task_id),
            "execution_id": str(execution.id),
            "user_sub": principal.sub,
        },
    )
    
    # TODO: Queue the actual execution (will be implemented with task executor)
    
    return TaskRunResponse(
        execution_id=execution.id,
        task_id=task_id,
        run_id=None,  # Will be set when agent run starts
        status="pending",
        message="Task execution queued",
    )


@router.get("/{task_id}/executions", response_model=List[TaskExecutionRead])
async def list_task_executions_endpoint(
    task_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[TaskExecutionRead]:
    """
    List executions for a task.
    
    Args:
        task_id: Task UUID
        limit: Maximum number of results
        offset: Pagination offset
        principal: Authenticated user
        session: Database session
        
    Returns:
        List of task executions
    """
    # Verify task exists and user has access
    task = await get_task(session, task_id, principal.sub)
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    
    executions = await list_task_executions(
        session=session,
        task_id=task_id,
        limit=limit,
        offset=offset,
    )
    
    return [TaskExecutionRead.model_validate(e) for e in executions]


@router.get("/{task_id}/insights")
async def get_task_insights(
    task_id: uuid.UUID,
    limit: int = Query(10, ge=1, le=50, description="Max results"),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    """
    Get insights/memories for a task.
    
    Task insights are stored results from previous executions that help
    the agent avoid duplicates and maintain context.
    
    Args:
        task_id: Task UUID
        limit: Maximum number of insights
        principal: Authenticated user
        session: Database session
        
    Returns:
        List of task insights
    """
    # Verify task exists and user has access
    task = await get_task(session, task_id, principal.sub)
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    
    # TODO: Implement task insights retrieval from Milvus
    # This will be implemented when we extend the insights service
    
    return {
        "task_id": str(task_id),
        "insights": [],
        "count": 0,
        "message": "Task insights feature pending implementation",
    }
