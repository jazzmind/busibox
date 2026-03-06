"""
Self-service role management endpoints.

Allows any authenticated user (via session JWT) to create and manage
roles they own. Distinct from admin endpoints (/admin/roles) which
manage all roles globally and require admin scopes.

Naming convention:
    app:{appName}:{roleName}

Security:
    - Scopes on self-service roles are restricted to an allow-list
    - Only the role creator can update, delete, or manage members
    - Session JWT auth (no admin scopes required)
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from config import Config
from oauth.jwt_auth import authenticate_self_service, require_auth, AuthContext

logger = logging.getLogger(__name__)

router = APIRouter(tags=["roles"])
config = Config()

pg = None
pg_test = None

TEST_MODE_HEADER = "X-Test-Mode"

SELF_SERVICE_ALLOWED_SCOPES = {
    "data:read", "data:write",
    "search:read", "search:write",
    "graph:read", "graph:write",
    "libraries:read", "libraries:write",
}

ROLE_NAME_PATTERN = re.compile(r"^app:[a-z0-9][a-z0-9._-]*:[a-z0-9][a-z0-9._-]*$")


def set_pg_service(pg_service, pg_test_service=None):
    global pg, pg_test
    pg = pg_service
    pg_test = pg_test_service


def _get_pg(request: Request):
    if pg_test and config.test_mode_enabled:
        if request.headers.get(TEST_MODE_HEADER, "").lower() == "true":
            return pg_test
    return pg


def _validate_name(name: str) -> str:
    """Validate and return the source_app extracted from a self-service role name."""
    if not ROLE_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Role name must match pattern app:{appName}:{roleName} "
                "using lowercase alphanumeric, dots, hyphens, and underscores."
            ),
        )
    parts = name.split(":", 2)
    return parts[1]


def _validate_scopes(scopes: List[str]) -> None:
    disallowed = set(scopes) - SELF_SERVICE_ALLOWED_SCOPES
    if disallowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Disallowed scopes for self-service roles: {sorted(disallowed)}. "
                   f"Allowed: {sorted(SELF_SERVICE_ALLOWED_SCOPES)}",
        )


async def _require_owner(db, role_id: str, actor_id: str) -> dict:
    """Load a role and verify the caller is the creator."""
    role = await db.get_role(role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if role.get("created_by") != actor_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the role creator can perform this action")
    return role


def _role_response(role: dict) -> dict:
    return {
        "id": role["id"],
        "name": role["name"],
        "description": role.get("description"),
        "scopes": role.get("scopes") or [],
        "source_app": role.get("source_app"),
        "created_by": role.get("created_by"),
        "created_at": role["created_at"].isoformat() if role.get("created_at") else "",
        "updated_at": role["updated_at"].isoformat() if role.get("updated_at") else "",
    }


# ============================================================================
# Request / Response Models
# ============================================================================

class SelfServiceRoleCreate(BaseModel):
    name: str = Field(..., min_length=5, max_length=255)
    description: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)


class SelfServiceRoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=5, max_length=255)
    description: Optional[str] = None
    scopes: Optional[List[str]] = None


class AddMemberRequest(BaseModel):
    userId: str


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/roles")
async def create_role(request: Request):
    """
    Create a self-service role.

    The caller is automatically assigned to the newly created role.
    Name must follow ``app:{appName}:{roleName}`` pattern.
    """
    db = _get_pg(request)
    auth: AuthContext = await authenticate_self_service(request, db)

    body = await request.json()
    try:
        data = SelfServiceRoleCreate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    source_app = _validate_name(data.name)
    if data.scopes:
        _validate_scopes(data.scopes)

    role = await db.create_role(
        name=data.name,
        description=data.description,
        scopes=data.scopes,
        created_by=auth.actor_id,
        source_app=source_app,
    )

    await db.add_user_role(user_id=auth.actor_id, role_id=role["id"])

    logger.info("Self-service role created: %s by user %s", role["id"], auth.actor_id)
    return _role_response(role)


@router.get("/roles")
async def list_roles(request: Request, app: Optional[str] = None):
    """
    List roles accessible to the caller.

    Returns roles the user created or is assigned to.
    Optionally filter by ``?app={appName}``.
    If the caller has ``authz.roles.read`` scope, returns all roles instead.
    """
    db = _get_pg(request)

    # Try admin auth first (access token with scope)
    try:
        admin_auth = await require_auth(request, db, scopes=["authz.roles.read"])
        if app:
            roles = await db.get_roles_by_source_app(app)
        else:
            roles = await db.list_roles()
        return [_role_response(r) for r in roles]
    except HTTPException:
        pass

    auth: AuthContext = await authenticate_self_service(request, db)
    roles = await db.get_user_accessible_roles(auth.actor_id, source_app=app)
    return [_role_response(r) for r in roles]


@router.get("/roles/{role_id}")
async def get_role(request: Request, role_id: str):
    """Get a role by ID (must be creator or assigned)."""
    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    db = _get_pg(request)
    auth: AuthContext = await authenticate_self_service(request, db)

    role = await db.get_role(role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    accessible = await db.get_user_accessible_roles(auth.actor_id)
    if not any(r["id"] == role_id for r in accessible):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    return _role_response(role)


@router.put("/roles/{role_id}")
async def update_role(request: Request, role_id: str):
    """Update a self-service role (creator only)."""
    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    db = _get_pg(request)
    auth: AuthContext = await authenticate_self_service(request, db)
    await _require_owner(db, role_id, auth.actor_id)

    body = await request.json()
    try:
        data = SelfServiceRoleUpdate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    if data.name is None and data.description is None and data.scopes is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one field must be provided")

    if data.name is not None:
        _validate_name(data.name)
    if data.scopes is not None:
        _validate_scopes(data.scopes)

    updated = await db.update_role(role_id=role_id, name=data.name, description=data.description, scopes=data.scopes)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    return _role_response(updated)


@router.delete("/roles/{role_id}")
async def delete_role(request: Request, role_id: str):
    """Delete a self-service role (creator only). Cascades to user-role bindings."""
    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    db = _get_pg(request)
    auth: AuthContext = await authenticate_self_service(request, db)
    await _require_owner(db, role_id, auth.actor_id)

    deleted = await db.delete_role(role_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    logger.info("Self-service role deleted: %s by user %s", role_id, auth.actor_id)
    return {"status": "ok", "deleted": True}


@router.post("/roles/{role_id}/members")
async def add_member(request: Request, role_id: str):
    """Add a user to a self-service role (creator only)."""
    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    db = _get_pg(request)
    auth: AuthContext = await authenticate_self_service(request, db)
    await _require_owner(db, role_id, auth.actor_id)

    body = await request.json()
    try:
        data = AddMemberRequest.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    try:
        UUID(data.userId)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid userId format") from e

    result = await db.add_user_role(user_id=data.userId, role_id=role_id)
    return {
        "user_id": result["user_id"],
        "role_id": result["role_id"],
        "created_at": result["created_at"].isoformat(),
    }


@router.delete("/roles/{role_id}/members/{user_id}")
async def remove_member(request: Request, role_id: str, user_id: str):
    """Remove a user from a self-service role (creator only)."""
    try:
        UUID(role_id)
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID format") from e

    db = _get_pg(request)
    auth: AuthContext = await authenticate_self_service(request, db)
    await _require_owner(db, role_id, auth.actor_id)

    deleted = await db.remove_user_role(user_id=user_id, role_id=role_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binding not found")

    return {"status": "ok", "deleted": True}
