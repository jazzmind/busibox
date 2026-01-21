"""
API endpoints for Agent Tasks.

Provides CRUD operations and execution control for event-driven agent tasks.
"""

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
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


async def _save_task_insight(
    task,
    execution,
    output_summary: Optional[str],
    authorization: Optional[str] = None,
) -> Optional[str]:
    """
    Save task execution output as an insight for duplicate detection.
    
    This stores the output summary in Milvus so future executions can
    check for similar results and avoid sending duplicates.
    
    Args:
        task: Task model
        execution: TaskExecution model
        output_summary: The output summary to save
        authorization: Bearer token for embedding generation (optional, will use delegation_token if not provided)
        
    Returns:
        Insight ID if saved, None otherwise
    """
    # Check if insights are enabled for this task
    insights_config = task.insights_config or {}
    if not insights_config.get("enabled", True):
        logger.debug(f"Insights disabled for task {task.id}")
        return None
    
    if not output_summary or len(output_summary.strip()) < 10:
        logger.debug(f"No output summary to save as insight for task {task.id}")
        return None
    
    try:
        from app.api.insights import get_insights_service
        
        insights_service = get_insights_service()
        
        # Get insight limits from config
        max_insights = insights_config.get("max_insights", 50)
        
        # Check current insight count
        current_count = insights_service.get_task_insight_count(
            task_id=str(task.id),
            user_id=task.user_id,
        )
        
        # Purge old insights if we're at the limit
        if current_count >= max_insights:
            purged = insights_service.purge_old_task_insights(
                task_id=str(task.id),
                user_id=task.user_id,
                keep_count=max_insights - 1,  # Make room for new one
            )
            logger.info(f"Purged {purged} old insights for task {task.id}")
        
        # Get an ingest-api audience token via Zero Trust token exchange
        # The delegation token has agent-api audience, we exchange it for ingest-api
        try:
            if not task.delegation_token:
                logger.warning(f"Task {task.id} has no delegation token for insight saving")
                return None
                
            from app.auth.tokens import get_service_token
            ingest_token = await get_service_token(
                user_token=task.delegation_token,
                user_id=task.user_id,
                target_audience="ingest-api",
            )
            access_token = f"Bearer {ingest_token}"
        except Exception as e:
            logger.warning(f"Failed to get ingest-api token for task {task.id}: {e}")
            return None
        
        # Extract the actual content from the output (unwrap JSON/dict, strip code fences)
        extracted_content = _extract_content_from_output(output_summary)
        
        # Insert the new insight
        insight_id = await insights_service.insert_task_insight(
            task_id=str(task.id),
            user_id=task.user_id,
            content=extracted_content,
            execution_id=str(execution.id),
            authorization=access_token,
        )
        
        logger.info(
            f"Saved task insight for task {task.id}, insight_id={insight_id}",
            extra={
                "task_id": str(task.id),
                "execution_id": str(execution.id),
                "insight_id": insight_id,
            }
        )
        
        return insight_id
        
    except Exception as e:
        logger.error(f"Error saving task insight: {e}", exc_info=True)
        return None


