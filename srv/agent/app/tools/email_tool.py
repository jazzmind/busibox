"""
Email read toolset for agents.

Supports Gmail API and Microsoft Graph Mail API using per-user OAuth tokens
retrieved from the authz integration store.

Tools:
  - email_list_recent  -- list recent emails
  - email_search       -- search inbox by sender/subject/keywords
  - email_read         -- read a specific email thread by message ID
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, quote

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Tool, RunContext

from app.agents.core import BusiboxDeps
from app.config.settings import get_settings

settings = get_settings()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class EmailMessage(BaseModel):
    id: str
    subject: str
    from_email: str
    to_emails: List[str] = Field(default_factory=list)
    date: Optional[str] = None
    snippet: Optional[str] = None
    body: Optional[str] = None
    thread_id: Optional[str] = None


class EmailListOutput(BaseModel):
    success: bool
    messages: List[EmailMessage] = Field(default_factory=list)
    count: int = 0
    provider: Optional[str] = None
    error: Optional[str] = None


class EmailReadOutput(BaseModel):
    success: bool
    messages: List[EmailMessage] = Field(default_factory=list)
    subject: Optional[str] = None
    count: int = 0
    provider: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth helpers (shared with calendar_tool)
# ---------------------------------------------------------------------------

def _authz_base_url() -> str:
    token_url = str(settings.auth_token_url)
    parsed = urlparse(token_url)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _get_user_token(ctx: RunContext[BusiboxDeps], provider: str) -> tuple[Optional[str], str]:
    """Return (access_token, provider_name) for the user's email integration."""
    user_token = ctx.deps.principal.token
    authz_url = _authz_base_url()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{authz_url}/internal/integrations/{provider}/token",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("access_token"), provider
    except Exception:
        pass
    return None, provider


async def _get_best_email_token(ctx: RunContext[BusiboxDeps]) -> tuple[Optional[str], str]:
    """Try Google then Microsoft for email access."""
    for provider in ("google", "microsoft"):
        token, pname = await _get_user_token(ctx, provider)
        if token:
            return token, pname
    return None, ""


# ---------------------------------------------------------------------------
# Gmail helpers
# ---------------------------------------------------------------------------

def _gmail_headers_to_dict(headers: List[Dict[str, str]]) -> Dict[str, str]:
    return {h["name"].lower(): h["value"] for h in headers}


def _gmail_part_text(part: Dict[str, Any]) -> str:
    """Recursively extract plain text from a Gmail message part."""
    if part.get("mimeType") == "text/plain":
        data = (part.get("body") or {}).get("data", "")
        if data:
            import base64
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for sub in part.get("parts") or []:
        text = _gmail_part_text(sub)
        if text:
            return text
    return ""


def _gmail_msg_to_email(msg: Dict[str, Any], include_body: bool = False) -> EmailMessage:
    headers = _gmail_headers_to_dict(msg.get("payload", {}).get("headers", []))
    to_raw = headers.get("to", "")
    to_emails = [e.strip() for e in to_raw.split(",") if e.strip()]
    body = None
    if include_body:
        body = _gmail_part_text(msg.get("payload", {}))
    return EmailMessage(
        id=msg.get("id", ""),
        subject=headers.get("subject", "(no subject)"),
        from_email=headers.get("from", ""),
        to_emails=to_emails,
        date=headers.get("date"),
        snippet=msg.get("snippet"),
        body=body,
        thread_id=msg.get("threadId"),
    )


async def _gmail_list(
    token: str,
    query: str,
    max_results: int,
    include_body: bool = False,
) -> EmailListOutput:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"q": query, "maxResults": max(1, min(max_results, 50))},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        payload = resp.json()

    msg_stubs = payload.get("messages", [])
    messages: List[EmailMessage] = []

    if not msg_stubs:
        return EmailListOutput(success=True, messages=[], count=0, provider="google")

    # Batch fetch message details
    async with httpx.AsyncClient(timeout=30.0) as client:
        for stub in msg_stubs[:max_results]:
            msg_resp = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{stub['id']}",
                params={"format": "full" if include_body else "metadata",
                        "metadataHeaders": "Subject,From,To,Date"},
                headers={"Authorization": f"Bearer {token}"},
            )
            if msg_resp.status_code == 200:
                messages.append(_gmail_msg_to_email(msg_resp.json(), include_body=include_body))

    return EmailListOutput(success=True, messages=messages, count=len(messages), provider="google")


# ---------------------------------------------------------------------------
# Microsoft Graph helpers
# ---------------------------------------------------------------------------

def _msft_msg_to_email(item: Dict[str, Any], include_body: bool = False) -> EmailMessage:
    sender = (item.get("from") or {}).get("emailAddress") or {}
    from_email = f"{sender.get('name', '')} <{sender.get('address', '')}>".strip()
    to_recipients = item.get("toRecipients") or []
    to_emails = [
        r.get("emailAddress", {}).get("address", "") for r in to_recipients if r.get("emailAddress")
    ]
    body = None
    if include_body:
        body_obj = item.get("body") or {}
        body = body_obj.get("content", "")
        # Strip HTML tags if HTML
        if body_obj.get("contentType", "").lower() == "html":
            import re
            body = re.sub(r"<[^>]+>", " ", body)
            body = " ".join(body.split())  # normalize whitespace
    return EmailMessage(
        id=item.get("id", ""),
        subject=item.get("subject", "(no subject)"),
        from_email=from_email,
        to_emails=to_emails,
        date=item.get("sentDateTime") or item.get("receivedDateTime"),
        snippet=item.get("bodyPreview"),
        body=body,
        thread_id=item.get("conversationId"),
    )


