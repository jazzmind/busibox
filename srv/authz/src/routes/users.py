"""
Admin User Management endpoints.

Protected by:
- OAuth client credentials (client_id/client_secret in body), OR
- Shared admin token (AUTHZ_ADMIN_TOKEN in Authorization: Bearer)

These endpoints allow ai-portal (or other admin tools) to manage users:
- Create, list, get, update, delete users
- Activate, deactivate, reactivate users
- Manage user roles
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, EmailStr

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


class UserCreate(BaseModel):
    email: EmailStr
    role_ids: List[str] = Field(default_factory=list)
    status: str = Field(default="PENDING", pattern="^(PENDING|ACTIVE|DEACTIVATED)$")


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    status: Optional[str] = Field(None, pattern="^(PENDING|ACTIVE|DEACTIVATED)$")
    email_verified_at: Optional[str] = None
    last_login_at: Optional[str] = None
    pending_expires_at: Optional[str] = None


class RoleResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class UserResponse(BaseModel):
    user_id: str
    email: str
    status: Optional[str] = None
    email_verified_at: Optional[str] = None
    last_login_at: Optional[str] = None
    pending_expires_at: Optional[str] = None
    created_at: str
    updated_at: str
    roles: List[RoleResponse] = Field(default_factory=list)


class PaginationResponse(BaseModel):
    page: int
    limit: int
    total_count: int
    total_pages: int


class UserListResponse(BaseModel):
    users: List[UserResponse]
    pagination: PaginationResponse


class UserRoleAssignment(BaseModel):
    role_id: str


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


def _format_user(user: dict) -> dict:
    """Format user dict for response."""
    roles = []
    for r in user.get("roles", []):
        roles.append({
            "id": str(r.get("id")) if r.get("id") else None,
            "name": r.get("name"),
            "description": r.get("description"),
            "scopes": r.get("scopes", []),
            "created_at": r.get("created_at").isoformat() if r.get("created_at") else "",
            "updated_at": r.get("updated_at").isoformat() if r.get("updated_at") else "",
        })
    
    # user_id might be a UUID object, convert to string
    user_id = user.get("user_id")
    user_id_str = str(user_id) if user_id else None
    
    return {
        "user_id": user_id_str,
        "email": user.get("email"),
        "status": user.get("status"),
        "email_verified_at": user.get("email_verified_at").isoformat() if user.get("email_verified_at") else None,
        "last_login_at": user.get("last_login_at").isoformat() if user.get("last_login_at") else None,
        "pending_expires_at": user.get("pending_expires_at").isoformat() if user.get("pending_expires_at") else None,
        "created_at": user.get("created_at").isoformat() if user.get("created_at") else "",
        "updated_at": user.get("updated_at").isoformat() if user.get("updated_at") else "",
        "roles": roles,
    }


# ============================================================================
# User CRUD Endpoints
# ============================================================================


@router.post("/admin/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(request: Request):
    """
    Create a new user.

    Body:
    - client_id, client_secret (OAuth client auth), OR
    - Authorization: Bearer <admin_token>
    - email: string (required)
    - role_ids: array of role UUIDs (optional)
    - status: PENDING | ACTIVE | DEACTIVATED (default: PENDING)
    """
    await _require_admin_auth(request)

    body = await request.json()
    try:
        user_data = UserCreate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    await pg.connect()

    # Validate email domain
    if not await pg.is_email_domain_allowed(user_data.email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Email domain not allowed: {user_data.email.split('@')[-1]}",
        )

    # Check if user with this email already exists
    existing = await pg.get_user_by_email(user_data.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists",
        )

    # Validate role IDs if provided
    if user_data.role_ids:
        for role_id in user_data.role_ids:
            try:
                UUID(role_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid role ID format: {role_id}",
                )
            role = await pg.get_role(role_id)
            if not role:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Role not found: {role_id}",
                )

    # Get admin user ID from body if available
    assigned_by = body.get("assigned_by")

    user = await pg.create_user(
        email=user_data.email,
        status=user_data.status,
        role_ids=user_data.role_ids,
        assigned_by=assigned_by,
    )

    return _format_user(user)


@router.get("/admin/users")
async def list_users(request: Request):
    """
    List all users with pagination.

    Query params:
    - page: int (default: 1)
    - limit: int (default: 20)
    - status: PENDING | ACTIVE | DEACTIVATED (optional)
    - search: string (email search, optional)

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    params = request.query_params
    page = int(params.get("page", "1"))
    limit = int(params.get("limit", "20"))
    user_status = params.get("status")
    search = params.get("search")

    # Validate pagination
    if page < 1:
        page = 1
    if limit < 1 or limit > 100:
        limit = 20

    await pg.connect()
    result = await pg.list_users(
        page=page,
        limit=limit,
        status=user_status,
        search=search,
    )

    return {
        "users": [_format_user(u) for u in result["users"]],
        "pagination": result["pagination"],
    }


@router.get("/admin/users/by-email/{email:path}")
async def get_user_by_email(request: Request, email: str):
    """
    Get a user by email address.

    Requires admin authentication.
    Returns 404 if user not found.
    """
    await _require_admin_auth(request)

    await pg.connect()
    user = await pg.get_user_by_email(email)

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return _format_user(user)