async def _save_task_output_to_library(
    task,
    execution,
    output_summary: Optional[str],
    success: bool,
    authorization: Optional[str] = None,
) -> Optional[str]:
    """
    Save task output to the user's personal Tasks library as a document.
    
    This allows task outputs to be searched and referenced later.
    
    Args:
        task: Task model
        execution: TaskExecution model
        output_summary: The output to save
        success: Whether the task succeeded
        authorization: Bearer token for API calls
        
    Returns:
        Document ID if saved, None otherwise
    """
    output_saving_config = task.output_saving_config or {}
    
    # Check if output saving is enabled
    if not output_saving_config.get("enabled", False):
        return None
    
    # Check success-only constraint
    if output_saving_config.get("on_success_only", True) and not success:
        logger.debug(f"Skipping output save for task {task.id} (failed, on_success_only=true)")
        return None
    
    if not output_summary or len(output_summary.strip()) < 10:
        logger.debug(f"No output to save for task {task.id}")
        return None
    
    try:
        from app.clients.busibox import BusiboxClient
        from app.config.settings import get_settings
        from datetime import datetime
        
        settings = get_settings()
        
        # Format the content
        formatted_content = _format_output_for_notification(output_summary)
        
        # Build title from template or default
        title_template = output_saving_config.get("title_template") or "{task_name} - {date}"
        title = title_template.format(
            task_name=task.name,
            date=datetime.now().strftime("%Y-%m-%d %H:%M"),
            status="Success" if success else "Failed",
        )
        
        # Get tags
        tags = output_saving_config.get("tags", [])
        
        # Get an ingest-api audience token via Zero Trust token exchange
        # The delegation token has agent-api audience, we exchange it for ingest-api
        try:
            if not task.delegation_token:
                logger.warning(f"Task {task.id} has no delegation token for output saving")
                return None
                
            from app.auth.tokens import get_service_token
            access_token = await get_service_token(
                user_token=task.delegation_token,
                user_id=task.user_id,
                target_audience="ingest-api",
            )
        except Exception as e:
            logger.warning(f"Failed to get ingest-api token for task {task.id} output saving: {e}")
            return None
        
        # Use the ingest content API via BusiboxClient
        client = BusiboxClient(access_token=access_token)
        
        # Call the ingest content endpoint with folder="personal-tasks"
        result = await client.ingest_content(
            content=formatted_content,
            title=title,
            folder="personal-tasks",  # This will be resolved to the Tasks library
            metadata={
                "task_id": str(task.id),
                "task_name": task.name,
                "execution_id": str(execution.id),
                "success": success,
                "tags": tags,
                "source": "task-output",
            },
        )
        
        document_id = result.get("document_id") or result.get("id")
        
        logger.info(
            f"Saved task output to library for task {task.id}",
            extra={
                "task_id": str(task.id),
                "document_id": document_id,
                "tags": tags,
            }
        )
        
        return document_id
        
    except Exception as e:
        logger.error(f"Error saving task output to library: {e}", exc_info=True)
        return None


def _extract_content_from_output(output_summary: Optional[str]) -> str:
    """
    Extract the actual content from an output summary, handling dict-like strings
    and stripping markdown code fences.
    
    Handles cases where the output is:
    - A dict-like string: "{'result': '...'}" 
    - JSON: '{"result": "..."}'
    - Markdown with code fences: "```markdown\n...\n```"
    
    Returns clean markdown content suitable for storage.
    """
    if not output_summary:
        return ""
    
    content = output_summary
    
    # Try to parse as dict if it looks like one
    if content.startswith("{") and content.endswith("}"):
        try:
            import ast
            parsed = ast.literal_eval(content)
            if isinstance(parsed, dict):
                # Extract the result content
                result_content = parsed.get("result") or parsed.get("summary") or parsed.get("output")
                if result_content:
                    content = str(result_content)
                else:
                    # If no standard keys, format as readable key-value pairs
                    formatted_parts = []
                    for key, value in parsed.items():
                        if isinstance(value, str) and len(value) > 500:
                            value = value[:500] + "..."
                        formatted_parts.append(f"**{key}:** {value}")
                    content = "\n".join(formatted_parts)
        except (ValueError, SyntaxError):
            # Not a valid dict string, try JSON
            pass
    
    # Also try JSON parsing
    if content.startswith("{") or content.startswith("["):
        try:
            import json
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                result_content = parsed.get("result") or parsed.get("summary") or parsed.get("output")
                if result_content:
                    content = str(result_content)
        except json.JSONDecodeError:
            pass
    
    # Strip markdown code fences if present (e.g., ```markdown\n...\n```)
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        # Remove first line (```markdown or ```)
        if lines:
            lines = lines[1:]
        # Remove last line if it's just ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)
    
    return content.strip()


def _format_output_for_notification(output_summary: Optional[str]) -> str:
    """
    Format output summary for notification display.
    
    Wrapper around _extract_content_from_output for backwards compatibility.
    """
    return _extract_content_from_output(output_summary)


