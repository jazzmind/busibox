"""
Webhook API endpoints for Agent Tasks.

Provides webhook receivers for triggering agent tasks from external sources:
- Generic task webhooks (with secret validation)
- Library triggers (from data-worker on document completion)
- Microsoft Teams incoming webhooks
- Slack event subscriptions
- Email webhooks (from providers like SendGrid/Mailgun)
"""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.task_service import (
    create_task_execution,
    get_task_by_webhook_secret,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookPayload(BaseModel):
    """Generic webhook payload."""
    
    event: Optional[str] = Field(None, description="Event type")
    data: Optional[Dict[str, Any]] = Field(None, description="Event data")
    message: Optional[str] = Field(None, description="Message content")


class WebhookResponse(BaseModel):
    """Webhook response."""
    
    success: bool
    message: str
    execution_id: Optional[str] = None


@router.post("/tasks/{task_id}", response_model=WebhookResponse)
async def trigger_task_webhook(
    task_id: uuid.UUID,
    request: Request,
    x_webhook_secret: str = Header(..., alias="X-Webhook-Secret"),
    session: AsyncSession = Depends(get_session),
) -> WebhookResponse:
    """
    Trigger a task via webhook.
    
    The webhook secret must match the task's configured secret.
    The request body is passed as input to the task execution.
    
    Args:
        task_id: Task UUID
        request: HTTP request
        x_webhook_secret: Webhook secret header
        session: Database session
        
    Returns:
        WebhookResponse with execution ID
        
    Raises:
        HTTPException: 401 if secret invalid, 404 if task not found
    """
    # Validate webhook secret
    task = await get_task_by_webhook_secret(session, task_id, x_webhook_secret)
    
    if not task:
        logger.warning(
            f"Invalid webhook request for task {task_id}: invalid secret or task not found"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret or task not found",
        )
    
    # Parse request body
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    # Create execution with webhook data
    execution = await create_task_execution(
        session=session,
        task=task,
        trigger_source="webhook",
        input_data={
            "webhook_payload": body,
            "prompt": task.prompt,
            **task.input_config,
        },
    )
    
    logger.info(
        f"Task {task_id} triggered via webhook, execution {execution.id}",
        extra={
            "task_id": str(task_id),
            "execution_id": str(execution.id),
        },
    )
    
    # Execute the agent task in the background
    asyncio.create_task(
        _execute_task_in_background(
            task=task,
            execution_id=execution.id,
            input_data={
                "webhook_payload": body,
                "prompt": task.prompt,
                **task.input_config,
            },
        )
    )
    
    return WebhookResponse(
        success=True,
        message="Task execution queued",
        execution_id=str(execution.id),
    )


class TeamsWebhookPayload(BaseModel):
    """Microsoft Teams webhook payload."""
    
    type: str = Field(..., description="Activity type")
    text: Optional[str] = Field(None, description="Message text")
    from_: Optional[Dict[str, Any]] = Field(None, alias="from")
    conversation: Optional[Dict[str, Any]] = None
    channelData: Optional[Dict[str, Any]] = None


@router.post("/integrations/teams/{task_id}", response_model=WebhookResponse)
async def teams_webhook(
    task_id: uuid.UUID,
    payload: TeamsWebhookPayload,
    x_webhook_secret: str = Header(..., alias="X-Webhook-Secret"),
    session: AsyncSession = Depends(get_session),
) -> WebhookResponse:
    """
    Receive webhook from Microsoft Teams.
    
    Handles incoming messages from Teams channels or bots.
    The message text is used as additional context for the task.
    
    Args:
        task_id: Task UUID
        payload: Teams webhook payload
        x_webhook_secret: Webhook secret
        session: Database session
        
    Returns:
        WebhookResponse
    """
    # Validate webhook secret
    task = await get_task_by_webhook_secret(session, task_id, x_webhook_secret)
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret or task not found",
        )
    
    # Extract message content
    message_text = payload.text or ""
    from_user = payload.from_.get("name", "Unknown") if payload.from_ else "Unknown"
    
    # Create execution with Teams context
    execution = await create_task_execution(
        session=session,
        task=task,
        trigger_source="teams",
        input_data={
            "teams_message": message_text,
            "teams_from": from_user,
            "prompt": task.prompt,
            **task.input_config,
        },
    )
    
    logger.info(
        f"Task {task_id} triggered via Teams webhook, execution {execution.id}"
    )
    
    return WebhookResponse(
        success=True,
        message="Task execution queued from Teams message",
        execution_id=str(execution.id),
    )


