"""
Email Service for Agent Notifications.

Supports multiple email providers:
- SMTP (default, works with any SMTP server)
- SendGrid (via API)
- AWS SES (via API)

Configuration is read from environment variables or settings.
"""

import logging
import os
import smtplib
import ssl
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

import httpx
from pydantic import BaseModel, Field

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


class EmailResult(BaseModel):
    """Result of email send operation."""
    
    success: bool = Field(..., description="Whether email was sent successfully")
    message_id: Optional[str] = Field(None, description="Email message ID")
    error: Optional[str] = Field(None, description="Error message if failed")


class EmailConfig(BaseModel):
    """Email service configuration."""
    
    provider: str = Field("smtp", description="Email provider: smtp, sendgrid, ses")
    
    # SMTP settings
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    
    # SendGrid settings
    sendgrid_api_key: Optional[str] = None
    
    # AWS SES settings
    ses_region: Optional[str] = None
    ses_access_key: Optional[str] = None
    ses_secret_key: Optional[str] = None
    
    # Common settings
    from_email: str = "noreply@busibox.local"
    from_name: str = "Busibox Agent Tasks"


def get_email_config() -> EmailConfig:
    """Get email configuration from environment."""
    settings = get_settings()
    
    return EmailConfig(
        provider=os.getenv("EMAIL_PROVIDER", "smtp"),
        smtp_host=os.getenv("SMTP_HOST", getattr(settings, "smtp_host", None)),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME", getattr(settings, "smtp_username", None)),
        smtp_password=os.getenv("SMTP_PASSWORD", getattr(settings, "smtp_password", None)),
        smtp_use_tls=os.getenv("SMTP_USE_TLS", "true").lower() == "true",
        sendgrid_api_key=os.getenv("SENDGRID_API_KEY"),
        ses_region=os.getenv("AWS_SES_REGION", "us-east-1"),
        ses_access_key=os.getenv("AWS_ACCESS_KEY_ID"),
        ses_secret_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        from_email=os.getenv("EMAIL_FROM", getattr(settings, "email_from", "noreply@busibox.local")),
        from_name=os.getenv("EMAIL_FROM_NAME", "Busibox Agent Tasks"),
    )


async def send_email(
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    reply_to: Optional[str] = None,
) -> EmailResult:
    """
    Send an email via configured provider.
    
    Args:
        to: Recipient email address
        subject: Email subject
        body: Plain text body
        html_body: Optional HTML body
        cc: Optional CC recipients
        bcc: Optional BCC recipients
        reply_to: Optional reply-to address
        
    Returns:
        EmailResult with success status
    """
    config = get_email_config()
    
    logger.info(
        f"Sending email to {to} via {config.provider}",
        extra={"to": to, "subject": subject, "provider": config.provider}
    )
    
    try:
        if config.provider == "sendgrid":
            return await _send_via_sendgrid(
                config=config,
                to=to,
                subject=subject,
                body=body,
                html_body=html_body,
                cc=cc,
                bcc=bcc,
                reply_to=reply_to,
            )
        elif config.provider == "ses":
            return await _send_via_ses(
                config=config,
                to=to,
                subject=subject,
                body=body,
                html_body=html_body,
                cc=cc,
                bcc=bcc,
                reply_to=reply_to,
            )
        else:
            # Default to SMTP
            return await _send_via_smtp(
                config=config,
                to=to,
                subject=subject,
                body=body,
                html_body=html_body,
                cc=cc,
                bcc=bcc,
                reply_to=reply_to,
            )
    
    except Exception as e:
        logger.error(f"Failed to send email: {e}", exc_info=True)
        return EmailResult(
            success=False,
            error=str(e),
        )


async def _send_via_smtp(
    config: EmailConfig,
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    reply_to: Optional[str] = None,
) -> EmailResult:
    """Send email via SMTP."""
    if not config.smtp_host:
        return EmailResult(
            success=False,
            error="SMTP host not configured. Set SMTP_HOST environment variable.",
        )
    
    # Create message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{config.from_name} <{config.from_email}>"
    msg["To"] = to
    
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    
    # Generate message ID
    message_id = f"<{uuid.uuid4()}@busibox.local>"
    msg["Message-ID"] = message_id
    
    # Attach plain text
    msg.attach(MIMEText(body, "plain"))
    
    # Attach HTML if provided
    if html_body:
        msg.attach(MIMEText(html_body, "html"))
    
    # Build recipient list
    recipients = [to]
    if cc:
        recipients.extend(cc)
    if bcc:
        recipients.extend(bcc)
    
    # Send via SMTP
    try:
        if config.smtp_use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
                server.starttls(context=context)
                if config.smtp_username and config.smtp_password:
                    server.login(config.smtp_username, config.smtp_password)
                server.sendmail(config.from_email, recipients, msg.as_string())
        else:
            with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
                if config.smtp_username and config.smtp_password:
                    server.login(config.smtp_username, config.smtp_password)
                server.sendmail(config.from_email, recipients, msg.as_string())
        
        logger.info(f"Email sent via SMTP to {to}, message_id={message_id}")
        return EmailResult(success=True, message_id=message_id)
    
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error: {e}")
        return EmailResult(success=False, error=f"SMTP error: {str(e)}")


