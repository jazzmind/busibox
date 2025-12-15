"""
Admin endpoints for RBAC management.

Protected by:
- OAuth client credentials (client_id/client_secret in body), OR
- Shared admin token (AUTHZ_ADMIN_TOKEN in Authorization: Bearer)

These endpoints allow ai-portal (or other admin tools) to manage:
- Roles (CRUD)
- User-role bindings
- External identity mappings (future)
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from config import Config
from oauth.client_auth import verify_client_secret

router = APIRouter()
config = Config()

# PostgresService instance - will be set by main.py
pg = None

def set_pg_service(pg_service):
    """Set the shared PostgresService instance."""
    global pg
    pg = pg_service


# ============================================================================
# Request/Response Models
# ============================================================================


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None


class RoleResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    created_at: str
    updated_at: str


class UserRoleBinding(BaseModel):
    user_id: str
    role_id: str


class UserRoleBindingResponse(BaseModel):
    user_id: str
    role_id: str
    created_at: str


# ============================================================================
# Authentication Helpers
# ============================================================================


async def _require_admin_auth(request: Request) -> None:
    """
    Require either OAuth client credentials or admin token.
    Raises HTTPException if unauthorized.
    """
    # Try admin token first (simplest for manual operations)
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        if config.admin_token and token == config.admin_token:
            return

    # Try OAuth client credentials in body
    try:
        body = await request.json()
        client_id = body.get("client_id")
        client_secret = body.get("client_secret")

        if client_id and client_secret:
            await pg.connect()
            client = await pg.get_oauth_client(client_id)
            if client and client.get("is_active"):
                if verify_client_secret(client_secret, client["client_secret_hash"]):
                    return
    except Exception:
        pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: valid admin token or OAuth client credentials required",
    )


# ============================================================================
# Role Management Endpoints
# ============================================================================


@router.post("/admin/roles", response_model=RoleResponse)
async def create_role(request: Request):
    """
    Create a new role.

    Body:
    - client_id, client_secret (OAuth client auth), OR
    - Authorization: Bearer <admin_token>
    - name: string (required)
    - description: string (optional)
    """
    await _require_admin_auth(request)

    body = await request.json()
    try:
        role_data = RoleCreate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    await pg.connect()
    role = await pg.create_role(name=role_data.name, description=role_data.description)

    return RoleResponse(
        id=role["id"],
        name=role["name"],
        description=role.get("description"),
        created_at=role["created_at"].isoformat(),
        updated_at=role["updated_at"].isoformat(),
    )


@router.get("/admin/roles", response_model=List[RoleResponse])
async def list_roles(request: Request):
    """
    List all roles.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    await pg.connect()
    roles = await pg.list_roles()

    return [
        RoleResponse(
            id=r["id"],
            name=r["name"],
            description=r.get("description"),
            created_at=r["created_at"].isoformat(),
            updated_at=r["updated_at"].isoformat(),
        )
        for r in roles
    ]


@router.get("/admin/roles/{role_id}", response_model=RoleResponse)
async def get_role(request: Request, role_id: str):
    """
    Get a specific role by ID.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    await pg.connect()
    role = await pg.get_role(role_id)

    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    return RoleResponse(
        id=role["id"],
        name=role["name"],
        description=role.get("description"),
        created_at=role["created_at"].isoformat(),
        updated_at=role["updated_at"].isoformat(),
    )


@router.put("/admin/roles/{role_id}", response_model=RoleResponse)
async def update_role(request: Request, role_id: str):
    """
    Update a role.

    Body:
    - client_id, client_secret (OAuth client auth), OR
    - Authorization: Bearer <admin_token>
    - name: string (optional)
    - description: string (optional)
    """
    await _require_admin_auth(request)

    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    body = await request.json()
    try:
        role_data = RoleUpdate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    if not role_data.name and not role_data.description:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one field must be provided")

    await pg.connect()
    role = await pg.update_role(role_id=role_id, name=role_data.name, description=role_data.description)

    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    return RoleResponse(
        id=role["id"],
        name=role["name"],
        description=role.get("description"),
        created_at=role["created_at"].isoformat(),
        updated_at=role["updated_at"].isoformat(),
    )


@router.delete("/admin/roles/{role_id}")
async def delete_role(request: Request, role_id: str):
    """
    Delete a role.

    This will cascade-delete all user-role bindings.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    try:
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role ID format") from e

    await pg.connect()
    deleted = await pg.delete_role(role_id)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    return {"status": "ok", "deleted": True}


# ============================================================================
# User-Role Binding Endpoints
# ============================================================================


