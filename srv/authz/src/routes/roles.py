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

import jwt as pyjwt
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from config import Config
from oauth.jwt_auth import require_auth, AuthContext

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


async def _authenticate_any_session(request: Request, db) -> AuthContext:
    """
    Authenticate using any authz-signed session JWT regardless of audience.

    Apps hold SSO tokens scoped to their own audience (e.g. ``busibox-recruiter``).
    The standard ``authenticate_self_service`` only accepts ``audience=busibox-portal``.
    For self-service role management we accept any audience because authz signed
    the token itself, so the user identity is proven.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session token")

    token = auth_header[7:]

    from routes.oauth import _get_signing_key_objects
    try:
        kid, alg, _, public_key = await _get_signing_key_objects(db)
    except RuntimeError:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="no_signing_key_configured")

    try:
        token_kid = pyjwt.get_unverified_header(token).get("kid")
        if token_kid != kid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token_key")

        # Accept any audience -- we only need signature + issuer + expiry
        claims = pyjwt.decode(
            token,
            public_key,
            algorithms=[alg],
            issuer=config.issuer,
            options={"verify_aud": False, "require": ["exp", "iat", "sub"]},
        )

        token_type = claims.get("typ")
        if token_type != "session":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token_type")

        # Check revocation if jti present
        jti = claims.get("jti")
        if jti:
            session = await db.get_session_by_id(jti)
            if session is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session_revoked")

        user_id = claims["sub"]
        email = claims.get("email", "")
        roles = claims.get("roles", [])

        return AuthContext(
            auth_type="session",
            actor_id=user_id,
            scopes=set(),
            email=email,
            roles=roles,
        )
    except HTTPException:
        raise
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_expired")
    except pyjwt.InvalidIssuerError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_issuer")
    except pyjwt.DecodeError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid_token: {e}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid session token: {e}")


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


class SelfServiceBindingCreate(BaseModel):
    resource_type: str = Field(..., description="Type of resource (app, document, library)")
    resource_id: str = Field(..., description="ID of the resource")


SELF_SERVICE_BINDING_RESOURCE_TYPES = {"app", "document", "library"}


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
    auth: AuthContext = await _authenticate_any_session(request, db)

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

    auth: AuthContext = await _authenticate_any_session(request, db)
    roles = await db.get_user_accessible_roles(auth.actor_id, source_app=app)
    return [_role_response(r) for r in roles]


@router.get("/roles/users/search")
async def search_users(request: Request, q: str = ""):
    """
    Search for active users to add to a self-service role.

    Requires a session JWT (any audience). Returns minimal user profile info.

    Query params:
        q: search string (min 2 chars, matches email, display name, first/last name)
    """
    if not q or len(q) < 2:
        return {"users": []}

    db = _get_pg(request)
    await _authenticate_any_session(request, db)

    members = await db.search_active_users(search=q, limit=20)
    return {
        "users": [
            {
                "user_id": m["user_id"],
                "email": m["email"],
                "display_name": m.get("display_name"),
                "first_name": m.get("first_name"),
                "last_name": m.get("last_name"),
                "avatar_url": m.get("avatar_url"),
            }
            for m in members
        ],
    }


@router.get("/roles/{role_id}")
async def get_role(request: Request, role_id: str):
    """Get a role by ID (must be creator or assigned)."""
    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    db = _get_pg(request)
    auth: AuthContext = await _authenticate_any_session(request, db)

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
    auth: AuthContext = await _authenticate_any_session(request, db)
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
    auth: AuthContext = await _authenticate_any_session(request, db)
    await _require_owner(db, role_id, auth.actor_id)

    deleted = await db.delete_role(role_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    logger.info("Self-service role deleted: %s by user %s", role_id, auth.actor_id)
    return {"status": "ok", "deleted": True}


@router.get("/roles/{role_id}/members")
async def list_members(request: Request, role_id: str):
    """List members of a self-service role (creator or assigned user)."""
    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    db = _get_pg(request)
    auth: AuthContext = await _authenticate_any_session(request, db)

    role = await db.get_role(role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    accessible = await db.get_user_accessible_roles(auth.actor_id)
    if not any(r["id"] == role_id for r in accessible):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    members = await db.get_role_members(role_id)
    return {
        "role_id": role_id,
        "members": [
            {
                "user_id": m["user_id"],
                "email": m["email"],
                "display_name": m.get("display_name"),
                "first_name": m.get("first_name"),
                "last_name": m.get("last_name"),
                "avatar_url": m.get("avatar_url"),
                "status": m.get("status"),
                "assigned_at": m["assigned_at"].isoformat() if m.get("assigned_at") else "",
            }
            for m in members
        ],
    }


@router.post("/roles/{role_id}/members")
async def add_member(request: Request, role_id: str):
    """Add a user to a self-service role (creator only)."""
    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    db = _get_pg(request)
    auth: AuthContext = await _authenticate_any_session(request, db)
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
    auth: AuthContext = await _authenticate_any_session(request, db)
    await _require_owner(db, role_id, auth.actor_id)

    deleted = await db.remove_user_role(user_id=user_id, role_id=role_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binding not found")

    return {"status": "ok", "deleted": True}


# ============================================================================
# Self-Service Role-Resource Bindings
# ============================================================================

@router.post("/roles/{role_id}/bindings", status_code=status.HTTP_201_CREATED)
async def create_self_service_binding(request: Request, role_id: str):
    """
    Bind a self-service role to a resource (idempotent).

    Only the role creator can create bindings. Allowed resource types:
    ``app``, ``document``, ``library``.

    Returns 201 on creation, 200 if the binding already exists.
    """
    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    db = _get_pg(request)
    auth: AuthContext = await _authenticate_any_session(request, db)
    await _require_owner(db, role_id, auth.actor_id)

    body = await request.json()
    try:
        data = SelfServiceBindingCreate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    if data.resource_type not in SELF_SERVICE_BINDING_RESOURCE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"resource_type must be one of: {sorted(SELF_SERVICE_BINDING_RESOURCE_TYPES)}",
        )

    existing = await db.get_role_binding_by_unique(
        role_id=role_id,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
    )
    if existing:
        return {
            "id": existing["id"],
            "role_id": existing["role_id"],
            "resource_type": existing["resource_type"],
            "resource_id": existing["resource_id"],
            "already_existed": True,
        }

    binding = await db.create_role_binding(
        role_id=role_id,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        created_by=auth.actor_id,
    )

    logger.info(
        "Self-service binding created: role %s -> %s/%s by user %s",
        role_id, data.resource_type, data.resource_id, auth.actor_id,
    )
    return {
        "id": binding["id"],
        "role_id": binding["role_id"],
        "resource_type": binding["resource_type"],
        "resource_id": binding["resource_id"],
        "already_existed": False,
    }


@router.get("/roles/{role_id}/bindings")
async def list_self_service_bindings(request: Request, role_id: str):
    """
    List resource bindings for a self-service role.

    The caller must be the role creator or an assigned member.
    """
    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    db = _get_pg(request)
    auth: AuthContext = await _authenticate_any_session(request, db)

    role = await db.get_role(role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    accessible = await db.get_user_accessible_roles(auth.actor_id)
    if not any(r["id"] == role_id for r in accessible):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    bindings = await db.get_resources_for_role(role_id)
    return {
        "role_id": role_id,
        "bindings": [
            {
                "id": b["id"],
                "resource_type": b["resource_type"],
                "resource_id": b["resource_id"],
            }
            for b in bindings
        ],
    }
