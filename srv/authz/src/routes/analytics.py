"""
Analytics endpoints for app usage and satisfaction tracking.

Provides admin-only endpoints that aggregate audit log data to produce
per-app usage metrics and user satisfaction (feedback) data.

Protected by:
- Access token (JWT) with audience=authz-api and authz.audit.read scope

Endpoints:
- GET /admin/analytics/apps           - Per-app usage summary
- GET /admin/analytics/apps/{app_id}  - Detailed app usage
- GET /admin/analytics/feedback       - Aggregated satisfaction scores
- GET /admin/analytics/feedback/{app_id} - Per-app feedback detail
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request

from config import Config
from oauth.jwt_auth import require_auth

router = APIRouter()
config = Config()

# PostgresService instances - will be set by main.py
pg = None
pg_test = None

TEST_MODE_HEADER = "X-Test-Mode"


def set_pg_service(pg_service, pg_test_service=None):
    """Set the shared PostgresService instances."""
    global pg, pg_test
    pg = pg_service
    pg_test = pg_test_service


def _get_pg(request: Request):
    """Return the appropriate PostgresService based on test-mode header."""
    if pg_test and config.test_mode_enabled:
        if request.headers.get(TEST_MODE_HEADER, "").lower() == "true":
            return pg_test
    return pg


async def _require_admin_auth(request: Request):
    """Require admin-level authentication (authz.audit.read scope)."""
    db = _get_pg(request)
    return await require_auth(request, db, scopes=["authz.audit.read"])


# =============================================================================
# App Usage Endpoints
# =============================================================================


@router.get("/admin/analytics/apps")
async def get_app_usage_summary(request: Request):
    """
    Return per-app usage summary derived from oauth.token.issued audit events.

    Query params:
    - days: int (default: 30)

    Each entry includes:
    - app_id
    - requests_today, requests_7d, requests_30d
    - unique_users_today, unique_users_7d, unique_users_30d
    - daily_trend: list of {date, requests, unique_users} for the past `days` days
    """
    await _require_admin_auth(request)

    days = int(request.query_params.get("days", "30"))
    db = _get_pg(request)
    await db.connect()

    data = await db.get_app_usage_summary(days=days)
    return {"apps": data}


@router.get("/admin/analytics/apps/{app_id}")
async def get_app_usage_detail(request: Request, app_id: str):
    """
    Return detailed usage for a single app.

    Query params:
    - days: int (default: 30)

    Returns:
    - daily_active_users: list of {date, unique_users, requests}
    - hourly_distribution: list of {hour, requests} (0-23, across the period)
    - top_users: list of {user_id, requests}
    """
    await _require_admin_auth(request)

    days = int(request.query_params.get("days", "30"))
    db = _get_pg(request)
    await db.connect()

    data = await db.get_app_usage_detail(app_id=app_id, days=days)
    return {"app_id": app_id, **data}


# =============================================================================
# Feedback Endpoints
# =============================================================================


@router.get("/admin/analytics/feedback")
async def get_feedback_summary(request: Request):
    """
    Return aggregated satisfaction feedback across all apps.

    Query params:
    - app_id: string (optional) — filter to a single app
    - from_date: ISO timestamp (optional)
    - to_date: ISO timestamp (optional)

    Returns per-app:
    - app_id
    - positive, neutral, negative counts
    - satisfaction_score: (positive - negative) / total * 100
    - recent_comments: last 5 non-empty comments
    - weekly_trend: list of {week, positive, neutral, negative}
    """
    await _require_admin_auth(request)

    params = request.query_params
    app_id: Optional[str] = params.get("app_id")
    from_date: Optional[str] = params.get("from_date")
    to_date: Optional[str] = params.get("to_date")

    db = _get_pg(request)
    await db.connect()

    data = await db.get_feedback_summary(
        app_id=app_id,
        from_date=from_date,
        to_date=to_date,
    )
    return {"feedback": data}


@router.get("/admin/analytics/feedback/{app_id}")
async def get_app_feedback_detail(request: Request, app_id: str):
    """
    Return full feedback history for a single app.

    Query params:
    - from_date: ISO timestamp (optional)
    - to_date: ISO timestamp (optional)
    - limit: int (default: 100)

    Returns:
    - summary: {positive, neutral, negative, satisfaction_score}
    - entries: list of {id, rating, comment, actor_id, created_at}
    - weekly_trend: list of {week, positive, neutral, negative}
    """
    await _require_admin_auth(request)

    params = request.query_params
    from_date: Optional[str] = params.get("from_date")
    to_date: Optional[str] = params.get("to_date")
    limit = int(params.get("limit", "100"))

    db = _get_pg(request)
    await db.connect()

    data = await db.get_app_feedback_detail(
        app_id=app_id,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
    )
    return {"app_id": app_id, **data}
