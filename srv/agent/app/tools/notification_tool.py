"""
Notification Tool for Agent Tasks.

Provides a unified interface for sending notifications across multiple channels:
- Email (SMTP/API)
- Microsoft Teams (Adaptive Cards)
- Slack (Block Kit)
- Generic webhooks
- Bridge outbound channels (Signal/Telegram/Discord/WhatsApp)

This tool is registered with the agent framework and can be called by agents
to send notifications about task completion, alerts, or any other events.
"""

import logging
import re
from typing import Any, Dict, Literal, Optional

import httpx
from pydantic import BaseModel, Field

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


# Notification channel types
NotificationChannel = Literal[
    "email",
    "teams",
    "slack",
    "webhook",
    "bridge_signal",
    "bridge_telegram",
    "bridge_discord",
    "bridge_whatsapp",
]


class NotificationInput(BaseModel):
    """Input schema for the notification tool."""
    
    channel: NotificationChannel = Field(
        ...,
        description="Notification channel: email, teams, slack, webhook, or bridge_* channel"
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


def _metadata_to_fields(metadata: Optional[Dict[str, Any]]) -> list[Dict[str, str]]:
    """Convert notification metadata to compact field/fact entries."""
    if not metadata:
        return []

    ordered_keys = [
        ("task_id", "Task ID"),
        ("execution_id", "Execution"),
        ("run_id", "Run"),
        ("success", "Success"),
        ("library_document_id", "Output Ref"),
    ]
    fields: list[Dict[str, str]] = []
    for key, label in ordered_keys:
        value = metadata.get(key)
        if value is None or value == "":
            continue
        fields.append({"title": label, "value": str(value)})
    return fields


def _strip_markdown(text: str) -> str:
    """Lightweight markdown stripping for channels that render plain text only."""
    stripped = text
    stripped = re.sub(r"```(?:\w+)?\n?([\s\S]*?)```", r"\1", stripped)
    stripped = re.sub(r"`([^`]+)`", r"\1", stripped)
    stripped = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
    stripped = re.sub(r"\*([^*]+)\*", r"\1", stripped)
    stripped = re.sub(r"_([^_]+)_", r"\1", stripped)
    stripped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", stripped)
    return stripped.strip()


def _unescape_literals(text: str) -> str:
    """Convert escaped \\n / \\t that survived JSON/regex extraction into real chars."""
    text = text.replace("\\n", "\n")
    text = text.replace("\\t", "\t")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _bridge_format_body(channel_type: str, body: str) -> str:
    """Render body content for channel-specific markdown support."""
    content = _unescape_literals((body or "").strip())
    if channel_type == "signal":
        return _strip_markdown(content)
    if channel_type == "whatsapp":
        normalized = content.replace("**", "*")
        normalized = re.sub(r"`([^`]+)`", r"\1", normalized)
        return normalized.strip()
    return content


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
        channel: Notification channel (email, teams, slack, webhook, or bridge_* channel)
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
        elif channel.startswith("bridge_"):
            return await _send_bridge_channel_notification(
                bridge_channel=channel,
                recipient=recipient,
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
    
    clean_body = _unescape_literals(body)
    html_body = _build_email_html(clean_body, portal_link)
    
    result = await send_email(
        to=recipient,
        subject=subject,
        body=clean_body,
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
    facts = _metadata_to_fields(metadata)
    
    result = await send_teams_message(
        webhook_url=webhook_url,
        title=subject,
        body=body,
        action_url=portal_link,
        action_text="View Details" if portal_link else None,
        facts=facts or None,
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
    fields = _metadata_to_fields(metadata)
    
    result = await send_slack_message(
        webhook_url=webhook_url,
        title=subject,
        body=body,
        action_url=portal_link,
        action_text="View Details" if portal_link else None,
        fields=fields or None,
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
        "rendered": {
            "email_html": _build_email_html(body, portal_link),
            "plain_text": _strip_markdown(body),
        },
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


def _markdown_to_html(text: str) -> str:
    """
    Convert markdown-like text to HTML for email display.
    
    Handles common patterns:
    - **bold** -> <strong>bold</strong>
    - *italic* -> <em>italic</em>
    - `code` -> <code>code</code>
    - ```code blocks``` -> <pre><code>...</code></pre>
    - Headers (##, ###)
    - Lists (-, *)
    - Links [text](url)
    - Newlines -> <br> or <p>
    """
    import re
    import html
    
    # Convert escaped string literals before anything else
    text = _unescape_literals(text)
    
    # Escape HTML entities first (but we'll unescape our own tags later)
    text = html.escape(text)
    
    # Handle code blocks first (```...```)
    def replace_code_block(match):
        code = match.group(1).strip()
        return f'<pre style="background-color: #f4f4f4; padding: 12px; border-radius: 6px; overflow-x: auto; font-family: monospace; font-size: 13px; border: 1px solid #ddd;"><code>{code}</code></pre>'
    
    text = re.sub(r'```(?:\w+)?\n?(.*?)```', replace_code_block, text, flags=re.DOTALL)
    
    # Handle inline code (`code`)
    text = re.sub(
        r'`([^`]+)`', 
        r'<code style="background-color: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: monospace; font-size: 13px;">\1</code>', 
        text
    )
    
    # Handle bold (**text**)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    
    # Handle italic (*text*) - but not inside URLs or after **
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', text)
    
    # Handle headers
    text = re.sub(r'^### (.+)$', r'<h3 style="margin: 16px 0 8px 0; font-size: 16px; color: #333;">\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h2 style="margin: 20px 0 10px 0; font-size: 18px; color: #333;">\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h1 style="margin: 24px 0 12px 0; font-size: 22px; color: #333;">\1</h1>', text, flags=re.MULTILINE)
    
    # Handle links [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" style="color: #0066cc;">\1</a>', text)
    
    # Handle unordered lists (- or *)
    lines = text.split('\n')
    in_list = False
    result_lines = []
    
    for line in lines:
        stripped = line.strip()
        is_list_item = stripped.startswith('- ') or stripped.startswith('* ')
        
        if is_list_item:
            if not in_list:
                result_lines.append('<ul style="margin: 12px 0; padding-left: 24px;">')
                in_list = True
            item_content = stripped[2:].strip()
            result_lines.append(f'<li style="margin: 6px 0;">{item_content}</li>')
        else:
            if in_list:
                result_lines.append('</ul>')
                in_list = False
            
            # Handle numbered lists (1. , 2. , etc.)
            num_match = re.match(r'^(\d+)\.\s+(.+)$', stripped)
            if num_match:
                # For simplicity, treat numbered items as regular list items
                if not in_list:
                    result_lines.append('<ol style="margin: 12px 0; padding-left: 24px;">')
                    in_list = True
                result_lines.append(f'<li style="margin: 6px 0;">{num_match.group(2)}</li>')
            elif stripped:
                result_lines.append(line)
            else:
                result_lines.append('<br>')
    
    if in_list:
        result_lines.append('</ul>' if '- ' in text or '* ' in text else '</ol>')
    
    text = '\n'.join(result_lines)
    
    # Convert remaining newlines to <br> (but not after block elements)
    text = re.sub(r'\n(?!<)', '<br>\n', text)
    
    # Clean up excessive <br> tags
    text = re.sub(r'(<br>\s*){3,}', '<br><br>', text)
    
    return text


def _build_email_html(body: str, portal_link: Optional[str] = None) -> str:
    """Build HTML email body with styling and portal link."""
    # Convert markdown-like formatting to proper HTML
    html_body = _markdown_to_html(body)
    
    # Add portal link button if provided (now points to output view)
    portal_section = ""
    if portal_link:
        # Modify link to go to output view
        output_link = portal_link + "/output" if not portal_link.endswith("/output") else portal_link
        portal_section = f"""
        <div style="margin-top: 24px; padding: 16px; background-color: #f8f9fa; border-radius: 8px; text-align: center;">
            <a href="{output_link}" 
               style="display: inline-block; padding: 12px 24px; background-color: #0066cc; 
                      color: white; text-decoration: none; border-radius: 6px; font-weight: 500;
                      font-size: 14px;">
                View Full Output
            </a>
        </div>
        """
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
                background-color: #f5f5f5;
            }}
            .content {{
                background-color: #ffffff;
                padding: 24px;
                border-radius: 8px;
                border: 1px solid #e0e0e0;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            }}
            pre {{
                white-space: pre-wrap;
                word-wrap: break-word;
            }}
            code {{
                font-family: 'SF Mono', Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
            }}
            strong {{
                color: #222;
            }}
        </style>
    </head>
    <body>
        <div class="content">
            {html_body}
            {portal_section}
        </div>
        <div style="margin-top: 20px; font-size: 12px; color: #888; text-align: center;">
            <p>This notification was sent by Busibox Agent Tasks.</p>
        </div>
    </body>
    </html>
    """


def _bridge_channel_to_type(bridge_channel: str) -> str:
    """Map notification channel names to bridge channel types."""
    mapping = {
        "bridge_signal": "signal",
        "bridge_telegram": "telegram",
        "bridge_discord": "discord",
        "bridge_whatsapp": "whatsapp",
    }
    return mapping.get(bridge_channel, "")


def _build_bridge_message(
    channel_type: str,
    subject: str,
    body: str,
    _portal_link: Optional[str],
    library_document_id: Optional[str] = None,
) -> str:
    """Compose plain-text bridge message payload."""
    rendered_body = _bridge_format_body(channel_type, body)
    if channel_type == "telegram":
        header = f"*{subject}*"
    elif channel_type == "whatsapp":
        header = f"*{subject}*"
    elif channel_type in {"discord", "slack"}:
        header = f"**{subject}**"
    else:
        header = subject

    parts = [header, "", rendered_body]
    if library_document_id:
        if channel_type == "telegram":
            parts.extend(["", f"Ref: `task-output/{library_document_id}`"])
        elif channel_type in {"discord", "slack"}:
            parts.extend(["", f"Ref: `task-output/{library_document_id}`"])
        else:
            parts.extend(["", f"Ref: task-output/{library_document_id}"])
    return "\n".join(part for part in parts if part is not None).strip()


async def _send_bridge_channel_notification(
    bridge_channel: str,
    recipient: str,
    subject: str,
    body: str,
    portal_link: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> NotificationOutput:
    """Send notification via bridge outbound channel endpoint."""
    channel_type = _bridge_channel_to_type(bridge_channel)
    if not channel_type:
        return NotificationOutput(
            success=False,
            channel=bridge_channel,
            recipient=recipient,
            error=f"Unsupported bridge channel: {bridge_channel}",
        )

    settings = get_settings()
    bridge_api_url = (settings.bridge_api_url or "").strip()
    if not bridge_api_url:
        return NotificationOutput(
            success=False,
            channel=bridge_channel,
            recipient=recipient,
            error="BRIDGE_API_URL is not configured in agent service",
        )

    library_document_id = None
    if metadata:
        library_document_id = metadata.get("library_document_id")

    bridge_metadata = dict(metadata or {})
    if channel_type == "telegram":
        bridge_metadata.setdefault("telegram_parse_mode", "Markdown")

    payload = {
        "channel_type": channel_type,
        "recipient": recipient,
        "text": _build_bridge_message(
            channel_type=channel_type,
            subject=subject,
            body=body,
            _portal_link=portal_link,
            library_document_id=library_document_id,
        ),
        "metadata": bridge_metadata,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{bridge_api_url.rstrip('/')}/api/v1/channels/send",
                json=payload,
            )
            response.raise_for_status()
        return NotificationOutput(
            success=True,
            channel=bridge_channel,
            recipient=recipient,
        )
    except Exception as exc:
        return NotificationOutput(
            success=False,
            channel=bridge_channel,
            recipient=recipient,
            error=str(exc),
        )


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
                "enum": [
                    "email",
                    "teams",
                    "slack",
                    "webhook",
                    "bridge_signal",
                    "bridge_telegram",
                    "bridge_discord",
                    "bridge_whatsapp",
                ],
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
