"""
Role-Resource Bindings API Routes

Provides endpoints for managing role-to-resource bindings.
This enables a generic authorization model where roles can be
bound to any type of resource (apps, libraries, etc).

Authentication:
- Admin endpoints require access token with authz.bindings.* scopes
- Self-service endpoints allow session JWT for users accessing their own resources
"""

from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request, Query, status
from pydantic import BaseModel, Field

from services.postgres import PostgresService
from config import Config
from oauth.jwt_auth import require_auth, require_auth_or_self_service, AuthContext

router = APIRouter()
config = Config()

# PostgresService instances - will be set by main.py
# pg is production, pg_test is test database (optional)
pg: PostgresService = None
pg_test: PostgresService = None

# Header name for test mode
TEST_MODE_HEADER = "X-Test-Mode"


def set_pg_service(service: PostgresService, test_service: PostgresService = None):
    """Set the shared PostgresService instances."""
    global pg, pg_test
    pg = service
    pg_test = test_service


def _get_pg(request: Request) -> PostgresService:
    """Get the appropriate PostgresService based on request headers.
    
    If X-Test-Mode: true header is present and test mode is enabled,
    returns the test database service. Otherwise returns production.
    """
    if pg_test and config.test_mode_enabled:
        test_mode = request.headers.get(TEST_MODE_HEADER, "").lower() == "true"
        if test_mode:
            return pg_test
    return pg


# -----------------------------------------------------------------------------
# Pydantic Models
# -----------------------------------------------------------------------------

class RoleBindingCreate(BaseModel):
    """Request model for creating a role binding."""
    role_id: str = Field(..., description="UUID of the role")
    resource_type: str = Field(..., description="Type of resource (app, library, document)")
    resource_id: str = Field(..., description="ID of the resource")
    permissions: Optional[dict] = Field(default=None, description="Optional fine-grained permissions")


class RoleBindingResponse(BaseModel):
    """Response model for a role binding."""
    id: str
    role_id: str
    resource_type: str
    resource_id: str
    permissions: Optional[dict] = None
    created_at: str
    created_by: Optional[str] = None


class RoleWithBinding(BaseModel):
    """Role information with binding details."""
    id: str
    name: str
    description: Optional[str] = None
    scopes: Optional[List[str]] = None
    binding_id: str
    permissions: Optional[dict] = None
    binding_created_at: str


# -----------------------------------------------------------------------------
# Auth Helper
# -----------------------------------------------------------------------------

async def _require_bindings_admin(request: Request, scopes: List[str] = None) -> AuthContext:
    """
    Require authentication for bindings admin operations.
    
    Uses JWT-based authentication with scope checks.
    Falls back to service account credentials.
    
    Returns AuthContext with actor information.
    """
    db = _get_pg(request)
    await db.connect()
    
    default_scopes = scopes or ["authz.bindings.read", "authz.bindings.write"]
    return await require_auth(request, db, default_scopes)


def _format_binding(binding: dict) -> dict:
    """Format a binding record for API response."""
    # Handle permissions - may be stored as JSON string in text column
    permissions = binding.get("permissions") or {}
    if isinstance(permissions, str):
        import json
        try:
            permissions = json.loads(permissions)
        except (json.JSONDecodeError, TypeError):
            permissions = {}
    
    return {
        "id": binding["id"],
        "role_id": binding["role_id"],
        "resource_type": binding["resource_type"],
        "resource_id": binding["resource_id"],
        "permissions": permissions,
        "created_at": binding["created_at"].isoformat() if binding.get("created_at") else None,
        "created_by": binding.get("created_by"),
    }


# -----------------------------------------------------------------------------
# Admin Endpoints
# -----------------------------------------------------------------------------

@router.post("/admin/bindings", status_code=status.HTTP_201_CREATED)
async def create_binding(request: Request, body: RoleBindingCreate):
    """
    Create a new role-resource binding.
    
    Requires access token with authz.bindings.write scope.
    """
    auth = await _require_bindings_admin(request, ["authz.bindings.write"])
    
    db = _get_pg(request)
    await db.connect()
    
    # Check if role exists
    role = await db.get_role(body.role_id)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role not found: {body.role_id}"
        )
    
    # Check if binding already exists
    existing = await db.get_role_binding_by_unique(
        role_id=body.role_id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Binding already exists for this role and resource"
        )
    
    # Create the binding
    try:
        binding = await db.create_role_binding(
            role_id=body.role_id,
            resource_type=body.resource_type,
            resource_id=body.resource_id,
            permissions=body.permissions,
            created_by=auth.actor_id if auth.auth_type != "service_account" else None,
        )
        return _format_binding(binding)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/admin/bindings")
