"""
User search tool for AI agents.

Allows agents to search for users by name or email via the authz service,
enabling mapping of freeform assignee names to actual user IDs.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from app.agents.core import BusiboxDeps
from app.config.settings import get_settings

logger = logging.getLogger(__name__)


class UserSearchOutput(BaseModel):
    """Output from searching users."""
    success: bool = Field(description="Whether the search succeeded")
    users: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of matching users with id, email, display_name, first_name, last_name",
    )
    total: int = Field(default=0, description="Total matching users")
    error: Optional[str] = Field(default=None, description="Error message if failed")


async def search_users(
    ctx: RunContext[BusiboxDeps],
    query: str,
    limit: int = 10,
) -> UserSearchOutput:
    """
    Search for users by name or email.

    Uses the authz admin/users endpoint with the search parameter,
    which matches against email, display_name, first_name, last_name,
    and the concatenated first+last name.

    Args:
        ctx: RunContext with BusiboxDeps
        query: Search string (name or email fragment)
        limit: Maximum results to return (default 10)
    """
    try:
        settings = get_settings()
        authz_base = str(settings.auth_token_url).rsplit("/oauth/token", 1)[0]

        token = ctx.deps.busibox_client._tokens.get(
            "authz-api",
            ctx.deps.busibox_client._default_token,
        )

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{authz_base}/admin/users",
                params={"search": query, "limit": limit, "page": 1},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

        raw_users = data.get("users", [])
        users = []
        for u in raw_users[:limit]:
            users.append({
                "id": u.get("id") or u.get("user_id"),
                "email": u.get("email"),
                "display_name": u.get("display_name"),
                "first_name": u.get("first_name"),
                "last_name": u.get("last_name"),
            })

        return UserSearchOutput(
            success=True,
            users=users,
            total=data.get("pagination", {}).get("total_count", len(users)),
        )

    except httpx.HTTPStatusError as exc:
        logger.error("User search HTTP error: %s", exc, exc_info=True)
        return UserSearchOutput(
            success=False,
            error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
        )
    except Exception as exc:
        logger.error("User search failed: %s", exc, exc_info=True)
        return UserSearchOutput(success=False, error=str(exc))