class SlackWebhookPayload(BaseModel):
    """Slack webhook/event payload."""
    
    type: str = Field(..., description="Event type")
    challenge: Optional[str] = Field(None, description="URL verification challenge")
    token: Optional[str] = None
    event: Optional[Dict[str, Any]] = Field(None, description="Event data")
    team_id: Optional[str] = None
    api_app_id: Optional[str] = None


@router.post("/integrations/slack/{task_id}")
async def slack_webhook(
    task_id: uuid.UUID,
    payload: SlackWebhookPayload,
    x_webhook_secret: str = Header(None, alias="X-Webhook-Secret"),
    x_slack_signature: str = Header(None, alias="X-Slack-Signature"),
    session: AsyncSession = Depends(get_session),
):
    """
    Receive webhook from Slack.
    
    Handles:
    - URL verification challenges
    - Event subscriptions (messages, etc.)
    
    Args:
        task_id: Task UUID
        payload: Slack event payload
        x_webhook_secret: Optional webhook secret
        x_slack_signature: Optional Slack signature
        session: Database session
        
    Returns:
        Challenge response or WebhookResponse
    """
    # Handle URL verification challenge
    if payload.type == "url_verification" and payload.challenge:
        return {"challenge": payload.challenge}
    
    # For events, validate and process
    if payload.type == "event_callback" and payload.event:
        # Validate webhook secret if provided
        if x_webhook_secret:
            task = await get_task_by_webhook_secret(session, task_id, x_webhook_secret)
            if not task:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid webhook secret or task not found",
                )
        else:
            # If no secret provided, just look up the task
            from app.services.task_service import get_task
            task = await get_task(session, task_id)
            if not task or task.trigger_type != "webhook":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Task not found",
                )
        
        # Extract event data
        event = payload.event
        event_type = event.get("type", "unknown")
        message_text = event.get("text", "")
        user = event.get("user", "Unknown")
        
        # Create execution with Slack context
        execution = await create_task_execution(
            session=session,
            task=task,
            trigger_source="slack",
            input_data={
                "slack_event_type": event_type,
                "slack_message": message_text,
                "slack_user": user,
                "prompt": task.prompt,
                **task.input_config,
            },
        )
        
        logger.info(
            f"Task {task_id} triggered via Slack webhook, execution {execution.id}"
        )
        
        return WebhookResponse(
            success=True,
            message="Task execution queued from Slack event",
            execution_id=str(execution.id),
        )
    
    # Unknown event type
    return WebhookResponse(
        success=False,
        message=f"Unknown event type: {payload.type}",
    )


class EmailWebhookPayload(BaseModel):
    """Email webhook payload (SendGrid/Mailgun style)."""
    
    # Common fields
    from_email: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    subject: Optional[str] = None
    text: Optional[str] = None
    html: Optional[str] = None
    
    # SendGrid specific
    envelope: Optional[Dict[str, Any]] = None
    headers: Optional[str] = None
    
    # Mailgun specific
    sender: Optional[str] = None
    recipient: Optional[str] = None
    stripped_text: Optional[str] = Field(None, alias="stripped-text")


