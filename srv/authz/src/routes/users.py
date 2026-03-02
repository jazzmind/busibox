"""
Admin User Management endpoints.

Protected by (in order of precedence):
- Access token (JWT) with authz.users.* scopes (audience: authz-api)

These endpoints allow busibox-portal (or other admin tools) to manage users:
- Create, list, get, update, delete users
- Activate, deactivate, reactivate users
- Manage user roles

Required scopes:
- authz.users.read: List, get users
- authz.users.write: Create, update, activate, deactivate users
- authz.users.delete: Delete users

Test Mode:
- Supports X-Test-Mode: true header to route to test database
- Enable with AUTHZ_TEST_MODE_ENABLED=true environment variable
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, EmailStr

from config import Config
from oauth.jwt_auth import require_auth, authenticate_self_service, AuthContext
from services.encryption import EnvelopeEncryptionService

router = APIRouter()
config = Config()

# PostgresService instances - will be set by main.py
# pg is production, pg_test is test database (optional)
pg = None
pg_test = None

# Header name for test mode
TEST_MODE_HEADER = "X-Test-Mode"


def set_pg_service(pg_service, pg_test_service=None):
    """Set the shared PostgresService instances."""
    global pg, pg_test
    pg = pg_service
    pg_test = pg_test_service


def _get_pg(request: Request):
    """Get the appropriate PostgresService based on request headers.
    
    If X-Test-Mode: true header is present and test mode is enabled,
    returns the test database service. Otherwise returns production.
    """
    if pg_test and config.test_mode_enabled:
        test_mode = request.headers.get(TEST_MODE_HEADER, "").lower() == "true"
        if test_mode:
            return pg_test
    return pg


# ============================================================================
# Request/Response Models
# ============================================================================


class UserCreate(BaseModel):
    email: EmailStr
    role_ids: List[str] = Field(default_factory=list)
    status: str = Field(default="PENDING", pattern="^(PENDING|ACTIVE|DEACTIVATED)$")
    display_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None
    favorite_color: Optional[str] = None


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    status: Optional[str] = Field(None, pattern="^(PENDING|ACTIVE|DEACTIVATED)$")
    display_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None
    favorite_color: Optional[str] = None
    email_verified_at: Optional[str] = None
    last_login_at: Optional[str] = None
    pending_expires_at: Optional[str] = None


class MeUpdate(BaseModel):
    display_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None
    favorite_color: Optional[str] = None
    github_pat: Optional[str] = None
    clear_github_pat: Optional[bool] = False


class ChannelBindingCreate(BaseModel):
    channel_type: str = Field(..., min_length=2, max_length=64)
    delegation_token: str = Field(..., min_length=20)
    delegation_token_jti: Optional[str] = None


class InternalChannelBindingInitiate(BaseModel):
    user_id: str
    channel_type: str = Field(..., min_length=2, max_length=64)
    delegation_token: str = Field(..., min_length=20)
    delegation_token_jti: Optional[str] = None


class InternalChannelBindingVerify(BaseModel):
    channel_type: str = Field(..., min_length=2, max_length=64)
    external_id: str = Field(..., min_length=1, max_length=256)
    link_code: str = Field(..., min_length=4, max_length=64)


class InternalChannelBindingRefresh(BaseModel):
    channel_type: str = Field(..., min_length=2, max_length=64)
    external_id: str = Field(..., min_length=1, max_length=256)


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
    display_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None
    favorite_color: Optional[str] = None
    has_github_pat: bool = False
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


async def _require_admin_auth(request: Request, scopes: Optional[List[str]] = None) -> AuthContext:
    """
    Require authentication for user management endpoints.
    
    Supports:
    - Access token (JWT) with audience=authz-api and required scopes
    
    Args:
        request: FastAPI request
        scopes: Required scopes (at least one must be present)
        
    Returns:
        AuthContext with actor info and available scopes
    """
    db = _get_pg(request)
    return await require_auth(request, db, scopes)


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
        "display_name": user.get("display_name"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "avatar_url": user.get("avatar_url"),
        "favorite_color": user.get("favorite_color"),
        "has_github_pat": bool(user.get("has_github_pat", False)),
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
    - email: string (required)
    - role_ids: array of role UUIDs (optional)
    - status: PENDING | ACTIVE | DEACTIVATED (default: PENDING)
    
    Headers:
    - X-Test-Mode: true (optional) - route to test database
    """
    await _require_admin_auth(request, scopes=["authz.users.write"])

    body = await request.json()
    try:
        user_data = UserCreate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # Get appropriate database (test or production)
    db = _get_pg(request)
    await db.connect()

    # Validate email domain
    if not await db.is_email_domain_allowed(user_data.email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Email domain not allowed: {user_data.email.split('@')[-1]}",
        )

    # Check if user with this email already exists
    existing = await db.get_user_by_email(user_data.email)
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
            role = await db.get_role(role_id)
            if not role:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Role not found: {role_id}",
                )

    # Get admin user ID from body if available
    assigned_by = body.get("assigned_by")

    user = await db.create_user(
        email=user_data.email,
        status=user_data.status,
        display_name=user_data.display_name,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        avatar_url=user_data.avatar_url,
        favorite_color=user_data.favorite_color,
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
    
    Headers:
    - X-Test-Mode: true (optional) - route to test database
    """
    await _require_admin_auth(request, scopes=["authz.users.read"])

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

    db = _get_pg(request)
    await db.connect()
    result = await db.list_users(
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
    await _require_admin_auth(request, scopes=["authz.users.read"])

    db = _get_pg(request)
    await db.connect()
    user = await db.get_user_by_email(email)

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return _format_user(user)


@router.get("/admin/users/{user_id}")
async def get_user(request: Request, user_id: str):
    """
    Get a specific user by ID.

    Requires admin authentication.
    """
    await _require_admin_auth(request, scopes=["authz.users.read"])

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    db = _get_pg(request)
    await db.connect()
    user = await db.get_user_with_roles(user_id)

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return _format_user(user)


@router.get("/internal/users/{user_id}/github-pat")
async def get_user_github_pat_internal(request: Request, user_id: str):
    """
    Internal endpoint: return decrypted GitHub PAT for a specific user.
    Requires authz.users.read scope.
    """
    await _require_admin_auth(request, scopes=["authz.users.read"])

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    db = _get_pg(request)
    await db.connect()
    encrypted = await db.get_user_github_pat_encrypted(user_id)
    if not encrypted:
        return {"github_pat": None}

    encryption = EnvelopeEncryptionService()
    try:
        github_pat = encryption.decrypt_kek(encrypted).decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to decrypt GitHub PAT: {e}") from e

    return {"github_pat": github_pat}


@router.patch("/admin/users/{user_id}")
async def update_user(request: Request, user_id: str):
    """
    Update a user.

    Body:
    - email: string (optional)
    - status: PENDING | ACTIVE | DEACTIVATED (optional)
    - email_verified_at: ISO timestamp (optional)
    - last_login_at: ISO timestamp (optional)
    - pending_expires_at: ISO timestamp (optional)
    """
    await _require_admin_auth(request, scopes=["authz.users.write"])

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    body = await request.json()
    try:
        user_data = UserUpdate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    db = _get_pg(request)
    await db.connect()

    # Check if user exists
    existing = await db.get_user(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # If changing email, validate domain
    if user_data.email and user_data.email != existing.get("email"):
        if not await db.is_email_domain_allowed(user_data.email):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Email domain not allowed: {user_data.email.split('@')[-1]}",
            )

    user = await db.update_user(
        user_id,
        email=user_data.email,
        status=user_data.status,
        display_name=user_data.display_name,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        avatar_url=user_data.avatar_url,
        favorite_color=user_data.favorite_color,
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
    await _require_admin_auth(request, scopes=["authz.users.delete"])

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    db = _get_pg(request)
    await db.connect()
    deleted = await db.delete_user(user_id)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return {"status": "ok", "deleted": True}


@router.get("/me", response_model=UserResponse)
async def get_me(request: Request):
    """Get the authenticated user's profile (self-service)."""
    db = _get_pg(request)
    await db.connect()
    auth = await authenticate_self_service(request, db)
    user = await db.get_user_with_roles(auth.actor_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _format_user(user)


@router.patch("/me", response_model=UserResponse)
async def patch_me(request: Request):
    """Update the authenticated user's profile fields (self-service)."""
    body = await request.json()
    try:
        me_data = MeUpdate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    db = _get_pg(request)
    await db.connect()
    auth = await authenticate_self_service(request, db)

    user = await db.update_user(
        auth.actor_id,
        display_name=me_data.display_name,
        first_name=me_data.first_name,
        last_name=me_data.last_name,
        avatar_url=me_data.avatar_url,
        favorite_color=me_data.favorite_color,
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Update encrypted GitHub PAT if provided.
    if me_data.clear_github_pat:
        await db.set_user_github_pat(auth.actor_id, None)
    elif me_data.github_pat:
        encryption = EnvelopeEncryptionService()
        encrypted = encryption.encrypt_kek(me_data.github_pat.encode("utf-8"))
        await db.set_user_github_pat(auth.actor_id, encrypted)

    user = await db.get_user_with_roles(auth.actor_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _format_user(user)


@router.get("/me/github-pat")
async def get_me_github_pat(request: Request):
    """
    Return the authenticated user's decrypted GitHub PAT.
    Intended for trusted internal app workflows.
    """
    db = _get_pg(request)
    await db.connect()
    auth = await authenticate_self_service(request, db)
    encrypted = await db.get_user_github_pat_encrypted(auth.actor_id)
    if not encrypted:
        return {"github_pat": None}

    encryption = EnvelopeEncryptionService()
    try:
        github_pat = encryption.decrypt_kek(encrypted).decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to decrypt GitHub PAT: {e}") from e

    return {"github_pat": github_pat}


@router.post("/me/refresh-session")
async def refresh_session_jwt(request: Request):
    """
    Re-sign the caller's session JWT with fresh profile data from the database.

    This is called after a profile update so that downstream apps (navbar, etc.)
    see the updated display name, avatar, etc. without requiring a full re-login.

    The caller must present a valid session JWT. The endpoint:
    1. Verifies the session JWT and extracts the session ID (jti)
    2. Reads the user's current profile + roles from the database
    3. Re-signs a new session JWT with the same session ID (jti)
    4. Returns the new JWT

    Returns:
        { "session_jwt": "<new_jwt>", "expires_at": <unix_timestamp> }
    """
    import jwt as pyjwt
    from routes.auth import _sign_session_jwt

    db = _get_pg(request)
    await db.connect()
    auth = await authenticate_self_service(request, db)

    # Extract session ID (jti) from the current session JWT
    auth_header = request.headers.get("authorization", "")
    token = auth_header.replace("Bearer ", "").strip() if auth_header.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session JWT")

    try:
        claims = pyjwt.decode(token, options={"verify_signature": False})
        session_id = claims.get("jti")
        if not session_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session JWT missing jti claim")
    except pyjwt.DecodeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JWT") from e

    # Get fresh user data from DB
    user = await db.get_user_with_roles(auth.actor_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Re-sign the session JWT with fresh profile data, preserving the session ID
    roles = user.get("roles", [])
    new_jwt, exp = await _sign_session_jwt(
        user_id=str(user["id"]),
        email=user["email"],
        session_id=session_id,
        display_name=user.get("display_name"),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        avatar_url=user.get("avatar_url"),
        favorite_color=user.get("favorite_color"),
        roles=roles,
        db=db,
    )

    return {"session_jwt": new_jwt, "expires_at": exp}


@router.get("/me/channel-bindings")
async def list_my_channel_bindings(request: Request):
    """List authenticated user's linked/pending channel bindings."""
    db = _get_pg(request)
    await db.connect()
    auth = await authenticate_self_service(request, db)
    bindings = await db.list_user_channel_bindings(auth.actor_id)
    return {"bindings": bindings}


@router.post("/me/channel-bindings")
async def create_my_channel_binding(request: Request):
    """Create or refresh a pending self-service channel binding."""
    body = await request.json()
    try:
        data = ChannelBindingCreate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    db = _get_pg(request)
    await db.connect()
    auth = await authenticate_self_service(request, db)

    binding = await db.create_user_channel_binding(
        user_id=auth.actor_id,
        channel_type=data.channel_type,
        delegation_token=data.delegation_token,
        delegation_token_jti=data.delegation_token_jti,
    )
    return {"binding": binding}


@router.delete("/me/channel-bindings/{binding_id}")
async def delete_my_channel_binding(request: Request, binding_id: str):
    """Delete one of the authenticated user's channel bindings."""
    db = _get_pg(request)
    await db.connect()
    auth = await authenticate_self_service(request, db)
    deleted = await db.delete_user_channel_binding(auth.actor_id, binding_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binding not found")
    return {"status": "ok", "deleted": True}


@router.post("/internal/channel-bindings/initiate")
async def internal_initiate_channel_binding(request: Request):
    """
    Internal endpoint used by bridge-api to create pending link records.
    Intentionally restricted to trusted internal network.
    """
    body = await request.json()
    try:
        data = InternalChannelBindingInitiate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    db = _get_pg(request)
    await db.connect()
    binding = await db.create_user_channel_binding(
        user_id=data.user_id,
        channel_type=data.channel_type,
        delegation_token=data.delegation_token,
        delegation_token_jti=data.delegation_token_jti,
    )
    return {"binding": binding}


@router.put("/internal/channel-bindings/verify")
async def internal_verify_channel_binding(request: Request):
    """
    Internal endpoint used by bridge-api when it receives /link <code>.
    Intentionally restricted to trusted internal network.
    """
    body = await request.json()
    try:
        data = InternalChannelBindingVerify.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    db = _get_pg(request)
    await db.connect()
    binding = await db.verify_user_channel_binding(
        channel_type=data.channel_type,
        external_id=data.external_id,
        link_code=data.link_code,
    )
    if not binding:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired link code")
    return {"binding": binding}


@router.get("/internal/channel-bindings/lookup")
async def internal_lookup_channel_binding(request: Request):
    """
    Internal endpoint used by bridge-api to map external user identity to busibox user.
    Intentionally restricted to trusted internal network.
    """
    channel_type = (request.query_params.get("channel_type") or "").strip()
    external_id = (request.query_params.get("external_id") or "").strip()
    if not channel_type or not external_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="channel_type and external_id are required")

    db = _get_pg(request)
    await db.connect()
    binding = await db.lookup_user_channel_binding(
        channel_type=channel_type,
        external_id=external_id,
    )
    if not binding:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binding not found")
    return {"binding": binding}


@router.post("/internal/channel-bindings/refresh-token")
async def internal_refresh_channel_binding_token(request: Request):
    """
    Re-sign a stale delegation token with the current signing key.

    Called by bridge when token exchange fails with ``invalid_subject_token_key``
    after an authz key rotation.  The underlying delegation record in
    ``authz_delegation_tokens`` must still be valid (not revoked, not expired).
    """
    body = await request.json()
    try:
        data = InternalChannelBindingRefresh.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    db = _get_pg(request)
    await db.connect()

    binding = await db.lookup_user_channel_binding(
        channel_type=data.channel_type,
        external_id=data.external_id,
    )
    if not binding:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binding not found")

    old_jwt = binding.get("delegation_token") or ""
    jti = binding.get("delegation_token_jti") or ""
    if not old_jwt or not jti:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Binding has no delegation token to refresh",
        )

    delegation = await db.get_delegation_token(jti)
    if not delegation:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Delegation token revoked or expired — user must re-link",
        )

    import jwt as pyjwt
    try:
        claims = pyjwt.decode(old_jwt, options={"verify_signature": False})
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot decode stored delegation JWT",
        )

    from routes.oauth import _sign_delegation_token

    new_jwt_str = await _sign_delegation_token(
        user_id=claims["sub"],
        email=claims.get("email", ""),
        jti=jti,
        scopes=(claims.get("scope") or "").split(),
        expires_at=claims["exp"],
    )

    updated = await db.update_channel_binding_token(
        channel_type=data.channel_type,
        external_id=data.external_id,
        delegation_token=new_jwt_str,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update binding")

    return {"binding": updated}


# ============================================================================
# User Status Transition Endpoints
# ============================================================================


@router.post("/admin/users/{user_id}/activate")
async def activate_user(request: Request, user_id: str):
    """
    Activate a pending user.

    Requires admin authentication.
    """
    await _require_admin_auth(request, scopes=["authz.users.write"])

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    db = _get_pg(request)
    await db.connect()

    # Check current status
    existing = await db.get_user(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if existing.get("status") == "ACTIVE":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is already active")

    user = await db.activate_user(user_id)

    return _format_user(user)


@router.post("/admin/users/{user_id}/deactivate")
async def deactivate_user(request: Request, user_id: str):
    """
    Deactivate an active user.

    This will also invalidate all sessions.

    Requires admin authentication.
    """
    await _require_admin_auth(request, scopes=["authz.users.write"])

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    db = _get_pg(request)
    await db.connect()

    # Check current status
    existing = await db.get_user(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if existing.get("status") == "DEACTIVATED":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is already deactivated")

    user = await db.deactivate_user(user_id)

    # Invalidate all sessions
    await db.delete_user_sessions(user_id)

    return _format_user(user)


@router.post("/admin/users/{user_id}/reactivate")
async def reactivate_user(request: Request, user_id: str):
    """
    Reactivate a deactivated user.

    Requires admin authentication.
    """
    await _require_admin_auth(request, scopes=["authz.users.write"])

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format") from e

    db = _get_pg(request)
    await db.connect()

    # Check current status
    existing = await db.get_user(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if existing.get("status") != "DEACTIVATED":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is not deactivated")

    user = await db.reactivate_user(user_id)

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
    await _require_admin_auth(request, scopes=["authz.users.write"])

    try:
        UUID(user_id)
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID format") from e

    db = _get_pg(request)
    await db.connect()

    # Check user exists
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Check role exists
    role = await db.get_role(role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    result = await db.add_user_role(user_id=user_id, role_id=role_id)

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
    await _require_admin_auth(request, scopes=["authz.users.write"])

    try:
        UUID(user_id)
        UUID(role_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID format") from e

    db = _get_pg(request)
    await db.connect()
    deleted = await db.remove_user_role(user_id=user_id, role_id=role_id)

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
    await _require_admin_auth(request, scopes=["authz.users.read"])

    db = _get_pg(request)
    await db.connect()
    domains = await db.list_email_domain_rules()

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
    await _require_admin_auth(request, scopes=["authz.users.write"])

    body = await request.json()
    domain = body.get("domain")
    is_allowed = body.get("is_allowed")

    if not domain:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="domain is required")
    if is_allowed is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="is_allowed is required")

    db = _get_pg(request)
    await db.connect()
    result = await db.add_email_domain_rule(domain, is_allowed)

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
    await _require_admin_auth(request, scopes=["authz.users.write"])

    db = _get_pg(request)
    await db.connect()
    deleted = await db.remove_email_domain_rule(domain)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain rule not found")

    return {"status": "ok", "deleted": True}