async def _send_via_sendgrid(
    config: EmailConfig,
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    reply_to: Optional[str] = None,
) -> EmailResult:
    """Send email via SendGrid API."""
    if not config.sendgrid_api_key:
        return EmailResult(
            success=False,
            error="SendGrid API key not configured. Set SENDGRID_API_KEY environment variable.",
        )
    
    # Build SendGrid payload
    content = [{"type": "text/plain", "value": body}]
    if html_body:
        content.append({"type": "text/html", "value": html_body})
    
    payload = {
        "personalizations": [
            {
                "to": [{"email": to}],
            }
        ],
        "from": {
            "email": config.from_email,
            "name": config.from_name,
        },
        "subject": subject,
        "content": content,
    }
    
    if cc:
        payload["personalizations"][0]["cc"] = [{"email": e} for e in cc]
    if bcc:
        payload["personalizations"][0]["bcc"] = [{"email": e} for e in bcc]
    if reply_to:
        payload["reply_to"] = {"email": reply_to}
    
    # Send via SendGrid API
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {config.sendgrid_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30.0,
        )
        
        if response.status_code == 202:
            message_id = response.headers.get("X-Message-Id", str(uuid.uuid4()))
            logger.info(f"Email sent via SendGrid to {to}, message_id={message_id}")
            return EmailResult(success=True, message_id=message_id)
        else:
            error = f"SendGrid error: {response.status_code} - {response.text}"
            logger.error(error)
            return EmailResult(success=False, error=error)


async def _send_via_ses(
    config: EmailConfig,
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    reply_to: Optional[str] = None,
) -> EmailResult:
    """Send email via AWS SES."""
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        return EmailResult(
            success=False,
            error="boto3 not installed. Install with: pip install boto3",
        )
    
    if not config.ses_access_key or not config.ses_secret_key:
        return EmailResult(
            success=False,
            error="AWS SES credentials not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.",
        )
    
    # Create SES client
    ses = boto3.client(
        "ses",
        region_name=config.ses_region,
        aws_access_key_id=config.ses_access_key,
        aws_secret_access_key=config.ses_secret_key,
    )
    
    # Build destination
    destination = {"ToAddresses": [to]}
    if cc:
        destination["CcAddresses"] = cc
    if bcc:
        destination["BccAddresses"] = bcc
    
    # Build message
    message = {
        "Subject": {"Data": subject, "Charset": "UTF-8"},
        "Body": {
            "Text": {"Data": body, "Charset": "UTF-8"},
        },
    }
    if html_body:
        message["Body"]["Html"] = {"Data": html_body, "Charset": "UTF-8"}
    
    try:
        response = ses.send_email(
            Source=f"{config.from_name} <{config.from_email}>",
            Destination=destination,
            Message=message,
            ReplyToAddresses=[reply_to] if reply_to else [],
        )
        
        message_id = response["MessageId"]
        logger.info(f"Email sent via SES to {to}, message_id={message_id}")
        return EmailResult(success=True, message_id=message_id)
    
    except ClientError as e:
        error = f"SES error: {e.response['Error']['Message']}"
        logger.error(error)
        return EmailResult(success=False, error=error)


async def send_task_completion_email(
    to: str,
    task_name: str,
    task_id: str,
    summary: str,
    status: str = "completed",
    portal_base_url: Optional[str] = None,
) -> EmailResult:
    """
    Send a task completion notification email.
    
    This is a convenience function for sending standardized task notifications.
    
    Args:
        to: Recipient email
        task_name: Name of the task
        task_id: Task UUID
        summary: Execution summary
        status: Task status (completed, failed)
        portal_base_url: Base URL for portal links
        
    Returns:
        EmailResult
    """
    subject = f"Task '{task_name}' {status}"
    
    portal_link = None
    if portal_base_url:
        portal_link = f"{portal_base_url}/tasks/{task_id}"
    
    # Build plain text body
    body = f"""
Task Execution {status.title()}

Task: {task_name}

Summary:
{summary}

"""
    if portal_link:
        body += f"View details: {portal_link}\n"
    
    # Build HTML body
    status_color = "#28a745" if status == "completed" else "#dc3545"
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
            }}
            .header {{
                background-color: {status_color};
                color: white;
                padding: 15px 20px;
                border-radius: 8px 8px 0 0;
            }}
            .content {{
                background-color: #f8f9fa;
                padding: 20px;
                border-radius: 0 0 8px 8px;
                border: 1px solid #e0e0e0;
                border-top: none;
            }}
            .summary {{
                background-color: white;
                padding: 15px;
                border-radius: 4px;
                margin: 15px 0;
                border-left: 4px solid {status_color};
            }}
            .button {{
                display: inline-block;
                padding: 12px 24px;
                background-color: #0066cc;
                color: white;
                text-decoration: none;
                border-radius: 4px;
                font-weight: 500;
                margin-top: 15px;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2 style="margin: 0;">Task {status.title()}</h2>
        </div>
        <div class="content">
            <p><strong>Task:</strong> {task_name}</p>
            <div class="summary">
                <strong>Summary:</strong>
                <p>{summary}</p>
            </div>
            {"<a href='" + portal_link + "' class='button'>View Details</a>" if portal_link else ""}
        </div>
        <div style="margin-top: 20px; font-size: 12px; color: #666;">
            <p>This notification was sent by Busibox Agent Tasks.</p>
        </div>
    </body>
    </html>
    """
    
    return await send_email(
        to=to,
        subject=subject,
        body=body.strip(),
        html_body=html_body,
    )
