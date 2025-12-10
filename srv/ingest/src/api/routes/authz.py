"""
Authorization and audit endpoints.

Provides:
- POST /authz/token : issue scoped JWT for downstream services
- POST /authz/audit : append audit log entry
"""

import os
import uuid
from typing import List, Optional

import jwt
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from api.services.postgres import PostgresService
from shared.config import Config

logger = structlog.get_logger()

router = APIRouter()

JWT_SECRET = os.environ.get("JWT_SECRET") or \
             os.environ.get("SERVICE_JWT_SECRET") or \
             os.environ.get("SSO_JWT_SECRET") or \
             "default-service-secret-change-in-production"

JWT_ISSUER = os.environ.get("JWT_ISSUER", "authz-service")
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "busibox-services")
DEFAULT_TTL_SECONDS = int(os.environ.get("AUTHZ_TOKEN_TTL", "900"))  # 15 minutes


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
    config = Config().to_dict()
    pg = PostgresService(config, request)
    await pg.connect()
    async with pg.acquire(request) as conn:
        await conn.execute(
            """
            INSERT INTO audit_logs (actor_id, action, resource_type, resource_id, details)
            VALUES ($1, $2, $3, $4, $5)
            """,
            uuid.UUID(actor_id),
            action,
            resource_type,
            uuid.UUID(resource_id) if resource_id else None,
            details,
        )

