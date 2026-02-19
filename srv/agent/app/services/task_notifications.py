"""
Shared task notification logic.

Single source of truth for sending task completion notifications.
Used by both the scheduler (cron tasks) and the API routes (manual execution).
"""

import ast
import json
import logging
import re
import time
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


_portal_name_cache: Dict[str, Any] = {"value": None, "expires": 0}


async def get_portal_name() -> str:
    """Read portal site name from deploy-api config store with 5-minute cache."""
    now = time.monotonic()
    if _portal_name_cache["value"] and now < _portal_name_cache["expires"]:
        return _portal_name_cache["value"]

    settings = get_settings()
    deploy_url = getattr(settings, "deploy_api_url", None) or os.getenv("DEPLOY_API_URL", "")
    service_key = os.getenv("LITELLM_API_KEY", "")

    if deploy_url and service_key:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{deploy_url.rstrip('/')}/api/v1/config/PORTAL_SITE_NAME/raw",
                    headers={"Authorization": f"Bearer {service_key}"},
                )
            if resp.status_code == 200:
                data = resp.json() or {}
                name = str(data.get("value") or "").strip()
                if name:
                    _portal_name_cache["value"] = name
                    _portal_name_cache["expires"] = now + 300
                    return name
        except Exception as exc:
            logger.debug("Could not read PORTAL_SITE_NAME from deploy-api: %s", exc)

    fallback = getattr(settings, "portal_name", None) or "Busibox"
    _portal_name_cache["value"] = fallback
    _portal_name_cache["expires"] = now + 60
    return fallback


def extract_content_from_output(output_summary: Optional[str]) -> str:
    """
    Extract the actual content from an output summary, handling dict-like strings,
    truncated JSON, and markdown code fences.
    """
    if not output_summary:
        return ""

    content = output_summary.strip()

    def _extract_json_candidate(text: str) -> Optional[str]:
        code_fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
        if code_fence_match:
            return code_fence_match.group(1).strip()
        if text.startswith("{") or text.startswith("["):
            return text
        start_positions = [idx for idx in (text.find("{"), text.find("[")) if idx >= 0]
        if not start_positions:
            return None
        return text[min(start_positions):].strip()

    def _repair_truncated_json(text: str) -> str:
        repaired = text.rstrip()
        repaired = re.sub(r",\s*$", "", repaired)

        in_string = False
        escape = False
        stack: List[str] = []
        for ch in repaired:
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]":
                if stack and ch == stack[-1]:
                    stack.pop()

        if in_string:
            repaired += '"'
        while stack:
            repaired += stack.pop()
        return repaired

    def _extract_preferred_value(parsed: Any) -> Optional[str]:
        if isinstance(parsed, dict):
            for key in ("result", "summary", "output", "response", "content"):
                value = parsed.get(key)
                if value:
                    return str(value)
        return None

    def _extract_preferred_value_by_regex(text: str) -> Optional[str]:
        for key in ("result", "summary", "output", "response", "content"):
            single_quote_match = re.search(
                rf"['\"]{key}['\"]\s*:\s*'([\s\S]+)",
                text,
                flags=re.IGNORECASE,
            )
            if single_quote_match:
                value = single_quote_match.group(1)
                return value.rstrip("'}] \n\r\t")
            double_quote_match = re.search(
                rf"['\"]{key}['\"]\s*:\s*\"([\s\S]+)",
                text,
                flags=re.IGNORECASE,
            )
            if double_quote_match:
                value = double_quote_match.group(1)
                return value.rstrip("\"}] \n\r\t")
        return None

    json_candidate = _extract_json_candidate(content)
    if json_candidate:
        parsed = None

        try:
            parsed = json.loads(json_candidate)
        except json.JSONDecodeError:
            parsed = None

        if parsed is None:
            try:
                parsed = json.loads(_repair_truncated_json(json_candidate))
            except json.JSONDecodeError:
                parsed = None

        if parsed is None:
            try:
                parsed = ast.literal_eval(json_candidate)
            except (ValueError, SyntaxError):
                parsed = None

        if parsed is not None:
            preferred = _extract_preferred_value(parsed)
            if preferred:
                content = preferred
            elif isinstance(parsed, dict):
                formatted_parts = []
                for key, value in parsed.items():
                    if isinstance(value, str) and len(value) > 500:
                        value = value[:500] + "..."
                    formatted_parts.append(f"**{key}:** {value}")
                content = "\n".join(formatted_parts)
        else:
            regex_value = _extract_preferred_value_by_regex(json_candidate)
            if regex_value:
                content = regex_value

    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    return content.strip()


