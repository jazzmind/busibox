"""
Task Service for Agent Tasks.

Handles CRUD operations and execution logic for event-driven agent tasks
with pre-authorized tokens, insights/memories, and notifications.
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import AgentDefinition, AgentTask, RunRecord, TaskExecution, WorkflowDefinition
from app.schemas.auth import Principal
from app.schemas.task import (
    InsightsConfig,
    NotificationConfig,
    TaskCreate,
    TaskRead,
    TaskUpdate,
    TriggerConfig,
    get_cron_from_preset,
)
from app.services.builtin_agents import get_builtin_agent_definitions
from app.auth.tokens import create_delegation_token

logger = logging.getLogger(__name__)


def _now() -> datetime:
    """Return timezone-naive UTC datetime for PostgreSQL."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _generate_webhook_secret() -> str:
    """Generate a secure webhook secret."""
    return secrets.token_urlsafe(32)


async def create_task(
    session: AsyncSession,
    principal: Principal,
    task_data: TaskCreate,
) -> AgentTask:
    """
    Create a new agent task.
    
    Args:
        session: Database session
        principal: User principal creating the task
        task_data: Task creation data
        
    Returns:
        Created AgentTask
        
    Raises:
        ValueError: If agent/workflow not found or validation fails
    """
    from app.services.builtin_workflows import get_builtin_workflow_definitions, is_builtin_workflow
    
    # Validate that either agent_id or workflow_id is provided
    if not task_data.agent_id and not task_data.workflow_id:
        raise ValueError("Either agent_id or workflow_id must be provided")
    if task_data.agent_id and task_data.workflow_id:
        raise ValueError("Cannot specify both agent_id and workflow_id")
    
    target_name = None
    
    if task_data.agent_id:
        # Verify agent exists - check both database and built-in code agents
        agent_result = await session.execute(
            select(AgentDefinition).where(AgentDefinition.id == task_data.agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        
        # If not found in database, check built-in agents loaded from code
        target_name = agent.name if agent else None
        if not agent:
            builtin_agents = get_builtin_agent_definitions()
            builtin_agent_map = {a.id: a for a in builtin_agents}
            if task_data.agent_id not in builtin_agent_map:
                raise ValueError(f"Agent {task_data.agent_id} not found")
            target_name = builtin_agent_map[task_data.agent_id].name
    
    elif task_data.workflow_id:
        # Verify workflow exists - check both database and built-in workflows
        workflow_result = await session.execute(
            select(WorkflowDefinition).where(WorkflowDefinition.id == task_data.workflow_id)
        )
        workflow = workflow_result.scalar_one_or_none()
        
        target_name = workflow.name if workflow else None
        if not workflow:
            # Check built-in workflows
            if not is_builtin_workflow(task_data.workflow_id):
                raise ValueError(f"Workflow {task_data.workflow_id} not found")
            builtin_workflows = get_builtin_workflow_definitions()
            for bw in builtin_workflows:
                if bw.id == task_data.workflow_id:
                    target_name = bw.name
                    break
    
    # Handle schedule presets
    trigger_config = task_data.trigger_config.model_dump()
    if task_data.trigger_type == "cron":
        cron = trigger_config.get("cron", "")
        # Check if it's a preset
        preset_cron = get_cron_from_preset(cron)
        if preset_cron:
            trigger_config["cron"] = preset_cron
    
    # Generate webhook secret for webhook triggers
    webhook_secret = None
    if task_data.trigger_type == "webhook":
        webhook_secret = _generate_webhook_secret()
    
    # Create delegation token for the task using the proper /oauth/delegation endpoint
    # This creates a long-lived token (3 years) that can be used for token exchange
    delegation_token = None
    delegation_expires_at = None
    
    try:
        if not principal.token:
            raise ValueError("Principal must have a token to create delegation")
        
        # Create a proper delegation token via authz /oauth/delegation endpoint
        token_response = await create_delegation_token(
            subject_token=principal.token,
            name=f"Task: {task_data.name}",
            scopes=task_data.scopes,
            # Default 3 years (94608000 seconds)
        )
        delegation_token = token_response.access_token
        delegation_expires_at = token_response.expires_at
        if delegation_expires_at and delegation_expires_at.tzinfo:
            delegation_expires_at = delegation_expires_at.replace(tzinfo=None)
        
        logger.info(
            f"Created delegation token for task '{task_data.name}'",
            extra={"expires_at": delegation_expires_at, "scopes": task_data.scopes}
        )
    except Exception as e:
        logger.warning(f"Failed to create delegation token for task: {e}")
        # Continue without delegation token - will need to refresh on execution
    
    # Calculate next run time for cron triggers
    next_run_at = None
    if task_data.trigger_type == "cron":
        next_run_at = _calculate_next_run(trigger_config.get("cron", ""))
    elif task_data.trigger_type == "one_time":
        run_at = trigger_config.get("run_at")
        if run_at:
            if isinstance(run_at, str):
                next_run_at = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
            else:
                next_run_at = run_at
            if next_run_at.tzinfo:
                next_run_at = next_run_at.replace(tzinfo=None)
    
    # Build notification config
    notification_config = {}
    if task_data.notification_config:
        notification_config = task_data.notification_config.model_dump()
    
    # Build insights config
    insights_config = {"enabled": True, "max_insights": 50, "purge_after_days": 30}
    if task_data.insights_config:
        insights_config = task_data.insights_config.model_dump()
    
    # Build output saving config
    output_saving_config = None
    if task_data.output_saving_config:
        output_saving_config = task_data.output_saving_config.model_dump()
    
    # Create task
    task = AgentTask(
        name=task_data.name,
        description=task_data.description,
        user_id=principal.sub,
        agent_id=task_data.agent_id,
        workflow_id=task_data.workflow_id,
        prompt=task_data.prompt,
        input_config=task_data.input_config,
        trigger_type=task_data.trigger_type,
        trigger_config=trigger_config,
        delegation_token=delegation_token,
        delegation_scopes=task_data.scopes,
        delegation_expires_at=delegation_expires_at,
        notification_config=notification_config,
        insights_config=insights_config,
        output_saving_config=output_saving_config,
        status="active",
        webhook_secret=webhook_secret,
        next_run_at=next_run_at,
    )
    
    session.add(task)
    await session.commit()
    await session.refresh(task)
    
    target_type = "agent" if task_data.agent_id else "workflow"
    logger.info(
        f"Created task {task.id} for user {principal.sub}, "
        f"trigger_type={task.trigger_type}, {target_type}={target_name}"
    )
    
    return task


async def get_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: Optional[str] = None,
) -> Optional[AgentTask]:
    """
    Get a task by ID.
    
    Args:
        session: Database session
        task_id: Task UUID
        user_id: Optional user ID for access control
        
    Returns:
        AgentTask or None if not found
    """
    stmt = select(AgentTask).where(AgentTask.id == task_id)
    if user_id:
        stmt = stmt.where(AgentTask.user_id == user_id)
    
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_tasks(
    session: AsyncSession,
    user_id: str,
    status: Optional[str] = None,
    trigger_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[List[AgentTask], int]:
    """
    List tasks for a user.
    
    Args:
        session: Database session
        user_id: User ID
        status: Optional status filter
        trigger_type: Optional trigger type filter
        limit: Max results
        offset: Pagination offset
        
    Returns:
        Tuple of (tasks list, total count)
    """
    # Build query
    conditions = [AgentTask.user_id == user_id]
    if status:
        conditions.append(AgentTask.status == status)
    if trigger_type:
        conditions.append(AgentTask.trigger_type == trigger_type)
    
    # Count query
    count_stmt = select(AgentTask).where(and_(*conditions))
    count_result = await session.execute(count_stmt)
    total = len(count_result.scalars().all())
    
    # List query with pagination
    stmt = (
        select(AgentTask)
        .where(and_(*conditions))
        .order_by(AgentTask.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    
    result = await session.execute(stmt)
    tasks = result.scalars().all()
    
    return list(tasks), total


async def update_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: str,
    update_data: TaskUpdate,
) -> Optional[AgentTask]:
    """
    Update a task.
    
    Args:
        session: Database session
        task_id: Task UUID
        user_id: User ID for access control
        update_data: Update data
        
    Returns:
        Updated AgentTask or None if not found
    """
    task = await get_task(session, task_id, user_id)
    if not task:
        logger.warning(f"Task {task_id} not found for user {user_id}")
        return None
    
    # Apply updates
    update_dict = update_data.model_dump(exclude_unset=True)
    logger.info(f"Updating task {task_id} with fields: {list(update_dict.keys())}, values: {update_dict}")
    
    for field, value in update_dict.items():
        # Handle nullable fields (agent_id/workflow_id can be set to None)
        if field in ("agent_id", "workflow_id"):
            logger.info(f"Setting {field}={value}")
            setattr(task, field, value)
        elif value is not None:
            if field == "trigger_config" and isinstance(value, dict):
                # Merge trigger config - create new dict to trigger SQLAlchemy change detection
                current = dict(task.trigger_config or {})
                logger.info(f"Merging trigger_config: current={current}, incoming={value}")
                current.update(value)
                task.trigger_config = current  # Assign new dict
                logger.info(f"trigger_config after merge: {task.trigger_config}")
            elif field == "notification_config" and isinstance(value, dict):
                # Merge notification config - create new dict to trigger SQLAlchemy change detection
                current = dict(task.notification_config or {})
                current.update(value)
                task.notification_config = current  # Assign new dict
            elif field == "insights_config" and isinstance(value, dict):
                # Merge insights config - create new dict to trigger SQLAlchemy change detection
                current = dict(task.insights_config or {})
                current.update(value)
                task.insights_config = current  # Assign new dict
            elif field == "output_saving_config" and isinstance(value, dict):
                # Merge output saving config
                current = dict(task.output_saving_config or {})
                current.update(value)
                task.output_saving_config = current
            else:
                logger.info(f"Setting {field}={value}")
                setattr(task, field, value)
    
    # Update next_run_at if trigger config changed
    if "trigger_config" in update_dict and task.trigger_type == "cron":
        cron = task.trigger_config.get("cron")
        if cron:
            task.next_run_at = _calculate_next_run(cron)
    
    task.updated_at = _now()
    await session.commit()
    await session.refresh(task)
    
    logger.info(f"Updated task {task_id}")
    return task


async def delete_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: str,
) -> bool:
    """
    Delete a task.
    
    Args:
        session: Database session
        task_id: Task UUID
        user_id: User ID for access control
        
    Returns:
        True if deleted, False if not found
    """
    task = await get_task(session, task_id, user_id)
    if not task:
        return False
    
    await session.delete(task)
    await session.commit()
    
    logger.info(f"Deleted task {task_id}")
    return True


async def pause_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: str,
) -> Optional[AgentTask]:
    """Pause a task."""
    task = await get_task(session, task_id, user_id)
    if not task:
        return None
    
    task.status = "paused"
    task.updated_at = _now()
    await session.commit()
    await session.refresh(task)
    
    logger.info(f"Paused task {task_id}")
    return task


async def resume_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: str,
) -> Optional[AgentTask]:
    """Resume a paused task."""
    task = await get_task(session, task_id, user_id)
    if not task:
        return None
    
    if task.status != "paused":
        return task
    
    task.status = "active"
    task.updated_at = _now()
    
    # Recalculate next run time
    if task.trigger_type == "cron":
        cron = task.trigger_config.get("cron")
        if cron:
            task.next_run_at = _calculate_next_run(cron)
    
    await session.commit()
    await session.refresh(task)
    
    logger.info(f"Resumed task {task_id}")
    return task