async def _send_task_notification(
    session: AsyncSession,
    task,
    execution,
    success: bool,
    output_summary: Optional[str],
) -> None:
    """
    Send notification for task completion (manual or scheduled).
    
    This is a helper function used by both manual execution and scheduled execution.
    Supports multiple notification channels if configured.
    """
    from app.tools.notification_tool import send_notification
    from app.models.domain import TaskNotification
    from app.config.settings import get_settings
    
    notification_config = task.notification_config or {}
    
    # Check if notifications are enabled
    if not notification_config.get("enabled"):
        return
    
    # Check notification triggers
    notify_on_success = notification_config.get("on_success", True)
    notify_on_failure = notification_config.get("on_failure", True)
    
    if success and not notify_on_success:
        logger.debug(f"Skipping success notification for task {task.id} (disabled)")
        return
    if not success and not notify_on_failure:
        logger.debug(f"Skipping failure notification for task {task.id} (disabled)")
        return
    
    # Build notification content
    status_emoji = "✅" if success else "❌"
    status_text = "succeeded" if success else "failed"
    
    subject = f"{status_emoji} Task '{task.name}' {status_text}"
    
    body_parts = [
        f"**Task:** {task.name}",
        f"**Status:** {status_text.upper()}",
        f"**Executed at:** {execution.started_at.isoformat() if execution.started_at else 'N/A'}",
    ]
    
    if output_summary:
        # Format the output for better readability (parse dicts, extract result content)
        formatted_output = _format_output_for_notification(output_summary)
        summary_preview = formatted_output[:500] + "..." if len(formatted_output) > 500 else formatted_output
        body_parts.append(f"\n**Result:**\n{summary_preview}")
    
    if not success and execution.error:
        body_parts.append(f"\n**Error:**\n{execution.error}")
    
    body = "\n".join(body_parts)
    
    # Portal link
    settings = get_settings()
    portal_base = settings.portal_base_url or "https://localhost"
    portal_link = f"{portal_base}/agents/tasks/{task.id}"
    
    # Get all configured channels - support both single channel (legacy) and multiple channels
    channels_to_notify = []
    
    # Check for new multi-channel format: notification_config.channels = [{channel, recipient}, ...]
    if notification_config.get("channels"):
        for ch in notification_config["channels"]:
            if ch.get("enabled", True) and ch.get("recipient"):
                channels_to_notify.append({
                    "channel": ch.get("channel", "email"),
                    "recipient": ch["recipient"],
                })
    
    # Fallback to legacy single-channel format
    if not channels_to_notify and notification_config.get("recipient"):
        channels_to_notify.append({
            "channel": notification_config.get("channel", "email"),
            "recipient": notification_config["recipient"],
        })
    
    if not channels_to_notify:
        logger.warning(f"Task {task.id} has notifications enabled but no valid channels configured")
        return
    
    # Send to all configured channels
    for ch_config in channels_to_notify:
        channel = ch_config["channel"]
        recipient = ch_config["recipient"]
        
        # Try to create notification record
        notification = None
        try:
            notification = TaskNotification(
                task_id=task.id,
                execution_id=execution.id,
                channel=channel,
                recipient=recipient,
                subject=subject,
                body=body,
                status="pending",
            )
            session.add(notification)
            await session.flush()
        except Exception as e:
            logger.warning(f"Could not create notification record: {e}")
            # Continue anyway - we can still send the notification
        
        try:
            result = await send_notification(
                channel=channel,
                recipient=recipient,
                subject=subject,
                body=body,
                portal_link=portal_link,
                metadata={
                    "task_id": str(task.id),
                    "execution_id": str(execution.id),
                    "success": success,
                },
            )
            
            if notification:
                notification.status = "sent" if result.success else "failed"
                notification.message_id = result.message_id
                notification.sent_at = notification.sent_at or (
                    __import__("datetime").datetime.utcnow() if result.success else None
                )
                notification.error = result.error
            
            if result.success:
                logger.info(f"Sent {channel} notification to {recipient} for task {task.id}")
            else:
                logger.error(f"Failed to send {channel} notification to {recipient}: {result.error}")
        
        except Exception as e:
            logger.error(f"Error sending {channel} notification: {e}", exc_info=True)
            if notification:
                notification.status = "failed"
                notification.error = str(e)
    
    # Commit all notification records to the database
    try:
        await session.commit()
    except Exception as e:
        logger.error(f"Failed to commit notification records: {e}", exc_info=True)


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


@router.post("/{task_id}/refresh-token", response_model=TaskRead)
async def refresh_task_delegation_token(
    task_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TaskRead:
    """
    Refresh the delegation token for a task.
    
    This creates a new long-lived token (3 years) with the task's configured scopes.
    Use this to extend the token before it expires or to refresh permissions.
    
    Args:
        task_id: Task UUID
        request: HTTP request for URL generation
        principal: Authenticated user (must be task owner)
        session: Database session
        
    Returns:
        Updated task with new delegation token expiry
        
    Raises:
        HTTPException: 404 if task not found, 500 if token refresh fails
    """
    from app.services.task_service import refresh_delegation_token
    
    try:
        task = await refresh_delegation_token(session, task_id, principal)
        
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found",
            )
        
        logger.info(f"Refreshed delegation token for task {task_id}")
        
        base_url = _get_base_url(request)
        return task_to_read(task, base_url)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to refresh delegation token: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to refresh delegation token: {str(e)}",
        )