def format_output_for_notification(output: Any) -> str:
    """Format output for notification display, handling dicts and strings."""
    if output is None:
        return ""
    if isinstance(output, dict):
        for key in ("result", "summary", "output", "response", "content"):
            value = output.get(key)
            if value:
                if isinstance(value, str):
                    return value
                return extract_content_from_output(str(value))
    return extract_content_from_output(str(output))


async def send_task_notification(
    session,
    task,
    execution,
    success: bool,
    output_summary: Optional[str],
    output_payload: Any = None,
    library_document_id: Optional[str] = None,
    run_record: Any = None,
) -> None:
    """
    Send notification for task completion.

    Creates notification records and delivers via all configured channels.
    Supports both single channel (legacy) and multiple channels.

    Args:
        session: SQLAlchemy async session
        task: Task model instance
        execution: TaskExecution model instance
        success: Whether the task succeeded
        output_summary: Short text summary of the output
        output_payload: Full structured output (dict or string) — preferred over output_summary
        library_document_id: Optional data-api document ID for follow-up
        run_record: Optional Run model instance (provides run_id for metadata)
    """
    from app.tools.notification_tool import send_notification
    from app.models.domain import TaskNotification

    notification_config = task.notification_config or {}

    if not notification_config.get("enabled", True):
        return

    notify_on_success = notification_config.get("on_success", True)
    notify_on_failure = notification_config.get("on_failure", True)

    if success and not notify_on_success:
        logger.debug(f"Skipping success notification for task {task.id} (disabled)")
        return
    if not success and not notify_on_failure:
        logger.debug(f"Skipping failure notification for task {task.id} (disabled)")
        return

    status_emoji = "✅" if success else "❌"
    portal_name = await get_portal_name()
    subject = f"{status_emoji} {task.name} from {portal_name}"

    body_parts: List[str] = []

    output_source = output_payload if output_payload is not None else output_summary
    if output_source:
        formatted_output = format_output_for_notification(output_source)

        if len(formatted_output) > 500:
            try:
                from app.services.output_summarizer import summarize_task_output
                summary_preview = await summarize_task_output(
                    output=formatted_output,
                    task_name=task.name,
                    max_length=500,
                )
            except Exception as e:
                logger.warning(f"Failed to summarize output, using truncation: {e}")
                summary_preview = formatted_output[:500] + "..."
        else:
            summary_preview = formatted_output

        body_parts.append(summary_preview)

    if not success and execution.error:
        body_parts.append(f"Error: {execution.error}")

    if not body_parts:
        body_parts.append("Task completed.")

    body = "\n".join(body_parts)

    settings = get_settings()
    portal_base = settings.portal_base_url or "https://localhost"
    portal_link = f"{portal_base}/agents/tasks/{task.id}/executions/{execution.id}/output"

    channels_to_notify = []

    if notification_config.get("channels"):
        for ch in notification_config["channels"]:
            if ch.get("enabled", True) and ch.get("recipient"):
                channels_to_notify.append({
                    "channel": ch.get("channel", "email"),
                    "recipient": ch["recipient"],
                })

    if not channels_to_notify and notification_config.get("recipient"):
        channels_to_notify.append({
            "channel": notification_config.get("channel", "email"),
            "recipient": notification_config["recipient"],
        })

    if not channels_to_notify:
        logger.warning(f"Task {task.id} has notifications enabled but no valid channels configured")
        return

    any_success = False
    last_error = None

    for ch_config in channels_to_notify:
        channel = ch_config["channel"]
        recipient = ch_config["recipient"]

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
                    "run_id": str(run_record.id) if run_record else None,
                    "success": success,
                    "library_document_id": library_document_id,
                },
            )

            if notification:
                notification.status = "sent" if result.success else "failed"
                notification.message_id = result.message_id
                notification.error = result.error
                notification.sent_at = datetime.now() if result.success else None

            if result.success:
                any_success = True
                logger.info(f"Sent {channel} notification to {recipient} for task {task.id}")
            else:
                last_error = result.error
                logger.error(f"Failed to send {channel} notification to {recipient}: {result.error}")

        except Exception as e:
            logger.error(f"Error sending {channel} notification: {e}", exc_info=True)
            last_error = str(e)
            if notification:
                notification.status = "failed"
                notification.error = str(e)

    execution.notification_sent = any_success
    execution.notification_error = last_error if not any_success else None

    try:
        await session.commit()
    except Exception as e:
        logger.error(f"Error committing notification status: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
