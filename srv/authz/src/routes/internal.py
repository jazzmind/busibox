"""
Internal-only endpoints used by first-party services (ai-portal) to sync RBAC state.

These endpoints are protected either by:
- OAuth client credentials in request body (client_id/client_secret), or
- a shared admin token (AUTHZ_ADMIN_TOKEN) for manual/bootstrap operations.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

import uuid

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
    # Upsert roles and get mapping of role names to IDs
    role_name_to_id = await pg.upsert_roles([r.model_dump() for r in su.roles])
    
    # Resolve user_role_ids: if provided IDs don't exist, try to resolve by role name
    resolved_role_ids = []
    for role_id_or_name in su.user_role_ids:
        # Check if it's a UUID (role ID)
        try:
            uuid.UUID(role_id_or_name)
            # It's a UUID, check if it exists
            role = await pg.get_role_by_id(role_id_or_name)
            if role:
                resolved_role_ids.append(role_id_or_name)
            else:
                # UUID doesn't exist, try to find by name
                role = await pg.get_role_by_name(role_id_or_name)
                if role:
                    resolved_role_ids.append(role["id"])
                else:
                    # Still not found, check if it's in the roles we just upserted
                    if role_id_or_name in [r["id"] for r in su.roles]:
                        # It's a role we just upserted, use it directly
                        resolved_role_ids.append(role_id_or_name)
        except ValueError:
            # Not a valid UUID, treat as role name
            role = await pg.get_role_by_name(role_id_or_name)
            if role:
                resolved_role_ids.append(role["id"])
            elif role_id_or_name in role_name_to_id:
                # Use the ID from the roles we just upserted (by name)
                resolved_role_ids.append(role_name_to_id[role_id_or_name])
            else:
                # Check if it's a role ID we just upserted (even if not a valid UUID format)
                if role_id_or_name in [r["id"] for r in su.roles]:
                    resolved_role_ids.append(role_id_or_name)
    
    await pg.upsert_user_and_roles(
        user_id=su.user_id,
        email=su.email,
        status=su.status,
        idp_provider=su.idp_provider,
        idp_tenant_id=su.idp_tenant_id,
        idp_object_id=su.idp_object_id,
        idp_roles=su.idp_roles,
        idp_groups=su.idp_groups,
        user_role_ids=resolved_role_ids,
    )

    return {"status": "ok"}