async def refresh_delegation_token(
    session: AsyncSession,
    task_id: uuid.UUID,
    principal: Principal,
) -> Optional[AgentTask]:
    """
    Refresh the delegation token for a task.
    
    Creates a new long-lived token (3 years) with the task's configured scopes
    using the proper /oauth/delegation endpoint.
    
    Args:
        session: Database session
        task_id: Task UUID
        principal: Authenticated user (task owner)
        
    Returns:
        Updated task with new delegation token, or None if task not found
    """
    task = await get_task(session, task_id, principal.sub)
    if not task:
        return None
    
    if not principal.token:
        raise ValueError("Principal must have a token to refresh delegation")
    
    # Get the scopes from the task
    scopes = task.delegation_scopes or []
    
    try:
        # Create a new proper delegation token via authz /oauth/delegation endpoint
        token_response = await create_delegation_token(
            subject_token=principal.token,
            name=f"Task: {task.name}",
            scopes=scopes,
            # Default 3 years (94608000 seconds)
        )
        
        task.delegation_token = token_response.access_token
        delegation_expires_at = token_response.expires_at
        if delegation_expires_at and delegation_expires_at.tzinfo:
            delegation_expires_at = delegation_expires_at.replace(tzinfo=None)
        task.delegation_expires_at = delegation_expires_at
        task.updated_at = _now()
        
        await session.commit()
        await session.refresh(task)
        
        logger.info(f"Refreshed delegation token for task {task_id}, expires at {task.delegation_expires_at}")
        return task
        
    except Exception as e:
        logger.error(f"Failed to refresh delegation token for task {task_id}: {e}")
        raise


