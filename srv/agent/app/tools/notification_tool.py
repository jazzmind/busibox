"""
Notification Tool for Agent Tasks.

Provides a unified interface for sending notifications across multiple channels:
- Email (SMTP/API)
- Microsoft Teams (Adaptive Cards)
- Slack (Block Kit)
- Generic webhooks

This tool is registered with the agent framework and can be called by agents
to send notifications about task completion, alerts, or any other events.
"""

import logging
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# Notification channel types
NotificationChannel = Literal["email", "teams", "slack", "webhook"]


class NotificationInput(BaseModel):
    """Input schema for the notification tool."""
    
    channel: NotificationChannel = Field(
        ...,
        description="Notification channel: email, teams, slack, or webhook"
    )
    recipient: str = Field(
        ...,
        description="Email address, webhook URL, or channel ID"
    )
    subject: str = Field(
        ...,
        description="Notification subject/title"
    )
    body: str = Field(
        ...,
        description="Notification body/content (supports markdown for teams/slack)"
    )
    portal_link: Optional[str] = Field(
        None,
        description="Link to portal for detailed results"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional metadata for the notification"
    )


class NotificationOutput(BaseModel):
    """Output schema for the notification tool."""
    
    success: bool = Field(..., description="Whether the notification was sent successfully")
    message_id: Optional[str] = Field(None, description="Message ID if available")
    channel: str = Field(..., description="Channel used")
    recipient: str = Field(..., description="Recipient address")
    error: Optional[str] = Field(None, description="Error message if failed")


