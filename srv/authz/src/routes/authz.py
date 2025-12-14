"""
Audit endpoints (standalone authz service).

Token issuance has moved to OAuth2-style endpoints in `routes/oauth.py`.
"""

import json
from typing import Optional, List

import jwt
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from config import Config
from services.postgres import PostgresService

router = APIRouter()
logger = structlog.get_logger()

config = Config()
JWT_ISSUER = config.issuer


async def _extract_actor_from_headers(pg: PostgresService, request: Request) -> tuple[Optional[str], List[dict]]:
    """
    Best-effort extract actor and roles from Authorization header if present.
    Token is expected to be a signed JWT with `sub` and `roles` fields.
    """
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None, []
    token = auth.split(" ", 1)[1]
    try:
        headers = jwt.get_unverified_header(token)
        kid = headers.get("kid")
        if not kid:
            return None, []
        # Look up public key via active JWKS keys stored in DB.
        jwks = await pg.list_public_jwks()
        key_data = next((k for k in jwks if k.get("kid") == kid), None)
        if not key_data:
            return None, []
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
        data = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=JWT_ISSUER,
            options={"verify_aud": False},  # audit context does not require audience
        )
        actor = data.get("sub")
        roles = data.get("roles") or []
        return actor, roles
    except Exception:
        return None, []


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
    caller_user, caller_roles = await _extract_actor_from_headers(pg, request)
    await pg.insert_audit(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        user_id=caller_user or actor_id,
        role_ids=[r.get("id") for r in caller_roles if isinstance(r, dict) and r.get("id")] if isinstance(caller_roles, list) else [],
    )