async def create_task_execution(
    session: AsyncSession,
    task: AgentTask,
    trigger_source: str,
    input_data: Optional[Dict[str, Any]] = None,
) -> TaskExecution:
    """
    Create a new task execution record.
    
    Args:
        session: Database session
        task: Parent task
        trigger_source: How the execution was triggered
        input_data: Optional input override
        
    Returns:
        Created TaskExecution
    """
    execution = TaskExecution(
        task_id=task.id,
        trigger_source=trigger_source,
        status="pending",
        input_data=input_data or {"prompt": task.prompt, **task.input_config},
        started_at=_now(),
    )
    
    session.add(execution)
    await session.commit()
    await session.refresh(execution)
    
    return execution


async def update_task_execution(
    session: AsyncSession,
    execution_id: uuid.UUID,
    status: str,
    run_id: Optional[uuid.UUID] = None,
    output_data: Optional[Dict[str, Any]] = None,
    output_summary: Optional[str] = None,
    error: Optional[str] = None,
) -> Optional[TaskExecution]:
    """
    Update a task execution.
    
    Args:
        session: Database session
        execution_id: Execution UUID
        status: New status
        run_id: Associated run record ID
        output_data: Execution output
        output_summary: Summary for notifications
        error: Error message if failed
        
    Returns:
        Updated TaskExecution
    """
    result = await session.execute(
        select(TaskExecution).where(TaskExecution.id == execution_id)
    )
    execution = result.scalar_one_or_none()
    
    if not execution:
        return None
    
    execution.status = status
    if run_id:
        execution.run_id = run_id
    if output_data:
        execution.output_data = output_data
    if output_summary:
        execution.output_summary = output_summary
    if error:
        execution.error = error
    
    if status in ("completed", "failed", "timeout", "stopped"):
        execution.completed_at = _now()
        if execution.started_at:
            execution.duration_seconds = (
                execution.completed_at - execution.started_at
            ).total_seconds()
    
    execution.updated_at = _now()
    await session.commit()
    await session.refresh(execution)
    
    return execution


