"""
Internal-only endpoints used by first-party services (ai-portal) to sync RBAC state.

These endpoints are protected either by:
- OAuth client credentials in request body (client_id/client_secret), or
- a shared admin token (AUTHZ_ADMIN_TOKEN) for manual/bootstrap operations.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from config import Config
from oauth.client_auth import verify_client_secret
from oauth.contracts import SyncUser

router = APIRouter()
config = Config()

# PostgresService instance - will be set by main.py
pg = None

def set_pg_service(pg_service):
    """Set the shared PostgresService instance."""
    global pg
    pg = pg_service


async def _require_oauth_client(body: dict) -> dict:
    client_id = body.get("client_id")
    client_secret = body.get("client_secret")
    if not client_id or not client_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_client")
    await pg.connect()
    client = await pg.get_oauth_client(client_id)
    if not client or not client.get("is_active"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_client")
    if not verify_client_secret(client_secret, client["client_secret_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_client")
    return client


@router.post("/internal/sync/user")
async def sync_user(request: Request):
    """
    Upsert user + roles + user_role assignments in authz.
    Called by ai-portal (server-to-server).
    """
    body = await request.json()
    await _require_oauth_client(body)

    # accept payload nested under `user` or directly
    payload = body.get("user") or body
    try:
        su = SyncUser.model_validate(payload)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request") from e

    await pg.connect()
    await pg.upsert_roles([r.model_dump() for r in su.roles])
    await pg.upsert_user_and_roles(
        user_id=su.user_id,
        email=su.email,
        status=su.status,
        idp_provider=su.idp_provider,
        idp_tenant_id=su.idp_tenant_id,
        idp_object_id=su.idp_object_id,
        idp_roles=su.idp_roles,
        idp_groups=su.idp_groups,
        user_role_ids=su.user_role_ids,
    )

    return {"status": "ok"}

