"""
Webhook Sender Service for Agent Notifications.

Provides functions to send messages to various webhook endpoints:
- Microsoft Teams (Adaptive Cards)
- Slack (Block Kit)
- Generic webhooks (JSON payload)
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class WebhookResult(BaseModel):
    """Result of webhook send operation."""
    
    success: bool = Field(..., description="Whether webhook was sent successfully")
    message_id: Optional[str] = Field(None, description="Message ID if available")
    status_code: Optional[int] = Field(None, description="HTTP status code")
    error: Optional[str] = Field(None, description="Error message if failed")


async def send_teams_message(
    webhook_url: str,
    title: str,
    body: str,
    action_url: Optional[str] = None,
    action_text: Optional[str] = None,
    color: str = "0078D4",  # Microsoft blue
    facts: Optional[List[Dict[str, str]]] = None,
) -> WebhookResult:
    """
    Send a message to Microsoft Teams via incoming webhook.
    
    Uses the Adaptive Card format for rich messaging.
    
    Args:
        webhook_url: Teams incoming webhook URL
        title: Card title
        body: Card body text
        action_url: Optional action button URL
        action_text: Optional action button text
        color: Card accent color (hex without #)
        facts: Optional list of fact key-value pairs
        
    Returns:
        WebhookResult with success status
    """
    logger.info(f"Sending Teams message to webhook", extra={"title": title})
    
    # Build Adaptive Card
    card_body = [
        {
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": body,
            "wrap": True,
            "spacing": "Medium",
        },
    ]
    
    # Add facts if provided
    if facts:
        fact_set = {
            "type": "FactSet",
            "facts": [
                {"title": f["title"], "value": f["value"]}
                for f in facts
            ],
            "spacing": "Medium",
        }
        card_body.append(fact_set)
    
    # Build actions if provided
    actions = []
    if action_url:
        actions.append({
            "type": "Action.OpenUrl",
            "title": action_text or "View Details",
            "url": action_url,
        })
    
    # Build Adaptive Card payload
    adaptive_card = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": card_body,
                    "actions": actions if actions else None,
                    "msteams": {
                        "width": "Full",
                    },
                },
            }
        ],
    }
    
    # Remove None actions
    if not actions:
        del adaptive_card["attachments"][0]["content"]["actions"]
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json=adaptive_card,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            
            if response.status_code == 200:
                message_id = str(uuid.uuid4())  # Teams doesn't return a message ID
                logger.info(f"Teams message sent successfully")
                return WebhookResult(
                    success=True,
                    message_id=message_id,
                    status_code=response.status_code,
                )
            else:
                error = f"Teams webhook error: {response.status_code} - {response.text}"
                logger.error(error)
                return WebhookResult(
                    success=False,
                    status_code=response.status_code,
                    error=error,
                )
    
    except httpx.RequestError as e:
        error = f"Teams webhook request failed: {str(e)}"
        logger.error(error)
        return WebhookResult(success=False, error=error)


async def send_slack_message(
    webhook_url: str,
    title: str,
    body: str,
    action_url: Optional[str] = None,
    action_text: Optional[str] = None,
    color: str = "#36a64f",  # Slack green
    fields: Optional[List[Dict[str, str]]] = None,
) -> WebhookResult:
    """
    Send a message to Slack via incoming webhook.
    
    Uses Block Kit for rich messaging.
    
    Args:
        webhook_url: Slack incoming webhook URL
        title: Message title
        body: Message body text
        action_url: Optional action button URL
        action_text: Optional action button text
        color: Attachment color (hex with #)
        fields: Optional list of field key-value pairs
        
    Returns:
        WebhookResult with success status
    """
    logger.info(f"Sending Slack message to webhook", extra={"title": title})
    
    # Build blocks
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": title,
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": body,
            },
        },
    ]
    
    # Add fields if provided
    if fields:
        field_block = {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*{f['title']}*\n{f['value']}",
                }
                for f in fields
            ],
        }
        blocks.append(field_block)
    
    # Add action button if provided
    if action_url:
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": action_text or "View Details",
                        "emoji": True,
                    },
                    "url": action_url,
                    "action_id": "view_details",
                },
            ],
        })
    
    # Add divider at end
    blocks.append({"type": "divider"})
    
    # Build Slack payload
    payload = {
        "blocks": blocks,
        "attachments": [
            {
                "color": color,
                "fallback": f"{title}: {body}",
            }
        ],
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            
            if response.status_code == 200 and response.text == "ok":
                message_id = str(uuid.uuid4())  # Slack webhook doesn't return message ID
                logger.info(f"Slack message sent successfully")
                return WebhookResult(
                    success=True,
                    message_id=message_id,
                    status_code=response.status_code,
                )
            else:
                error = f"Slack webhook error: {response.status_code} - {response.text}"
                logger.error(error)
                return WebhookResult(
                    success=False,
                    status_code=response.status_code,
                    error=error,
                )
    
    except httpx.RequestError as e:
        error = f"Slack webhook request failed: {str(e)}"
        logger.error(error)
        return WebhookResult(success=False, error=error)


async def send_generic_webhook(
    webhook_url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    method: str = "POST",
) -> WebhookResult:
    """
    Send a generic webhook request.
    
    Args:
        webhook_url: Webhook URL
        payload: JSON payload to send
        headers: Optional additional headers
        method: HTTP method (default POST)
        
    Returns:
        WebhookResult with success status
    """
    logger.info(f"Sending generic webhook to {webhook_url}")
    
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    
    try:
        async with httpx.AsyncClient() as client:
            if method.upper() == "POST":
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers=request_headers,
                    timeout=30.0,
                )
            elif method.upper() == "PUT":
                response = await client.put(
                    webhook_url,
                    json=payload,
                    headers=request_headers,
                    timeout=30.0,
                )
            else:
                return WebhookResult(
                    success=False,
                    error=f"Unsupported HTTP method: {method}",
                )
            
            if 200 <= response.status_code < 300:
                # Try to extract message ID from response
                message_id = None
                try:
                    response_data = response.json()
                    message_id = response_data.get("id") or response_data.get("message_id")
                except Exception:
                    pass
                
                if not message_id:
                    message_id = str(uuid.uuid4())
                
                logger.info(f"Generic webhook sent successfully")
                return WebhookResult(
                    success=True,
                    message_id=message_id,
                    status_code=response.status_code,
                )
            else:
                error = f"Webhook error: {response.status_code} - {response.text}"
                logger.error(error)
                return WebhookResult(
                    success=False,
                    status_code=response.status_code,
                    error=error,
                )
    
    except httpx.RequestError as e:
        error = f"Webhook request failed: {str(e)}"
        logger.error(error)
        return WebhookResult(success=False, error=error)


async def send_task_notification(
    channel: str,
    webhook_url: str,
    task_name: str,
    task_id: str,
    summary: str,
    status: str = "completed",
    portal_base_url: Optional[str] = None,
) -> WebhookResult:
    """
    Send a task completion notification via webhook.
    
    This is a convenience function for sending standardized task notifications.
    
    Args:
        channel: Notification channel (teams, slack, webhook)
        webhook_url: Webhook URL
        task_name: Name of the task
        task_id: Task UUID
        summary: Execution summary
        status: Task status (completed, failed)
        portal_base_url: Base URL for portal links
        
    Returns:
        WebhookResult
    """
    title = f"Task '{task_name}' {status.title()}"
    
    portal_link = None
    if portal_base_url:
        portal_link = f"{portal_base_url}/tasks/{task_id}"
    
    # Status color
    if status == "completed":
        teams_color = "28a745"  # Green
        slack_color = "#28a745"
    else:
        teams_color = "dc3545"  # Red
        slack_color = "#dc3545"
    
    facts = [
        {"title": "Task", "value": task_name},
        {"title": "Status", "value": status.title()},
    ]
    
    if channel == "teams":
        return await send_teams_message(
            webhook_url=webhook_url,
            title=title,
            body=summary,
            action_url=portal_link,
            action_text="View Details",
            color=teams_color,
            facts=facts,
        )
    elif channel == "slack":
        return await send_slack_message(
            webhook_url=webhook_url,
            title=title,
            body=summary,
            action_url=portal_link,
            action_text="View Details",
            color=slack_color,
            fields=facts,
        )
    else:
        # Generic webhook
        payload = {
            "event": "task_completed",
            "task": {
                "id": task_id,
                "name": task_name,
                "status": status,
            },
            "summary": summary,
            "portal_link": portal_link,
        }
        return await send_generic_webhook(
            webhook_url=webhook_url,
            payload=payload,
        )