async def stop_task_execution(
    session: AsyncSession,
    execution_id: uuid.UUID,
    task_id: uuid.UUID,
    user_id: str,
) -> Optional[TaskExecution]:
    """
    Stop a running or pending task execution.
    
    If the execution has a linked workflow execution, that is stopped too.
    
    Args:
        session: Database session
        execution_id: Execution UUID
        task_id: Parent task UUID (for access control)
        user_id: User ID for access control
        
    Returns:
        Updated TaskExecution, or None if not found
        
    Raises:
        ValueError: If execution cannot be stopped (already in terminal state)
    """
    from app.models.domain import WorkflowExecution
    
    # Verify task ownership
    task = await get_task(session, task_id, user_id)
    if not task:
        return None
    
    # Get the execution
    result = await session.execute(
        select(TaskExecution).where(
            TaskExecution.id == execution_id,
            TaskExecution.task_id == task_id,
        )
    )
    execution = result.scalar_one_or_none()
    
    if not execution:
        return None
    
    # Only allow stopping non-terminal executions
    terminal = {"completed", "failed", "timeout", "stopped", "succeeded"}
    if execution.status in terminal:
        raise ValueError(
            f"Cannot stop execution with status '{execution.status}'. "
            f"Only pending or running executions can be stopped."
        )
    
    # If there's a linked workflow execution, stop it too
    wf_exec_id = (execution.output_data or {}).get("workflow_execution_id")
    if wf_exec_id:
        try:
            wf_exec = await session.get(WorkflowExecution, uuid.UUID(wf_exec_id))
            if wf_exec and wf_exec.status in ("pending", "running", "awaiting_human"):
                wf_exec.status = "stopped"
                wf_exec.error = "Stopped by user"
                wf_exec.completed_at = _now()
                if wf_exec.started_at:
                    wf_exec.duration_seconds = (
                        wf_exec.completed_at - wf_exec.started_at
                    ).total_seconds()
                logger.info(f"Stopped linked workflow execution {wf_exec_id}")
        except Exception as e:
            logger.warning(f"Failed to stop linked workflow execution {wf_exec_id}: {e}")
    
    # Stop the task execution
    execution.status = "stopped"
    execution.error = "Stopped by user"
    execution.completed_at = _now()
    if execution.started_at:
        execution.duration_seconds = (
            execution.completed_at - execution.started_at
        ).total_seconds()
    execution.updated_at = _now()
    
    await session.commit()
    await session.refresh(execution)
    
    logger.info(f"Stopped task execution {execution_id}")
    return execution


