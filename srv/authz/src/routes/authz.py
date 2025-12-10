"""
Authorization and audit endpoints (standalone authz service).
"""

from typing import List, Optional

import jwt
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from config import Config
from services.postgres import PostgresService

router = APIRouter()
logger = structlog.get_logger()

config = Config()
JWT_SECRET = config.jwt_secret
JWT_ISSUER = config.jwt_issuer
JWT_AUDIENCE = config.jwt_audience
DEFAULT_TTL_SECONDS = config.authz_token_ttl


def issue_token(user_id: str, roles: List[dict], audience: str = JWT_AUDIENCE, ttl_seconds: int = DEFAULT_TTL_SECONDS):
    payload = {
        "sub": user_id,
        "roles": roles,
        "aud": audience,
        "iss": JWT_ISSUER,
        "typ": "access",
    }
    # exp handled by jwt.encode options
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return token


def _extract_actor_from_headers(request: Request) -> tuple[Optional[str], List[str]]:
    """
    Best-effort extract actor and roles from Authorization header if present.
    Token is expected to be HS256 JWT with sub and roles fields.
    """
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None, []
    token = auth.split(" ", 1)[1]
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], audience=JWT_AUDIENCE, options={"verify_exp": False})
        actor = data.get("sub")
        roles = data.get("roles") or []
        return actor, roles
    except Exception:
        return None, []


@router.post("/authz/token")
async def create_token(request: Request):
    """
    Issue a scoped JWT for downstream services (search, ingest, agent).
    Body:
      - userId: str
      - roles: [{ id, name, permissions: ['read','create','update','delete'] }]
      - audience: optional
    """
    body = await request.json()
    user_id = body.get("userId")
    roles = body.get("roles", [])
    audience = body.get("audience", JWT_AUDIENCE)

    if not user_id or not isinstance(user_id, str):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": "userId required"})
    if not isinstance(roles, list):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": "roles must be a list"})

    token = issue_token(user_id, roles, audience)

    # Audit
    await write_audit(
        actor_id=user_id,
        action="authz.token.issued",
        resource_type="authz_token",
        resource_id=None,
        details={"audience": audience, "role_count": len(roles)},
        request=request,
    )

    return {"token": token, "audience": audience, "roles": roles}


@router.post("/authz/audit")
async def audit(request: Request):
    """
    Append an audit log entry.
    Body: { actorId, action, resourceType, resourceId?, details? }
    """
    body = await request.json()
    actor_id = body.get("actorId")
    action = body.get("action")
    resource_type = body.get("resourceType")
    resource_id = body.get("resourceId")
    details = body.get("details", {})

    if not actor_id or not action or not resource_type:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "actorId, action, and resourceType are required"}
        )

    await write_audit(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        request=request,
    )

    return {"status": "ok"}


async def write_audit(actor_id: str, action: str, resource_type: str, resource_id: Optional[str], details: dict, request: Request):
    """Insert audit log entry."""
    cfg = config.to_dict()
    pg = PostgresService(cfg)
    await pg.connect()

    # attempt to propagate caller context if supplied
    caller_user, caller_roles = _extract_actor_from_headers(request)
    await pg.insert_audit(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        user_id=caller_user or actor_id,
        role_ids=[r.get("id") for r in caller_roles if isinstance(r, dict) and r.get("id")] if isinstance(caller_roles, list) else [],
    )