async def list_bindings(
    request: Request,
    role_id: Optional[str] = Query(None, description="Filter by role ID"),
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    resource_id: Optional[str] = Query(None, description="Filter by resource ID"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    List role-resource bindings with optional filters.
    
    Requires access token with authz.bindings.read scope.
    """
    await _require_bindings_admin(request, ["authz.bindings.read"])
    
    db = _get_pg(request)
    await db.connect()
    
    try:
        bindings = await db.list_role_bindings(
            role_id=role_id,
            resource_type=resource_type,
            resource_id=resource_id,
            limit=limit,
            offset=offset,
        )
        return [_format_binding(b) for b in bindings]
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/admin/bindings/{binding_id}")
async def get_binding(request: Request, binding_id: str):
    """
    Get a specific role-resource binding by ID.
    
    Requires access token with authz.bindings.read scope.
    """
    await _require_bindings_admin(request, ["authz.bindings.read"])
    
    db = _get_pg(request)
    await db.connect()
    
    try:
        binding = await db.get_role_binding(binding_id)
        if not binding:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Binding not found"
            )
        return _format_binding(binding)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete("/admin/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_binding(request: Request, binding_id: str):
    """
    Delete a role-resource binding by ID.
    
    Requires access token with authz.bindings.write scope.
    """
    await _require_bindings_admin(request, ["authz.bindings.write"])
    
    db = _get_pg(request)
    await db.connect()
    
    try:
        deleted = await db.delete_role_binding(binding_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Binding not found"
            )
        return None
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


# -----------------------------------------------------------------------------
# Role-Centric Endpoints
# -----------------------------------------------------------------------------

@router.get("/roles/{role_id}/bindings")
async def get_role_bindings(
    request: Request,
    role_id: str,
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
):
    """
    Get all resource bindings for a specific role.
    
    Requires access token with authz.bindings.read scope.
    """
    await _require_bindings_admin(request, ["authz.bindings.read"])
    
    db = _get_pg(request)
    await db.connect()
    
    # Check if role exists
    role = await db.get_role(role_id)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role not found: {role_id}"
        )
    
    try:
        bindings = await db.get_resources_for_role(role_id, resource_type)
        return [_format_binding(b) for b in bindings]
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


# -----------------------------------------------------------------------------
# Resource-Centric Endpoints
# -----------------------------------------------------------------------------

@router.get("/resources/{resource_type}/{resource_id}/roles")
async def get_resource_roles(request: Request, resource_type: str, resource_id: str):
    """
    Get all roles that have access to a specific resource.
    
    Requires access token with authz.bindings.read scope.
    Returns role information along with binding details.
    """
    await _require_bindings_admin(request, ["authz.bindings.read"])
    
    db = _get_pg(request)
    await db.connect()
    
    roles = await db.get_roles_for_resource(resource_type, resource_id)
    
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r.get("description"),
            "scopes": r.get("scopes") or [],
            "binding_id": r["binding_id"],
            "permissions": r.get("permissions") or {},
            "binding_created_at": r["binding_created_at"].isoformat() if r.get("binding_created_at") else None,
        }
        for r in roles
    ]


# -----------------------------------------------------------------------------
# User Access Check Endpoints (Self-Service Enabled)
# -----------------------------------------------------------------------------

@router.get("/users/{user_id}/can-access/{resource_type}/{resource_id}")
async def check_user_access(request: Request, user_id: str, resource_type: str, resource_id: str):
    """
    Check if a user can access a specific resource via any of their roles.
    
    Supports self-service: users can check their own access with session JWT.
    Admins can check any user's access with access token + authz.bindings.read scope.
    
    Returns {"has_access": true/false}.
    """
    db = _get_pg(request)
    await db.connect()
    
    # Allow self-service (user checking their own access) or admin with scope
    await require_auth_or_self_service(
        request, db,
        self_service_user_id=user_id,
        admin_scopes=["authz.bindings.read"],
    )
    
    try:
        has_access = await db.user_can_access_resource(user_id, resource_type, resource_id)
        return {"has_access": has_access}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/users/{user_id}/resources/{resource_type}")
async def get_user_resources(request: Request, user_id: str, resource_type: str):
    """
    Get all resource IDs of a given type that a user can access.
    
    Supports self-service: users can list their own accessible resources with session JWT.
    Admins can list any user's resources with access token + authz.bindings.read scope.
    
    Returns {"resource_ids": [...]}.
    """
    db = _get_pg(request)
    await db.connect()
    
    # Allow self-service (user listing their own resources) or admin with scope
    await require_auth_or_self_service(
        request, db,
        self_service_user_id=user_id,
        admin_scopes=["authz.bindings.read"],
    )
    
    try:
        resource_ids = await db.get_user_accessible_resources(user_id, resource_type)
        return {"resource_ids": resource_ids}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

