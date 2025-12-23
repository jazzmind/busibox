"""
Role-Resource Bindings API Routes

Provides endpoints for managing role-to-resource bindings.
This enables a generic authorization model where roles can be
bound to any type of resource (apps, libraries, etc).

All endpoints require admin authentication.
"""

from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request, Query, status
from pydantic import BaseModel, Field

from services.postgres import PostgresService
from config import Config

router = APIRouter()
config = Config()
pg: PostgresService = None  # Set via set_pg_service()


def set_pg_service(service: PostgresService):
    """Set the shared PostgresService instance."""
    global pg
    pg = service


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

async def _require_admin_auth(request: Request) -> str:
    """
    Require admin token authentication.
    Returns the actor ID (from X-Actor-Id header or 'system').
    
    Raises HTTPException if not authorized.
    """
    admin_token = config.admin_token
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header"
        )
    
    token = auth_header.removeprefix("Bearer ").strip()
    
    if token != admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token"
        )
    
    # Get actor ID from header for audit purposes
    actor_id = request.headers.get("X-Actor-Id", "system")
    return actor_id


def _format_binding(binding: dict) -> dict:
    """Format a binding record for API response."""
    return {
        "id": binding["id"],
        "role_id": binding["role_id"],
        "resource_type": binding["resource_type"],
        "resource_id": binding["resource_id"],
        "permissions": binding.get("permissions") or {},
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
    
    Requires admin authentication via Bearer token.
    """
    actor_id = await _require_admin_auth(request)
    
    await pg.connect()
    
    # Check if role exists
    role = await pg.get_role(body.role_id)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role not found: {body.role_id}"
        )
    
    # Check if binding already exists
    existing = await pg.get_role_binding_by_unique(
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
        binding = await pg.create_role_binding(
            role_id=body.role_id,
            resource_type=body.resource_type,
            resource_id=body.resource_id,
            permissions=body.permissions,
            created_by=actor_id if actor_id != "system" else None,
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
    
    Requires admin authentication via Bearer token.
    """
    await _require_admin_auth(request)
    
    await pg.connect()
    
    try:
        bindings = await pg.list_role_bindings(
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
    
    Requires admin authentication via Bearer token.
    """
    await _require_admin_auth(request)
    
    await pg.connect()
    
    try:
        binding = await pg.get_role_binding(binding_id)
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
    
    Requires admin authentication via Bearer token.
    """
    await _require_admin_auth(request)
    
    await pg.connect()
    
    try:
        deleted = await pg.delete_role_binding(binding_id)
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
    
    Requires admin authentication via Bearer token.
    """
    await _require_admin_auth(request)
    
    await pg.connect()
    
    # Check if role exists
    role = await pg.get_role(role_id)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role not found: {role_id}"
        )
    
    try:
        bindings = await pg.get_resources_for_role(role_id, resource_type)
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
    
    Requires admin authentication via Bearer token.
    Returns role information along with binding details.
    """
    await _require_admin_auth(request)
    
    await pg.connect()
    
    roles = await pg.get_roles_for_resource(resource_type, resource_id)
    
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
# User Access Check Endpoints
# -----------------------------------------------------------------------------

@router.get("/users/{user_id}/can-access/{resource_type}/{resource_id}")
async def check_user_access(request: Request, user_id: str, resource_type: str, resource_id: str):
    """
    Check if a user can access a specific resource via any of their roles.
    
    Requires admin authentication via Bearer token.
    Returns {"has_access": true/false}.
    """
    await _require_admin_auth(request)
    
    await pg.connect()
    
    try:
        has_access = await pg.user_can_access_resource(user_id, resource_type, resource_id)
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
    
    Requires admin authentication via Bearer token.
    Returns {"resource_ids": [...]}.
    """
    await _require_admin_auth(request)
    
    await pg.connect()
    
    try:
        resource_ids = await pg.get_user_accessible_resources(user_id, resource_type)
        return {"resource_ids": resource_ids}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