@router.post("/{task_id}/run", response_model=TaskRunResponse)
async def run_agent_task(
    task_id: uuid.UUID,
    run_request: Optional[TaskRunRequest] = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    background_tasks: "BackgroundTasks" = None,
) -> TaskRunResponse:
    """
    Manually trigger a task execution.
    
    This creates a new execution regardless of the task's schedule.
    
    Args:
        task_id: Task UUID
        run_request: Optional input overrides
        principal: Authenticated user
        session: Database session
        background_tasks: FastAPI background tasks
        
    Returns:
        Execution details with status
    """
    from app.services.run_service import create_run
    from app.services.task_service import update_task_execution, update_task_after_execution
    
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
    
    # Validate task has an agent_id or workflow_id
    if not task.agent_id and not task.workflow_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task has no agent or workflow configured. Please edit the task to assign one.",
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
            "has_agent": bool(task.agent_id),
            "has_workflow": bool(task.workflow_id),
        },
    )
    
    # Build the payload from task configuration with optional overrides
    payload = {
        "prompt": task.prompt,
        **(task.input_config or {}),
        **(input_data or {}),
    }
    
    # For workflows, also include 'query' mapped from 'prompt' for compatibility
    # with workflows that expect $.input.query (like web-research-workflow)
    if task.workflow_id and "query" not in payload:
        payload["query"] = task.prompt
    
    try:
        if task.workflow_id:
            # Execute workflow
            from app.workflows.enhanced_engine import create_workflow_execution, run_workflow_execution
            
            # Create workflow execution record
            workflow_execution = await create_workflow_execution(
                session=session,
                principal=principal,
                workflow_id=task.workflow_id,
                input_data=payload,
            )
            
            # Run the workflow (synchronously for manual execution)
            workflow_execution = await run_workflow_execution(
                execution_id=workflow_execution.id,
                principal=principal,
                scopes=task.delegation_scopes or [],
                purpose="task-manual-execution",
            )
            
            # Update execution with workflow result
            # WorkflowExecution stores outputs in step_outputs dict
            output_summary = None
            if workflow_execution.step_outputs:
                # Get the last step's output or synthesize step output
                last_output = None
                if isinstance(workflow_execution.step_outputs, dict):
                    # Try to get synthesize step output, or last step output
                    last_output = workflow_execution.step_outputs.get("synthesize") or \
                                  workflow_execution.step_outputs.get("result") or \
                                  list(workflow_execution.step_outputs.values())[-1] if workflow_execution.step_outputs else None
                if last_output:
                    if isinstance(last_output, dict):
                        output_summary = last_output.get("summary") or last_output.get("result") or str(last_output)[:500]
                    else:
                        output_summary = str(last_output)[:500]
            
            success = workflow_execution.status in ("completed", "succeeded")
            
            # Don't set run_id for workflow executions - it has FK to run_records
            # Store workflow execution ID in output_data instead
            await update_task_execution(
                session=session,
                execution_id=execution.id,
                run_id=None,  # No run_id for workflow executions
                status=workflow_execution.status,
                output_summary=output_summary,
                error=workflow_execution.error if not success else None,
                output_data={
                    "workflow_execution_id": str(workflow_execution.id),
                    "step_outputs": workflow_execution.step_outputs,
                },
            )
            
            await update_task_after_execution(
                session=session,
                task_id=task_id,
                execution=execution,
                success=success,
            )
            
            # Send notification for workflow execution
            await _send_task_notification(
                session=session,
                task=task,
                execution=execution,
                success=success,
                output_summary=output_summary,
            )
            
            # Save insight from execution output (for duplicate detection)
            if success and output_summary:
                await _save_task_insight(
                    task=task,
                    execution=execution,
                    output_summary=output_summary,
                    authorization=principal.token,  # Use the caller's token for fresh auth
                )
            
            # Save output to library if configured
            await _save_task_output_to_library(
                task=task,
                execution=execution,
                output_summary=output_summary,
                success=success,
                authorization=principal.token,  # Use the caller's token for fresh auth
            )
            
            return TaskRunResponse(
                execution_id=execution.id,
                task_id=task_id,
                run_id=workflow_execution.id,
                status=workflow_execution.status,
                message=f"Workflow execution {workflow_execution.status}",
            )
        else:
            # Execute agent
            run_record = await create_run(
                session=session,
                principal=principal,
                agent_id=task.agent_id,
                payload=payload,
                scopes=task.delegation_scopes or [],
                purpose="task-manual-execution",
                agent_tier="complex",  # Tasks use complex tier (10 min timeout) for LLM processing
            )
            
            # Update execution with run result
            output_summary = None
            if run_record.output:
                if isinstance(run_record.output, dict):
                    output_summary = run_record.output.get("summary") or str(run_record.output)[:500]
                else:
                    output_summary = str(run_record.output)[:500]
            
            success = run_record.status in ("completed", "succeeded")
            
            await update_task_execution(
                session=session,
                execution_id=execution.id,
                run_id=run_record.id,
                status=run_record.status,
                output_summary=output_summary,
                error=run_record.output.get("error") if isinstance(run_record.output, dict) and not success else None,
            )
            
            await update_task_after_execution(
                session=session,
                task_id=task_id,
                execution=execution,
                success=success,
            )
            
            # Send notification for agent execution
            await _send_task_notification(
                session=session,
                task=task,
                execution=execution,
                success=success,
                output_summary=output_summary,
            )
            
            # Save insight from execution output (for duplicate detection)
            if success and output_summary:
                await _save_task_insight(
                    task=task,
                    execution=execution,
                    output_summary=output_summary,
                    authorization=principal.token,  # Use the caller's token for fresh auth
                )
            
            # Save output to library if configured
            await _save_task_output_to_library(
                task=task,
                execution=execution,
                output_summary=output_summary,
                success=success,
                authorization=principal.token,  # Use the caller's token for fresh auth
            )

            return TaskRunResponse(
                execution_id=execution.id,
                task_id=task_id,
                run_id=run_record.id,
                status=run_record.status,
                message=f"Task execution {run_record.status}",
            )
        
    except Exception as e:
        logger.error(f"Manual task execution failed: {e}", exc_info=True)
        
        await update_task_execution(
            session=session,
            execution_id=execution.id,
            status="failed",
            error=str(e),
        )
        
        await update_task_after_execution(
            session=session,
            task_id=task_id,
            execution=execution,
            success=False,
        )
        
        # Send failure notification
        await _send_task_notification(
            session=session,
            task=task,
            execution=execution,
            success=False,
            output_summary=str(e),
        )
        
        return TaskRunResponse(
            execution_id=execution.id,
            task_id=task_id,
            run_id=None,
            status="failed",
            message=f"Task execution failed: {str(e)}",
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
    limit: int = Query(50, ge=1, le=100, description="Max results"),
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
    
    # Get insights from Milvus via InsightsService
    try:
        from app.api.insights import get_insights_service
        
        insights_service = get_insights_service()
        insights = insights_service.get_task_insights(
            task_id=str(task_id),
            user_id=principal.sub,
            limit=limit,
        )
        
        return {
            "task_id": str(task_id),
            "insights": insights,
            "count": len(insights),
        }
    except Exception as e:
        logger.error(f"Error retrieving task insights: {e}", exc_info=True)
        # Return empty list on error, don't fail the request
        return {
            "task_id": str(task_id),
            "insights": [],
            "count": 0,
            "error": str(e),
        }


@router.get("/{task_id}/notifications")
async def get_task_notifications(
    task_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    """
    Get notification history for a task.
    
    Returns all notifications sent for this task across all executions,
    including delivery status, timestamps, and any errors.
    
    Args:
        task_id: Task UUID
        limit: Maximum number of notifications
        offset: Pagination offset
        principal: Authenticated user
        session: Database session
        
    Returns:
        List of notifications with delivery status
    """
    from sqlalchemy import select
    from app.models.domain import TaskNotification
    
    # Verify task exists and user has access
    task = await get_task(session, task_id, principal.sub)
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    
    # Get notifications
    stmt = (
        select(TaskNotification)
        .where(TaskNotification.task_id == task_id)
        .order_by(TaskNotification.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    
    result = await session.execute(stmt)
    notifications = result.scalars().all()
    
    return {
        "task_id": str(task_id),
        "notifications": [
            {
                "id": str(n.id),
                "execution_id": str(n.execution_id),
                "channel": n.channel,
                "recipient": n.recipient,
                "subject": n.subject,
                "status": n.status,
                "message_id": n.message_id,
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
                "delivered_at": n.delivered_at.isoformat() if n.delivered_at else None,
                "read_at": n.read_at.isoformat() if n.read_at else None,
                "error": n.error,
                "retry_count": n.retry_count,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
        "count": len(notifications),
    }