async def _msft_list(
    token: str,
    filter_str: str,
    search_str: Optional[str],
    max_results: int,
    include_body: bool = False,
) -> EmailListOutput:
    params: Dict[str, Any] = {
        "$top": max(1, min(max_results, 50)),
        "$orderby": "receivedDateTime desc",
    }
    if filter_str:
        params["$filter"] = filter_str
    if search_str:
        params["$search"] = f'"{search_str}"'

    select_fields = "id,subject,from,toRecipients,sentDateTime,receivedDateTime,bodyPreview,conversationId"
    if include_body:
        select_fields += ",body"
    params["$select"] = select_fields

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/me/messages",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Prefer": 'outlook.body-content-type="text"',
            },
        )
        resp.raise_for_status()
        payload = resp.json()

    messages = [
        _msft_msg_to_email(item, include_body=include_body)
        for item in payload.get("value", [])
    ]
    return EmailListOutput(success=True, messages=messages, count=len(messages), provider="microsoft")


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

async def email_list_recent(
    ctx: RunContext[BusiboxDeps],
    hours: int = 24,
    max_results: int = 20,
) -> EmailListOutput:
    """
    List recent emails from the user's connected inbox (Gmail or Outlook).

    Args:
        hours: How many hours back to look (default 24).
        max_results: Maximum number of emails to return (max 50).
    """
    token, provider = await _get_best_email_token(ctx)
    if not token:
        return EmailListOutput(
            success=False,
            error="No email integration found. Please connect your Google or Microsoft account in the portal settings.",
        )

    since = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
    since_str = since.strftime("%Y/%m/%d")

    try:
        if provider == "google":
            query = f"after:{since_str} in:inbox"
            return await _gmail_list(token, query=query, max_results=max_results)
        else:
            since_iso = since.isoformat()
            return await _msft_list(
                token,
                filter_str=f"receivedDateTime ge {since_iso}",
                search_str=None,
                max_results=max_results,
            )
    except Exception as e:
        return EmailListOutput(success=False, error=str(e), provider=provider)


async def email_search(
    ctx: RunContext[BusiboxDeps],
    query: str,
    max_results: int = 20,
    from_email: Optional[str] = None,
    subject: Optional[str] = None,
    days_back: int = 30,
) -> EmailListOutput:
    """
    Search the user's email inbox.

    Args:
        query: Free-text search keywords.
        max_results: Maximum emails to return (max 50).
        from_email: Filter by sender email address.
        subject: Filter by subject keywords.
        days_back: How many days back to search (default 30).
    """
    token, provider = await _get_best_email_token(ctx)
    if not token:
        return EmailListOutput(
            success=False,
            error="No email integration found. Please connect your Google or Microsoft account in the portal settings.",
        )

    since = datetime.now(timezone.utc) - timedelta(days=max(1, days_back))

    try:
        if provider == "google":
            parts = [query] if query else []
            if from_email:
                parts.append(f"from:{from_email}")
            if subject:
                parts.append(f"subject:{subject}")
            since_str = since.strftime("%Y/%m/%d")
            parts.append(f"after:{since_str}")
            gmail_query = " ".join(parts)
            return await _gmail_list(token, query=gmail_query, max_results=max_results)
        else:
            since_iso = since.isoformat()
            filter_parts = [f"receivedDateTime ge {since_iso}"]
            if from_email:
                filter_parts.append(f"from/emailAddress/address eq '{from_email}'")
            filter_str = " and ".join(filter_parts)
            return await _msft_list(
                token,
                filter_str=filter_str,
                search_str=query or subject,
                max_results=max_results,
            )
    except Exception as e:
        return EmailListOutput(success=False, error=str(e), provider=provider)


async def email_read(
    ctx: RunContext[BusiboxDeps],
    message_id: str,
) -> EmailReadOutput:
    """
    Read the full content of a specific email message by its ID.

    Use message IDs returned by email_list_recent or email_search.
    """
    token, provider = await _get_best_email_token(ctx)
    if not token:
        return EmailReadOutput(
            success=False,
            error="No email integration found.",
        )

    try:
        if provider == "google":
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                    params={"format": "full"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                msg = _gmail_msg_to_email(resp.json(), include_body=True)
            return EmailReadOutput(
                success=True,
                messages=[msg],
                subject=msg.subject,
                count=1,
                provider="google",
            )
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"https://graph.microsoft.com/v1.0/me/messages/{message_id}",
                    params={
                        "$select": "id,subject,from,toRecipients,sentDateTime,receivedDateTime,body,conversationId"
                    },
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Prefer": 'outlook.body-content-type="text"',
                    },
                )
                resp.raise_for_status()
                msg = _msft_msg_to_email(resp.json(), include_body=True)
            return EmailReadOutput(
                success=True,
                messages=[msg],
                subject=msg.subject,
                count=1,
                provider="microsoft",
            )
    except Exception as e:
        return EmailReadOutput(success=False, error=str(e), provider=provider)


# ---------------------------------------------------------------------------
# Tool objects
# ---------------------------------------------------------------------------

email_list_recent_tool = Tool(
    email_list_recent,
    takes_ctx=True,
    name="email_list_recent",
    description="List recent emails from the user's connected inbox (Gmail or Outlook).",
)

email_search_tool = Tool(
    email_search,
    takes_ctx=True,
    name="email_search",
    description=(
        "Search the user's email inbox by keywords, sender, subject, or date range. "
        "Returns matching email metadata and snippets."
    ),
)

email_read_tool = Tool(
    email_read,
    takes_ctx=True,
    name="email_read",
    description="Read the full content of a specific email message by its ID.",
)