@router.post("/integrations/email/{task_id}", response_model=WebhookResponse)
async def email_webhook(
    task_id: uuid.UUID,
    payload: EmailWebhookPayload,
    x_webhook_secret: str = Header(..., alias="X-Webhook-Secret"),
    session: AsyncSession = Depends(get_session),
) -> WebhookResponse:
    """
    Receive webhook from email provider (SendGrid, Mailgun, etc.).
    
    Handles incoming emails forwarded via webhooks.
    The email content is used as context for the task.
    
    Args:
        task_id: Task UUID
        payload: Email webhook payload
        x_webhook_secret: Webhook secret
        session: Database session
        
    Returns:
        WebhookResponse
    """
    # Validate webhook secret
    task = await get_task_by_webhook_secret(session, task_id, x_webhook_secret)
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret or task not found",
        )
    
    # Extract email content
    from_email = payload.from_email or payload.sender or "Unknown"
    to_email = payload.to or payload.recipient or "Unknown"
    subject = payload.subject or "No Subject"
    body = payload.stripped_text or payload.text or payload.html or ""
    
    # Create execution with email context
    execution = await create_task_execution(
        session=session,
        task=task,
        trigger_source="email",
        input_data={
            "email_from": from_email,
            "email_to": to_email,
            "email_subject": subject,
            "email_body": body[:5000],  # Limit body size
            "prompt": task.prompt,
            **task.input_config,
        },
    )
    
    logger.info(
        f"Task {task_id} triggered via email webhook, execution {execution.id}"
    )
    
    return WebhookResponse(
        success=True,
        message="Task execution queued from email",
        execution_id=str(execution.id),
    )


class LibraryTriggerPayload(BaseModel):
    """Payload from data-worker when a document completes processing in a library with triggers."""
    
    trigger_id: str = Field(..., description="Library trigger ID")
    agent_id: str = Field(..., description="Agent ID to execute")
    prompt: str = Field(..., description="Full prompt including document content and schema")
    file_id: str = Field(..., description="Completed file ID")
    user_id: str = Field(..., description="File owner's user ID")
    library_id: str = Field(..., description="Library ID")
    schema_document_id: Optional[str] = Field(None, description="Schema data document ID")
    delegation_token: Optional[str] = Field(None, description="Delegation token for auth")


@router.post("/library-trigger", response_model=WebhookResponse)
async def library_trigger_webhook(
    payload: LibraryTriggerPayload,
    session: AsyncSession = Depends(get_session),
) -> WebhookResponse:
    """
    Receive a library trigger from the data-worker.
    
    Called when a document completes processing in a library that has active triggers.
    Executes the configured agent with the document content and extraction schema.
    
    No webhook secret validation is needed since this is an internal service call.
    """
    logger.info(
        f"Library trigger received: trigger={payload.trigger_id}, "
        f"file={payload.file_id}, agent={payload.agent_id}"
    )
    
    try:
        agent_uuid = uuid.UUID(payload.agent_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid agent_id: {payload.agent_id}",
        )
    
    # Execute the agent in the background
    asyncio.create_task(
        _execute_library_trigger(
            agent_id=agent_uuid,
            prompt=payload.prompt,
            user_id=payload.user_id,
            file_id=payload.file_id,
            library_id=payload.library_id,
            trigger_id=payload.trigger_id,
            schema_document_id=payload.schema_document_id,
            delegation_token=payload.delegation_token,
        )
    )
    
    return WebhookResponse(
        success=True,
        message="Library trigger execution started",
        execution_id=payload.trigger_id,
    )