@router.get("/admin/users/{user_id}")
async def get_user(request: Request, user_id: str):
    """
    Get a specific user by ID.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    await pg.connect()
    user = await pg.get_user_with_roles(user_id)

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return _format_user(user)


@router.patch("/admin/users/{user_id}")
async def update_user(request: Request, user_id: str):
    """
    Update a user.

    Body:
    - client_id, client_secret (OAuth client auth), OR
    - Authorization: Bearer <admin_token>
    - email: string (optional)
    - status: PENDING | ACTIVE | DEACTIVATED (optional)
    - email_verified_at: ISO timestamp (optional)
    - last_login_at: ISO timestamp (optional)
    - pending_expires_at: ISO timestamp (optional)
    """
    await _require_admin_auth(request)

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    body = await request.json()
    try:
        user_data = UserUpdate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    await pg.connect()

    # Check if user exists
    existing = await pg.get_user(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # If changing email, validate domain
    if user_data.email and user_data.email != existing.get("email"):
        if not await pg.is_email_domain_allowed(user_data.email):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Email domain not allowed: {user_data.email.split('@')[-1]}",
            )

    user = await pg.update_user(
        user_id,
        email=user_data.email,
        status=user_data.status,
        email_verified_at=user_data.email_verified_at,
        last_login_at=user_data.last_login_at,
        pending_expires_at=user_data.pending_expires_at,
    )

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return _format_user(user)


@router.delete("/admin/users/{user_id}")
async def delete_user(request: Request, user_id: str):
    """
    Delete a user.

    This will cascade-delete all sessions, magic links, passkeys, etc.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    await pg.connect()
    deleted = await pg.delete_user(user_id)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return {"status": "ok", "deleted": True}


# ============================================================================
# User Status Transition Endpoints
# ============================================================================


@router.post("/admin/users/{user_id}/activate")
async def activate_user(request: Request, user_id: str):
    """
    Activate a pending user.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    await pg.connect()

    # Check current status
    existing = await pg.get_user(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if existing.get("status") == "ACTIVE":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is already active")

    user = await pg.activate_user(user_id)

    return _format_user(user)


@router.post("/admin/users/{user_id}/deactivate")
async def deactivate_user(request: Request, user_id: str):
    """
    Deactivate an active user.

    This will also invalidate all sessions.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    await pg.connect()

    # Check current status
    existing = await pg.get_user(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if existing.get("status") == "DEACTIVATED":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is already deactivated")

    user = await pg.deactivate_user(user_id)

    # Invalidate all sessions
    await pg.delete_user_sessions(user_id)

    return _format_user(user)


@router.post("/admin/users/{user_id}/reactivate")
async def reactivate_user(request: Request, user_id: str):
    """
    Reactivate a deactivated user.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    await pg.connect()

    # Check current status
    existing = await pg.get_user(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if existing.get("status") != "DEACTIVATED":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is not deactivated")

    user = await pg.reactivate_user(user_id)

    return _format_user(user)


# ============================================================================
# User Role Management Endpoints
# ============================================================================


@router.post("/admin/users/{user_id}/roles/{role_id}")
async def add_user_role(request: Request, user_id: str, role_id: str):
    """
    Add a role to a user.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    try:
        UUID(user_id)
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID format") from e

    await pg.connect()

    # Check user exists
    user = await pg.get_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Check role exists
    role = await pg.get_role(role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    result = await pg.add_user_role(user_id=user_id, role_id=role_id)

    return {
        "status": "ok",
        "user_id": result["user_id"],
        "role_id": result["role_id"],
        "created_at": result["created_at"].isoformat(),
    }


@router.delete("/admin/users/{user_id}/roles/{role_id}")
async def remove_user_role(request: Request, user_id: str, role_id: str):
    """
    Remove a role from a user.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    try:
        UUID(user_id)
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID format") from e

    await pg.connect()
    deleted = await pg.remove_user_role(user_id=user_id, role_id=role_id)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role assignment not found")

    return {"status": "ok", "deleted": True}


# ============================================================================
# Email Domain Configuration Endpoints
# ============================================================================


@router.get("/admin/email-domains")
async def list_email_domains(request: Request):
    """
    List all email domain rules.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    await pg.connect()
    domains = await pg.list_email_domain_rules()

    return {
        "domains": [
            {
                "id": d["id"],
                "domain": d["domain"],
                "is_allowed": d["is_allowed"],
                "created_at": d["created_at"].isoformat() if d.get("created_at") else "",
                "updated_at": d["updated_at"].isoformat() if d.get("updated_at") else "",
            }
            for d in domains
        ],
    }


@router.post("/admin/email-domains")
async def add_email_domain(request: Request):
    """
    Add or update an email domain rule.

    Body:
    - domain: string (required)
    - is_allowed: boolean (required)
    """
    await _require_admin_auth(request)

    body = await request.json()
    domain = body.get("domain")
    is_allowed = body.get("is_allowed")

    if not domain:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="domain is required")
    if is_allowed is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="is_allowed is required")

    await pg.connect()
    result = await pg.add_email_domain_rule(domain, is_allowed)

    return {
        "id": result["id"],
        "domain": result["domain"],
        "is_allowed": result["is_allowed"],
        "created_at": result["created_at"].isoformat() if result.get("created_at") else "",
        "updated_at": result["updated_at"].isoformat() if result.get("updated_at") else "",
    }


@router.delete("/admin/email-domains/{domain}")
async def remove_email_domain(request: Request, domain: str):
    """
    Remove an email domain rule.

    Requires admin authentication.
    """
    await _require_admin_auth(request)

    await pg.connect()
    deleted = await pg.remove_email_domain_rule(domain)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain rule not found")

    return {"status": "ok", "deleted": True}

