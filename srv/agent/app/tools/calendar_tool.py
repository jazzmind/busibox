"""
Calendar toolset for agents.

Supports both Google Calendar and Microsoft Graph Calendar, using per-user
OAuth tokens retrieved from the authz integration store.

Falls back to the legacy GOOGLE_CALENDAR_ACCESS_TOKEN env var when no user
integration is connected (backward-compatible).
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Tool, RunContext

from app.agents.core import BusiboxDeps
from app.config.settings import get_settings

settings = get_settings()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class CalendarEvent(BaseModel):
    id: str
    summary: str
    start: str
    end: str
    html_link: Optional[str] = None
    attendees: Optional[List[str]] = None
    location: Optional[str] = None
    description: Optional[str] = None


class CalendarListOutput(BaseModel):
    success: bool
    events: List[CalendarEvent] = Field(default_factory=list)
    count: int = 0
    provider: Optional[str] = None
    error: Optional[str] = None


class CalendarCreateOutput(BaseModel):
    success: bool
    event: Optional[CalendarEvent] = None
    provider: Optional[str] = None
    error: Optional[str] = None


class CalendarAvailabilitySlot(BaseModel):
    start: str
    end: str
    busy: bool


class CalendarAvailabilityOutput(BaseModel):
    success: bool
    slots: List[CalendarAvailabilitySlot] = Field(default_factory=list)
    provider: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _authz_base_url() -> str:
    """Derive authz base URL from the configured token exchange URL."""
    token_url = str(settings.auth_token_url)
    parsed = urlparse(token_url)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _get_oauth_token(user_id: str, provider: str) -> Optional[str]:
    """
    Retrieve a valid OAuth access token for the user from authz.
    Returns None if no integration is connected.
    """
    authz_url = _authz_base_url()
    # We need to authenticate to authz as the agent-api service.
    # Use the agent-api's own token (exchanged for authz-api audience).
    # Since agent-api calls authz internally, it can use the user's principal token.
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # The internal token endpoint expects a bearer token scoped to authz-api.
            # We use the user's token here (the agent's ctx has it via principal.token).
            resp = await client.get(
                f"{authz_url}/internal/integrations/{provider}/token",
                headers={"Authorization": f"Bearer {user_id}"},
            )
            if resp.status_code == 200:
                return resp.json().get("access_token")
    except Exception:
        pass
    return None


async def _get_user_token(ctx: RunContext[BusiboxDeps], provider: str) -> tuple[Optional[str], str]:
    """
    Return (access_token, provider_name) for the user.
    Tries integration store first, then falls back to env vars.
    """
    principal = ctx.deps.principal
    user_id = principal.sub
    user_token = principal.token

    # Try per-user OAuth token from authz integration store
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

    # Fallback: legacy env var (google only)
    if provider == "google":
        env_token = os.environ.get("GOOGLE_CALENDAR_ACCESS_TOKEN")
        if env_token:
            return env_token, "google"

    return None, provider


# ---------------------------------------------------------------------------
# Google Calendar helpers
# ---------------------------------------------------------------------------

def _google_to_event(item: Dict[str, Any]) -> CalendarEvent:
    start = (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date") or ""
    end = (item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date") or ""
    attendees = [
        a.get("email", "") for a in (item.get("attendees") or []) if a.get("email")
    ]
    return CalendarEvent(
        id=str(item.get("id", "")),
        summary=str(item.get("summary", "")),
        start=start,
        end=end,
        html_link=item.get("htmlLink"),
        attendees=attendees or None,
        location=item.get("location"),
        description=item.get("description"),
    )


async def _google_list_events(
    token: str,
    time_min: Optional[str],
    time_max: Optional[str],
    max_results: int,
    calendar_id: str = "primary",
) -> CalendarListOutput:
    params: Dict[str, Any] = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max(1, min(max_results, 50)),
        "timeMin": time_min or (datetime.now(timezone.utc).isoformat() + "Z"),
    }
    if time_max:
        params["timeMax"] = time_max

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        payload = response.json()

    events = [_google_to_event(item) for item in payload.get("items", [])]
    return CalendarListOutput(success=True, events=events, count=len(events), provider="google")


# ---------------------------------------------------------------------------
# Microsoft Graph Calendar helpers
# ---------------------------------------------------------------------------

def _microsoft_to_event(item: Dict[str, Any]) -> CalendarEvent:
    start = (item.get("start") or {}).get("dateTime", "")
    end = (item.get("end") or {}).get("dateTime", "")
    attendees = [
        a.get("emailAddress", {}).get("address", "")
        for a in (item.get("attendees") or [])
    ]
    return CalendarEvent(
        id=str(item.get("id", "")),
        summary=str(item.get("subject", "")),
        start=start,
        end=end,
        html_link=item.get("webLink"),
        attendees=[a for a in attendees if a] or None,
        location=(item.get("location") or {}).get("displayName"),
        description=item.get("bodyPreview"),
    )


async def _microsoft_list_events(
    token: str,
    time_min: Optional[str],
    time_max: Optional[str],
    max_results: int,
) -> CalendarListOutput:
    start = time_min or datetime.now(timezone.utc).isoformat() + "Z"
    # Microsoft Graph requires OData filter for date range
    filter_parts = [f"start/dateTime ge '{start}'"]
    if time_max:
        filter_parts.append(f"end/dateTime le '{time_max}'")

    params: Dict[str, Any] = {
        "$top": max(1, min(max_results, 50)),
        "$orderby": "start/dateTime asc",
        "$filter": " and ".join(filter_parts),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            "https://graph.microsoft.com/v1.0/me/events",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Prefer": 'outlook.timezone="UTC"',
            },
        )
        response.raise_for_status()
        payload = response.json()

    events = [_microsoft_to_event(item) for item in payload.get("value", [])]
    return CalendarListOutput(success=True, events=events, count=len(events), provider="microsoft")


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

async def calendar_list_events(
    ctx: RunContext[BusiboxDeps],
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
) -> CalendarListOutput:
    """
    List upcoming calendar events from the user's connected calendar (Google or Microsoft).

    Datetime format: RFC3339, e.g. 2026-02-18T00:00:00Z
    """
    # Try Google first, then Microsoft
    for provider in ("google", "microsoft"):
        token, pname = await _get_user_token(ctx, provider)
        if token:
            try:
                if pname == "google":
                    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
                    return await _google_list_events(token, time_min, time_max, max_results, calendar_id)
                else:
                    return await _microsoft_list_events(token, time_min, time_max, max_results)
            except Exception as e:
                return CalendarListOutput(success=False, error=str(e), provider=pname)

    return CalendarListOutput(
        success=False,
        error=(
            "No calendar integration found. "
            "Please connect your Google or Microsoft account in the portal settings."
        ),
    )


async def calendar_create_event(
    ctx: RunContext[BusiboxDeps],
    summary: str,
    start: str,
    end: str,
    description: Optional[str] = None,
    timezone: str = "UTC",
    attendee_emails: Optional[List[str]] = None,
) -> CalendarCreateOutput:
    """
    Create a calendar event. Uses the user's connected Google or Microsoft calendar.

    start/end should be RFC3339 datetimes (e.g. 2026-02-18T15:00:00Z).
    attendee_emails: optional list of attendee email addresses.
    """
    # Try Google first, then Microsoft
    for provider in ("google", "microsoft"):
        token, pname = await _get_user_token(ctx, provider)
        if token:
            try:
                if pname == "google":
                    return await _google_create_event(
                        token, summary, start, end, description, timezone, attendee_emails
                    )
                else:
                    return await _microsoft_create_event(
                        token, summary, start, end, description, timezone, attendee_emails
                    )
            except Exception as e:
                return CalendarCreateOutput(success=False, error=str(e), provider=pname)

    return CalendarCreateOutput(
        success=False,
        error=(
            "No calendar integration found. "
            "Please connect your Google or Microsoft account in the portal settings."
        ),
    )


async def _google_create_event(
    token: str,
    summary: str,
    start: str,
    end: str,
    description: Optional[str],
    tz: str,
    attendee_emails: Optional[List[str]],
) -> CalendarCreateOutput:
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    payload: Dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": tz},
        "end": {"dateTime": end, "timeZone": tz},
    }
    if description:
        payload["description"] = description
    if attendee_emails:
        payload["attendees"] = [{"email": e} for e in attendee_emails]

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        item = response.json()

    return CalendarCreateOutput(success=True, event=_google_to_event(item), provider="google")


async def _microsoft_create_event(
    token: str,
    summary: str,
    start: str,
    end: str,
    description: Optional[str],
    tz: str,
    attendee_emails: Optional[List[str]],
) -> CalendarCreateOutput:
    payload: Dict[str, Any] = {
        "subject": summary,
        "start": {"dateTime": start, "timeZone": tz},
        "end": {"dateTime": end, "timeZone": tz},
    }
    if description:
        payload["body"] = {"contentType": "Text", "content": description}
    if attendee_emails:
        payload["attendees"] = [
            {"emailAddress": {"address": e}, "type": "required"}
            for e in attendee_emails
        ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://graph.microsoft.com/v1.0/me/events",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        item = response.json()

    return CalendarCreateOutput(success=True, event=_microsoft_to_event(item), provider="microsoft")


async def calendar_get_availability(
    ctx: RunContext[BusiboxDeps],
    time_min: str,
    time_max: str,
) -> CalendarAvailabilityOutput:
    """
    Get the user's free/busy availability for a time range.
    Returns a list of busy slots.

    Datetime format: RFC3339, e.g. 2026-02-18T09:00:00Z
    """
    for provider in ("google", "microsoft"):
        token, pname = await _get_user_token(ctx, provider)
        if token:
            try:
                if pname == "google":
                    return await _google_freebusy(token, time_min, time_max)
                else:
                    return await _microsoft_freebusy(token, time_min, time_max)
            except Exception as e:
                return CalendarAvailabilityOutput(success=False, error=str(e), provider=pname)

    return CalendarAvailabilityOutput(
        success=False,
        error="No calendar integration found.",
    )


async def _google_freebusy(
    token: str, time_min: str, time_max: str
) -> CalendarAvailabilityOutput:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://www.googleapis.com/calendar/v3/freeBusy",
            json={
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": os.environ.get("GOOGLE_CALENDAR_ID", "primary")}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        payload = response.json()

    busy_periods = payload.get("calendars", {}).get(
        os.environ.get("GOOGLE_CALENDAR_ID", "primary"), {}
    ).get("busy", [])

    slots = [
        CalendarAvailabilitySlot(start=b["start"], end=b["end"], busy=True)
        for b in busy_periods
    ]
    return CalendarAvailabilityOutput(success=True, slots=slots, provider="google")


async def _microsoft_freebusy(
    token: str, time_min: str, time_max: str
) -> CalendarAvailabilityOutput:
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Use me/calendarView to get events in range as proxy for busy slots
        response = await client.get(
            "https://graph.microsoft.com/v1.0/me/calendarView",
            params={"startDateTime": time_min, "endDateTime": time_max, "$top": 50},
            headers={"Authorization": f"Bearer {token}", "Prefer": 'outlook.timezone="UTC"'},
        )
        response.raise_for_status()
        payload = response.json()

    slots = []
    for item in payload.get("value", []):
        if item.get("showAs") in ("busy", "oof", "tentative"):
            start = (item.get("start") or {}).get("dateTime", "")
            end = (item.get("end") or {}).get("dateTime", "")
            if start and end:
                slots.append(CalendarAvailabilitySlot(start=start, end=end, busy=True))

    return CalendarAvailabilityOutput(success=True, slots=slots, provider="microsoft")


# ---------------------------------------------------------------------------
# Tool objects
# ---------------------------------------------------------------------------

calendar_list_events_tool = Tool(
    calendar_list_events,
    takes_ctx=True,
    name="calendar_list_events",
    description=(
        "List upcoming calendar events from the user's connected calendar "
        "(Google Calendar or Microsoft Outlook). Supports time filtering."
    ),
)

calendar_create_event_tool = Tool(
    calendar_create_event,
    takes_ctx=True,
    name="calendar_create_event",
    description=(
        "Create a calendar event on the user's connected calendar "
        "(Google or Microsoft). Optionally invite attendees by email."
    ),
)

calendar_get_availability_tool = Tool(
    calendar_get_availability,
    takes_ctx=True,
    name="calendar_get_availability",
    description=(
        "Get the user's busy/free calendar availability for a time range. "
        "Useful for scheduling meetings."
    ),
)