async def delete_task_execution(
    session: AsyncSession,
    execution_id: uuid.UUID,
    task_id: uuid.UUID,
    user_id: str,
) -> bool:
    """
    Delete a task execution record.
    
    Only allows deletion of executions in terminal states (failed, stopped, timeout, completed).
    Also cleans up linked workflow execution and its step executions.
    
    Args:
        session: Database session
        execution_id: Execution UUID
        task_id: Parent task UUID (for access control)
        user_id: User ID for access control
        
    Returns:
        True if deleted, False if not found
        
    Raises:
        ValueError: If execution is still running and cannot be deleted
    """
    from app.models.domain import WorkflowExecution, StepExecution, TaskNotification
    
    # Verify task ownership
    task = await get_task(session, task_id, user_id)
    if not task:
        return False
    
    # Get the execution
    result = await session.execute(
        select(TaskExecution).where(
            TaskExecution.id == execution_id,
            TaskExecution.task_id == task_id,
        )
    )
    execution = result.scalar_one_or_none()
    
    if not execution:
        return False
    
    # Only allow deletion of terminal executions
    terminal = {"completed", "failed", "timeout", "stopped", "succeeded"}
    if execution.status not in terminal:
        raise ValueError(
            f"Cannot delete execution with status '{execution.status}'. "
            f"Stop the execution first, then delete it."
        )
    
    # Clean up linked workflow execution and its steps
    wf_exec_id = (execution.output_data or {}).get("workflow_execution_id")
    if wf_exec_id:
        try:
            wf_uuid = uuid.UUID(wf_exec_id)
            # Delete step executions
            step_result = await session.execute(
                select(StepExecution).where(StepExecution.execution_id == wf_uuid)
            )
            for step in step_result.scalars().all():
                await session.delete(step)
            
            # Delete workflow execution
            wf_exec = await session.get(WorkflowExecution, wf_uuid)
            if wf_exec:
                await session.delete(wf_exec)
                logger.info(f"Deleted linked workflow execution {wf_exec_id}")
        except Exception as e:
            logger.warning(f"Failed to delete linked workflow execution {wf_exec_id}: {e}")
    
    # Delete linked notifications
    try:
        notif_result = await session.execute(
            select(TaskNotification).where(TaskNotification.execution_id == execution_id)
        )
        for notif in notif_result.scalars().all():
            await session.delete(notif)
    except Exception as e:
        logger.warning(f"Failed to delete linked notifications: {e}")
    
    # Delete the task execution
    await session.delete(execution)
    await session.commit()
    
    logger.info(f"Deleted task execution {execution_id}")
    return True


async def mark_notification_sent(
    session: AsyncSession,
    execution_id: uuid.UUID,
    success: bool,
    error: Optional[str] = None,
) -> None:
    """Mark notification as sent for an execution."""
    await session.execute(
        update(TaskExecution)
        .where(TaskExecution.id == execution_id)
        .values(
            notification_sent=success,
            notification_error=error,
            updated_at=_now(),
        )
    )
    await session.commit()