async def send_notification(
    channel: str,
    recipient: str,
    subject: str,
    body: str,
    portal_link: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> NotificationOutput:
    """
    Send a notification via the specified channel.
    
    This is the main tool function that agents can call.
    
    Args:
        channel: Notification channel (email, teams, slack, webhook)
        recipient: Email address, webhook URL, or channel ID
        subject: Notification subject/title
        body: Notification body (supports markdown for teams/slack)
        portal_link: Optional link to portal for details
        metadata: Optional additional metadata
        
    Returns:
        NotificationOutput with success status and details
    """
    logger.info(
        f"Sending notification via {channel} to {recipient}",
        extra={"channel": channel, "recipient": recipient, "subject": subject}
    )
    
    try:
        if channel == "email":
            return await _send_email_notification(
                recipient=recipient,
                subject=subject,
                body=body,
                portal_link=portal_link,
                metadata=metadata,
            )
        elif channel == "teams":
            return await _send_teams_notification(
                webhook_url=recipient,
                subject=subject,
                body=body,
                portal_link=portal_link,
                metadata=metadata,
            )
        elif channel == "slack":
            return await _send_slack_notification(
                webhook_url=recipient,
                subject=subject,
                body=body,
                portal_link=portal_link,
                metadata=metadata,
            )
        elif channel == "webhook":
            return await _send_webhook_notification(
                webhook_url=recipient,
                subject=subject,
                body=body,
                portal_link=portal_link,
                metadata=metadata,
            )
        else:
            return NotificationOutput(
                success=False,
                channel=channel,
                recipient=recipient,
                error=f"Unknown notification channel: {channel}",
            )
    
    except Exception as e:
        logger.error(f"Failed to send {channel} notification: {e}", exc_info=True)
        return NotificationOutput(
            success=False,
            channel=channel,
            recipient=recipient,
            error=str(e),
        )


async def _send_email_notification(
    recipient: str,
    subject: str,
    body: str,
    portal_link: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> NotificationOutput:
    """Send email notification via email service."""
    from app.services.email_service import send_email
    
    # Build HTML body with portal link
    html_body = _build_email_html(body, portal_link)
    
    result = await send_email(
        to=recipient,
        subject=subject,
        body=body,  # Plain text fallback
        html_body=html_body,
    )
    
    return NotificationOutput(
        success=result.success,
        message_id=result.message_id,
        channel="email",
        recipient=recipient,
        error=result.error,
    )


async def _send_teams_notification(
    webhook_url: str,
    subject: str,
    body: str,
    portal_link: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> NotificationOutput:
    """Send Microsoft Teams notification via webhook."""
    from app.services.webhook_sender import send_teams_message
    
    result = await send_teams_message(
        webhook_url=webhook_url,
        title=subject,
        body=body,
        action_url=portal_link,
        action_text="View Details" if portal_link else None,
    )
    
    return NotificationOutput(
        success=result.success,
        message_id=result.message_id,
        channel="teams",
        recipient=webhook_url,
        error=result.error,
    )


async def _send_slack_notification(
    webhook_url: str,
    subject: str,
    body: str,
    portal_link: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> NotificationOutput:
    """Send Slack notification via webhook."""
    from app.services.webhook_sender import send_slack_message
    
    result = await send_slack_message(
        webhook_url=webhook_url,
        title=subject,
        body=body,
        action_url=portal_link,
        action_text="View Details" if portal_link else None,
    )
    
    return NotificationOutput(
        success=result.success,
        message_id=result.message_id,
        channel="slack",
        recipient=webhook_url,
        error=result.error,
    )


async def _send_webhook_notification(
    webhook_url: str,
    subject: str,
    body: str,
    portal_link: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> NotificationOutput:
    """Send generic webhook notification."""
    from app.services.webhook_sender import send_generic_webhook
    
    payload = {
        "subject": subject,
        "body": body,
        "portal_link": portal_link,
        "metadata": metadata or {},
    }
    
    result = await send_generic_webhook(
        webhook_url=webhook_url,
        payload=payload,
    )
    
    return NotificationOutput(
        success=result.success,
        message_id=result.message_id,
        channel="webhook",
        recipient=webhook_url,
        error=result.error,
    )


def _build_email_html(body: str, portal_link: Optional[str] = None) -> str:
    """Build HTML email body with styling and portal link."""
    # Convert markdown-like formatting to HTML
    html_body = body.replace("\n", "<br>")
    
    # Add portal link button if provided
    portal_section = ""
    if portal_link:
        portal_section = f"""
        <div style="margin-top: 20px; padding: 15px; background-color: #f8f9fa; border-radius: 8px;">
            <a href="{portal_link}" 
               style="display: inline-block; padding: 10px 20px; background-color: #0066cc; 
                      color: white; text-decoration: none; border-radius: 4px; font-weight: 500;">
                View Details in Portal
            </a>
        </div>
        """
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
            }}
            .content {{
                background-color: #ffffff;
                padding: 20px;
                border-radius: 8px;
                border: 1px solid #e0e0e0;
            }}
        </style>
    </head>
    <body>
        <div class="content">
            {html_body}
            {portal_section}
        </div>
        <div style="margin-top: 20px; font-size: 12px; color: #666;">
            <p>This notification was sent by Busibox Agent Tasks.</p>
        </div>
    </body>
    </html>
    """


# Register the notification tool with the agent framework
def register_notification_tool():
    """Register the notification tool with the ToolRegistry."""
    from app.agents.base_agent import ToolRegistry
    
    ToolRegistry.register(
        name="send_notification",
        func=send_notification,
        output_type=NotificationOutput,
    )
    
    logger.info("Registered send_notification tool")


# Tool metadata for agent discovery
NOTIFICATION_TOOL_SCHEMA = {
    "name": "send_notification",
    "description": "Send notifications via email, Microsoft Teams, Slack, or generic webhooks",
    "parameters": {
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "enum": ["email", "teams", "slack", "webhook"],
                "description": "Notification channel to use"
            },
            "recipient": {
                "type": "string",
                "description": "Email address or webhook URL"
            },
            "subject": {
                "type": "string",
                "description": "Notification subject/title"
            },
            "body": {
                "type": "string",
                "description": "Notification body content"
            },
            "portal_link": {
                "type": "string",
                "description": "Optional link to portal for details"
            }
        },
        "required": ["channel", "recipient", "subject", "body"]
    }
}