async def _execute_task_in_background(
    task,
    execution_id: uuid.UUID,
    input_data: Dict[str, Any],
) -> None:
    """Execute a task's agent in the background after webhook/continuation trigger.

    Uses create_run_background so that the on_complete callback can detect and
    start any pending continuation queued by trigger_task_run during this run.
    This is what allows the depth-1, depth-2, ... chain to keep flowing —
    without it, each continuation finishes but never kicks off the next one.
    """
    try:
        from app.db.session import SessionLocal
        from app.models.domain import TaskExecution, AgentTask
        from app.services.run_service import create_run_background
        from app.services.task_service import update_task_execution, update_task_after_execution
        from app.schemas.auth import Principal
        from sqlalchemy import select, update as sql_update

        if not task.agent_id:
            logger.warning(f"Task {task.id} has no agent_id, skipping execution")
            return

        # Restore app_id so exchange_token passes resource_id to authz,
        # auto-granting the app:<name> role in the downstream data-api token.
        task_app_id = (task.input_config or {}).get("__app_id__")
        principal = Principal(
            sub=task.user_id,
            scopes=task.delegation_scopes or ["agent.execute"],
            token=task.delegation_token or "",
            app_id=task_app_id,
        )

        # Forward continuation depth from the payload if present
        continuation_depth = input_data.get("_continuation_depth", 0)

        task_obj_id = task.id
        task_exec_id = execution_id

        async def _on_continuation_complete(bg_run_id, bg_status, bg_summary):
            """Mirror of tasks.py _on_bg_complete — updates execution and chains the next pending continuation."""
            from app.db.session import SessionLocal as _SL
            from app.models.domain import TaskExecution as _TE, AgentTask as _AT
            async with _SL() as cb_session:
                mapped_status = "completed" if bg_status in ("succeeded", "completed") else "failed"
                await update_task_execution(
                    session=cb_session,
                    execution_id=task_exec_id,
                    run_id=bg_run_id,
                    status=mapped_status,
                    output_summary=bg_summary,
                )
                exec_result = await cb_session.execute(
                    select(_TE).where(_TE.id == task_exec_id)
                )
                exec_obj = exec_result.scalar_one_or_none()
                if exec_obj:
                    await update_task_after_execution(cb_session, task_obj_id, exec_obj, mapped_status == "completed")

                # Start the next pending continuation (if any) now that this run finished.
                if mapped_status == "completed":
                    pending_cont_stmt = (
                        select(_TE)
                        .where(
                            _TE.task_id == task_obj_id,
                            _TE.trigger_source == "continuation",
                            _TE.status == "pending",
                        )
                        .order_by(_TE.created_at.asc())
                        .limit(1)
                    )
                    pending_result = await cb_session.execute(pending_cont_stmt)
                    pending_exec = pending_result.scalar_one_or_none()
                    if pending_exec:
                        logger.info(
                            "Starting pending continuation from _execute_task_in_background: "
                            "exec_id=%s, task_id=%s",
                            pending_exec.id, task_obj_id,
                        )
                        task_result = await cb_session.execute(
                            select(_AT).where(_AT.id == task_obj_id)
                        )
                        cont_task = task_result.scalar_one_or_none()
                        if cont_task:
                            import asyncio as _asyncio
                            # #region agent log
                            import time as _t2
                            try:
                                import aiohttp as _ah2
                                async def _dbg2():
                                    async with _ah2.ClientSession() as _s2:
                                        await _s2.post('http://127.0.0.1:7251/ingest/606d8d55-f269-4a7e-9f32-b5c818b6655a', json={'sessionId':'fce93e','location':'webhooks.py:_on_continuation_complete','message':'chaining_next_continuation','data':{'task_id':str(task_obj_id),'next_exec_id':str(pending_exec.id),'next_depth':pending_exec.input_data.get('_continuation_depth') if pending_exec.input_data else None},'timestamp':int(_t2.time()*1000),'hypothesisId':'H1'}, headers={'X-Debug-Session-Id':'fce93e'})
                                _asyncio.create_task(_dbg2())
                            except Exception:
                                pass
                            # #endregion agent log
                            _asyncio.create_task(
                                _execute_task_in_background(
                                    task=cont_task,
                                    execution_id=pending_exec.id,
                                    input_data=pending_exec.input_data,
                                )
                            )

        run_record = await create_run_background(
            principal=principal,
            agent_id=task.agent_id,
            payload={
                "prompt": input_data.get("prompt", task.prompt),
                **input_data,
                "_task_id": str(task.id),
                "_execution_id": str(execution_id),
                "_continuation_depth": continuation_depth,
            },
            scopes=task.delegation_scopes or ["agent.execute", "data.write", "data.read", "search.read"],
            purpose="continuation-task",
            agent_tier="complex",
            on_complete=_on_continuation_complete,
        )

        # Update execution record with the pre-created run_id immediately
        async with SessionLocal() as session:
            await session.execute(
                sql_update(TaskExecution)
                .where(TaskExecution.id == execution_id)
                .values(run_id=run_record.id, status="running")
            )
            await session.commit()

        # #region agent log
        import json as _json, time as _time
        try:
            import aiohttp as _aiohttp
            import asyncio as _al
            async def _dbg_log():
                async with _aiohttp.ClientSession() as _s:
                    await _s.post('http://127.0.0.1:7251/ingest/606d8d55-f269-4a7e-9f32-b5c818b6655a', json={'sessionId':'fce93e','location':'webhooks.py:_execute_task_in_background','message':'continuation_started','data':{'task_id':str(task.id),'execution_id':str(execution_id),'run_id':str(run_record.id),'depth':continuation_depth},'timestamp':int(_time.time()*1000),'hypothesisId':'H1'}, headers={'X-Debug-Session-Id':'fce93e'})
            _al.create_task(_dbg_log())
        except Exception:
            pass
        # #endregion agent log
        logger.info(
            f"Continuation task started in background: task={task.id}, "
            f"execution={execution_id}, run={run_record.id}, depth={continuation_depth}"
        )

    except Exception as e:
        logger.error(
            f"Background task execution failed: task={task.id}, error={e}",
            exc_info=True,
        )
        try:
            from app.db.session import SessionLocal
            from app.models.domain import TaskExecution
            from sqlalchemy import update as sql_update

            async with SessionLocal() as session:
                await session.execute(
                    sql_update(TaskExecution)
                    .where(TaskExecution.id == execution_id)
                    .values(status="failed", output_summary=str(e)[:500])
                )
                await session.commit()
        except Exception:
            pass