async def update_task_after_execution(
    session: AsyncSession,
    task_id: uuid.UUID,
    execution: TaskExecution,
    success: bool,
) -> None:
    """
    Update task record after an execution completes.
    
    Args:
        session: Database session
        task_id: Task UUID
        execution: Completed execution
        success: Whether execution succeeded
    """
    result = await session.execute(
        select(AgentTask).where(AgentTask.id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        return
    
    task.last_run_at = execution.completed_at or _now()
    task.last_run_id = execution.run_id
    task.run_count = (task.run_count or 0) + 1
    
    if not success:
        task.error_count = (task.error_count or 0) + 1
        task.last_error = execution.error
    
    # Calculate next run time for cron tasks
    if task.trigger_type == "cron" and task.status == "active":
        cron = task.trigger_config.get("cron")
        if cron:
            task.next_run_at = _calculate_next_run(cron)
    elif task.trigger_type == "one_time":
        # One-time tasks complete after execution
        task.status = "completed"
        task.next_run_at = None
    
    task.updated_at = _now()
    await session.commit()


async def list_task_executions(
    session: AsyncSession,
    task_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
) -> List[TaskExecution]:
    """List executions for a task."""
    stmt = (
        select(TaskExecution)
        .where(TaskExecution.task_id == task_id)
        .order_by(TaskExecution.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_tasks_due_for_execution(
    session: AsyncSession,
    limit: int = 100,
) -> List[AgentTask]:
    """
    Get tasks that are due for execution.
    
    Returns active cron tasks whose next_run_at is in the past.
    """
    now = _now()
    
    stmt = (
        select(AgentTask)
        .where(
            and_(
                AgentTask.status == "active",
                AgentTask.trigger_type.in_(["cron", "one_time"]),
                AgentTask.next_run_at <= now,
            )
        )
        .order_by(AgentTask.next_run_at.asc())
        .limit(limit)
    )
    
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_task_by_webhook_secret(
    session: AsyncSession,
    task_id: uuid.UUID,
    webhook_secret: str,
) -> Optional[AgentTask]:
    """
    Get a task by ID and webhook secret for webhook validation.
    
    Args:
        session: Database session
        task_id: Task UUID
        webhook_secret: Secret from webhook request
        
    Returns:
        AgentTask if valid, None otherwise
    """
    stmt = select(AgentTask).where(
        and_(
            AgentTask.id == task_id,
            AgentTask.trigger_type == "webhook",
            AgentTask.webhook_secret == webhook_secret,
            AgentTask.status == "active",
        )
    )
    
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _calculate_next_run(cron_expression: str) -> Optional[datetime]:
    """
    Calculate the next run time from a cron expression.
    
    Args:
        cron_expression: 5-field cron expression
        
    Returns:
        Next run datetime (naive UTC) or None if invalid
    """
    try:
        from croniter import croniter
        
        base = _now()
        cron = croniter(cron_expression, base)
        next_time = cron.get_next(datetime)
        
        # Ensure timezone-naive
        if next_time.tzinfo:
            next_time = next_time.replace(tzinfo=None)
        
        return next_time
    except Exception as e:
        logger.warning(f"Failed to calculate next run for cron '{cron_expression}': {e}")
        return None


def task_to_read(
    task: AgentTask,
    base_url: Optional[str] = None,
    include_secret: bool = False,
) -> TaskRead:
    """
    Convert AgentTask model to TaskRead schema.
    
    Args:
        task: AgentTask model
        base_url: Base URL for webhook URL generation
        include_secret: Whether to include the webhook secret (only on creation)
        
    Returns:
        TaskRead schema
    """
    # Generate webhook URL for webhook tasks
    webhook_url = None
    if task.trigger_type == "webhook" and base_url:
        webhook_url = f"{base_url}/api/webhooks/tasks/{task.id}"
    
    return TaskRead(
        id=task.id,
        name=task.name,
        description=task.description,
        user_id=task.user_id,
        agent_id=task.agent_id,
        workflow_id=task.workflow_id,
        prompt=task.prompt,
        trigger_type=task.trigger_type,
        trigger_config=task.trigger_config or {},
        delegation_scopes=task.delegation_scopes or [],
        delegation_expires_at=task.delegation_expires_at,
        notification_config=task.notification_config or {},
        insights_config=task.insights_config or {},
        output_saving_config=task.output_saving_config,
        input_config=task.input_config or {},
        status=task.status,
        last_run_at=task.last_run_at,
        last_run_id=task.last_run_id,
        next_run_at=task.next_run_at,
        run_count=task.run_count or 0,
        error_count=task.error_count or 0,
        last_error=task.last_error,
        created_at=task.created_at,
        updated_at=task.updated_at,
        webhook_url=webhook_url,
        webhook_secret=task.webhook_secret if include_secret else None,
    )