@router.post("/admin/user-roles", response_model=UserRoleBindingResponse)
async def add_user_role(request: Request):
    """
    Add a user-role binding.

    Body:
    - client_id, client_secret (OAuth client auth), OR
    - Authorization: Bearer <admin_token>
    - user_id: string (UUID)
    - role_id: string (UUID)
    """
    await _require_admin_auth(request)

    body = await request.json()
    try:
        binding = UserRoleBinding.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    try:
        UUID(binding.user_id)
        UUID(binding.role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID format") from e

    await pg.connect()
    result = await pg.add_user_role(user_id=binding.user_id, role_id=binding.role_id)

    return UserRoleBindingResponse(
        user_id=result["user_id"],
        role_id=result["role_id"],
        created_at=result["created_at"].isoformat(),
    )


@router.delete("/admin/user-roles")
async def remove_user_role(request: Request):
    """
    Remove a user-role binding.

    Body:
    - client_id, client_secret (OAuth client auth), OR
    - Authorization: Bearer <admin_token>
    - user_id: string (UUID)
    - role_id: string (UUID)
    """
    await _require_admin_auth(request)

    body = await request.json()
    try:
        binding = UserRoleBinding.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    try:
        UUID(binding.user_id)
        UUID(binding.role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID format") from e

    await pg.connect()
    deleted = await pg.remove_user_role(user_id=binding.user_id, role_id=binding.role_id)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binding not found")

    return {"status": "ok", "deleted": True}


@router.get("/admin/users/{user_id}/roles", response_model=List[RoleResponse])
async def get_user_roles(request: Request, user_id: str):
    """
    Get all roles for a specific user.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    await pg.connect()
    roles = await pg.get_user_roles(user_id)

    return [
        RoleResponse(
            id=r["id"],
            name=r["name"],
            description=r.get("description"),
            created_at=r.get("created_at", ""),
            updated_at=r.get("updated_at", ""),
        )
        for r in roles
    ]


# ============================================================================
# OAuth Client Management
# ============================================================================


class OAuthClientCreate(BaseModel):
    client_id: str = Field(..., min_length=1, description="Client ID for the new OAuth client")
    client_secret: str = Field(..., min_length=1, description="Client secret for the new OAuth client")
    allowed_audiences: List[str] = Field(default_factory=list)
    allowed_scopes: List[str] = Field(default_factory=list)
    # Optional: for authentication when admin token is not available
    auth_client_id: Optional[str] = Field(None, description="Client ID for authentication (if not using admin token)")
    auth_client_secret: Optional[str] = Field(None, description="Client secret for authentication (if not using admin token)")


class OAuthClientResponse(BaseModel):
    client_id: str
    allowed_audiences: List[str]
    allowed_scopes: List[str]
    is_active: bool
    created_at: str


@router.post("/admin/oauth-clients", status_code=status.HTTP_201_CREATED)
async def create_oauth_client(
    client_data: OAuthClientCreate, request: Request
) -> OAuthClientResponse:
    """Create a new OAuth client."""
    # Check authentication: admin token in header OR client credentials in body
    auth_header = request.headers.get("authorization", "")
    authenticated = False
    
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        if config.admin_token and token == config.admin_token:
            authenticated = True
    
    # Check OAuth client credentials in body (auth_client_id/auth_client_secret)
    if not authenticated and client_data.auth_client_id and client_data.auth_client_secret:
        await pg.connect()
        client = await pg.get_oauth_client(client_data.auth_client_id)
        if client and client.get("is_active"):
            if verify_client_secret(client_data.auth_client_secret, client["client_secret_hash"]):
                authenticated = True
    
    if not authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: valid admin token or OAuth client credentials required",
        )

    from oauth.client_auth import hash_client_secret

    # Hash the client secret
    hashed_secret = hash_client_secret(client_data.client_secret)

    # Create the client
    await pg.create_oauth_client(
        client_id=client_data.client_id,
        client_secret_hash=hashed_secret,
        allowed_audiences=client_data.allowed_audiences,
        allowed_scopes=client_data.allowed_scopes,
    )

    # Fetch the created client
    client = await pg.get_oauth_client(client_data.client_id)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create OAuth client",
        )

    return OAuthClientResponse(
        client_id=client["client_id"],
        allowed_audiences=client["allowed_audiences"],
        allowed_scopes=client["allowed_scopes"],
        is_active=client["is_active"],
        created_at=str(client.get("created_at", "")),
    )


@router.get("/admin/oauth-clients")
async def list_oauth_clients(request: Request) -> List[OAuthClientResponse]:
    """List all OAuth clients."""
    await _require_admin_auth(request)

    clients = await pg.list_oauth_clients()
    return [
        OAuthClientResponse(
            client_id=c["client_id"],
            allowed_audiences=c["allowed_audiences"],
            allowed_scopes=c["allowed_scopes"],
            is_active=c["is_active"],
            created_at=str(c.get("created_at", "")),
        )
        for c in clients
    ]