async def _execute_library_trigger(
    agent_id: uuid.UUID,
    prompt: str,
    user_id: str,
    file_id: str,
    library_id: str,
    trigger_id: str,
    schema_document_id: Optional[str] = None,
    delegation_token: Optional[str] = None,
) -> None:
    """Execute a library trigger's agent in the background."""
    try:
        from app.db.session import SessionLocal
        from app.services.run_service import create_run
        from app.schemas.auth import Principal
        
        # Create a principal from the user_id
        principal = Principal(
            sub=user_id,
            scopes=["agent.execute", "data.write", "data.read", "search.read", "graph.read", "graph.write"],
            token=delegation_token or "",
        )
        
        async with SessionLocal() as session:
            run = await create_run(
                session=session,
                principal=principal,
                agent_id=agent_id,
                payload={
                    "prompt": prompt,
                    "file_id": file_id,
                    "library_id": library_id,
                    "trigger_id": trigger_id,
                    "schema_document_id": schema_document_id,
                },
                scopes=["agent.execute", "data.write", "data.read", "search.read", "graph.read", "graph.write"],
                purpose="library-trigger",
                agent_tier="complex",
            )
            
            logger.info(
                f"Library trigger execution completed: trigger={trigger_id}, "
                f"file={file_id}, agent={agent_id}, run={run.id}, status={run.status}"
            )
    
    except Exception as e:
        logger.error(
            f"Library trigger execution failed: trigger={trigger_id}, "
            f"file={file_id}, error={e}",
            exc_info=True,
        )


@router.get("/health")
async def webhooks_health():
    """Health check for webhook endpoints."""
    return {"status": "healthy", "service": "webhooks"}
