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
    """Execute a task's agent in the background after webhook trigger."""
    try:
        from app.db.session import SessionLocal
        from app.services.run_service import create_run
        from app.schemas.auth import Principal
        
        if not task.agent_id:
            logger.warning(f"Task {task.id} has no agent_id, skipping execution")
            return
        
        # Create a principal from the task's user_id
        principal = Principal(
            sub=task.user_id,
            scopes=task.delegation_scopes or ["agent.execute"],
            token=task.delegation_token or "",
        )
        
        async with SessionLocal() as session:
            run = await create_run(
                session=session,
                principal=principal,
                agent_id=task.agent_id,
                payload={"prompt": input_data.get("prompt", task.prompt), **input_data},
                scopes=task.delegation_scopes or ["agent.execute", "data.write", "data.read", "search.read"],
                purpose="webhook-task",
                agent_tier="complex",
            )
            
            # Update execution with run_id
            from app.models.domain import TaskExecution
            from sqlalchemy import update
            
            await session.execute(
                update(TaskExecution)
                .where(TaskExecution.id == execution_id)
                .values(
                    run_id=run.id,
                    status=run.status or "completed",
                    output_summary=str(run.output)[:500] if run.output else None,
                )
            )
            await session.commit()
            
            logger.info(
                f"Webhook task execution completed: task={task.id}, run={run.id}, status={run.status}"
            )
    
    except Exception as e:
        logger.error(
            f"Background task execution failed: task={task.id}, error={e}",
            exc_info=True,
        )
        # Update execution status to failed
        try:
            from app.db.session import SessionLocal
            from app.models.domain import TaskExecution
            from sqlalchemy import update
            
            async with SessionLocal() as session:
                await session.execute(
                    update(TaskExecution)
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
