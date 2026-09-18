"""
Completion emails for detached chat turns.

Policy ("only if nobody is watching"):

- send when the turn ends and no client is attached to its stream, or when
  it ran longer than ``chat_notify_min_seconds`` (the user has almost
  certainly moved on);
- never for a turn that finished in a few seconds with the user still on the
  page;
- respects ``chat_settings.notify_email_on_completion`` (per user) and
  ``chat_notify_email_enabled`` (platform);
- needs an email claim on the user's JWT and a configured email transport
  (Bridge API or SMTP — ``services/email_service.py``).

The email says what finished, shows the first part of the answer, links back
to the conversation, and lists any files the answer produced (Excel / Word /
PowerPoint links from the document engine) as direct download links.
"""

from __future__ import annotations

import html
import logging
import re
import uuid
from typing import List, Optional, Tuple

from app.config.settings import get_settings
from app.schemas.auth import Principal

logger = logging.getLogger(__name__)

_LINK_RE = re.compile(r"\[([^\]]{1,200})\]\(([^)\s]+)\)")
_MEDIA_RE = re.compile(r"/api/media/[0-9a-fA-F-]{36}")
_PREVIEW_CHARS = 700

STATUS_LABEL = {
    "completed": "is ready",
    "failed": "could not be completed",
    "cancelled": "was stopped",
    "interrupted": "was interrupted",
}


def should_notify(*, status: str, subscribers: int, elapsed_s: float) -> bool:
    settings = get_settings()
    if not getattr(settings, "chat_notify_email_enabled", True):
        return False
    if status == "cancelled":
        return False  # the user pressed Stop; they know
    min_s = int(getattr(settings, "chat_notify_min_seconds", 120))
    return subscribers == 0 or elapsed_s >= min_s


def file_links(answer_text: str, base_url: str) -> List[Tuple[str, str]]:
    """(label, absolute url) for download/preview links the answer contains."""
    out: List[Tuple[str, str]] = []
    for label, url in _LINK_RE.findall(answer_text or ""):
        if "download=1" in url or _MEDIA_RE.search(url):
            absolute = url if url.startswith("http") else f"{base_url.rstrip('/')}{url if url.startswith('/') else '/' + url}"
            if "download=1" in url:
                name = re.sub(r"^(?:Download the (?:spreadsheet|document|slides|file)|Download):\s*", "", label.strip())
                out.append((name, absolute))
    return out


def _strip_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images
    text = _LINK_RE.sub(lambda m: m.group(1), text)              # links → label
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)           # headings
    text = re.sub(r"^\s*(?:-{3,}|\*{3,})\s*$", "", text, flags=re.M)  # horizontal rules
    text = re.sub(r"[*_`>]+", "", text)                          # emphasis, code, quotes
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_email(
    *,
    status: str,
    conversation_title: str,
    conversation_id: uuid.UUID,
    answer_text: str,
    error_text: Optional[str],
    elapsed_s: float,
    base_url: str,
) -> Tuple[str, str, str]:
    """Return (subject, text body, html body)."""
    label = STATUS_LABEL.get(status, status)
    title = conversation_title.strip() or "your chat"
    subject = f"Busibox: your answer {label} — {title}"[:160]
    link = f"{base_url.rstrip('/')}/chat?conversation={conversation_id}"
    minutes = max(1, round(elapsed_s / 60)) if elapsed_s >= 60 else None
    took = f" It took about {minutes} minute{'s' if minutes != 1 else ''}." if minutes else ""

    preview = _strip_markdown(answer_text)[:_PREVIEW_CHARS]
    if len(_strip_markdown(answer_text)) > _PREVIEW_CHARS:
        preview += "…"
    files = file_links(answer_text, base_url)

    if status == "completed":
        lead = f"Your response in “{title}” is ready.{took}"
    elif status == "interrupted":
        lead = f"Your response in “{title}” was interrupted (the service restarted).{took} Open the conversation and ask again to rerun it."
    else:
        lead = f"Your response in “{title}” could not be completed.{took}" + (f" Error: {error_text}" if error_text else "")

    text_lines = [lead, ""]
    if preview:
        text_lines += [preview, ""]
    if files:
        text_lines.append("Files:")
        text_lines += [f"- {name}: {url}" for name, url in files]
        text_lines.append("")
    text_lines.append(f"Open the conversation: {link}")
    text_lines += ["", "You can turn these emails off in Chat settings."]
    text = "\n".join(text_lines)

    esc = html.escape
    files_html = ""
    if files:
        items = "".join(f'<li><a href="{esc(u)}">{esc(n)}</a></li>' for n, u in files)
        files_html = f"<p style=\"margin:16px 0 4px\"><strong>Files</strong></p><ul style=\"margin:0 0 16px 20px;padding:0\">{items}</ul>"
    preview_html = f"<div style=\"white-space:pre-wrap;color:#374151;border-left:3px solid #d1d5db;padding-left:12px;margin:16px 0\">{esc(preview)}</div>" if preview else ""
    html_body = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;line-height:1.5;color:#111827;max-width:640px;margin:0 auto;padding:24px">
<p style="font-size:16px">{esc(lead)}</p>
{preview_html}
{files_html}
<p style="margin:24px 0"><a href="{esc(link)}" style="display:inline-block;padding:10px 20px;background:#1f3864;color:#fff;text-decoration:none;border-radius:6px;font-weight:600">Open the conversation</a></p>
<p style="color:#6b7280;font-size:12px">You can turn these emails off in Chat settings.</p>
</body></html>"""
    return subject, text, html_body


async def maybe_notify(
    *,
    turn_id: uuid.UUID,
    status: str,
    principal: Principal,
    conversation_id: uuid.UUID,
    conversation_title: str,
    answer_text: str,
    error_text: Optional[str],
    elapsed_s: float,
    subscribers: int,
    use_test_db: bool = False,
) -> bool:
    """Apply the policy and send. Returns True when an email went out."""
    if not should_notify(status=status, subscribers=subscribers, elapsed_s=elapsed_s):
        return False
    email = getattr(principal, "email", None)
    if not email:
        logger.info("chat turn %s: no email on principal; skipping notification", turn_id)
        return False
    from app.services.chat_turns import user_notify_preference

    if not await user_notify_preference(principal.sub, use_test_db):
        return False

    settings = get_settings()
    base_url = getattr(settings, "portal_base_url", "") or ""
    subject, text, html_body = build_email(
        status=status, conversation_title=conversation_title, conversation_id=conversation_id,
        answer_text=answer_text, error_text=error_text, elapsed_s=elapsed_s, base_url=base_url,
    )
    from app.services.email_service import send_email

    result = await send_email(to=email, subject=subject, body=text, html_body=html_body)
    if result.success:
        logger.info("chat turn %s: completion email sent", turn_id, extra={"status": status, "subscribers": subscribers})
        try:
            from datetime import datetime, timezone

            from sqlalchemy import select

            from app.db.session import get_session_context
            from app.models.domain import ChatTurn

            async with get_session_context(use_test_db) as session:
                turn = (await session.execute(select(ChatTurn).where(ChatTurn.id == turn_id))).scalar_one_or_none()
                if turn:
                    turn.notified_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    await session.commit()
        except Exception:  # noqa: BLE001
            logger.debug("chat turn %s: could not record notified_at", turn_id)
        return True
    logger.warning("chat turn %s: completion email failed: %s", turn_id, result.error)
    return False
