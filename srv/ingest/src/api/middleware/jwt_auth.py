"""
JWT Authentication Middleware for Role-Based Access Control

Validates JWT tokens from AI Portal and extracts:
- User identity (sub, email)
- Document role memberships with CRUD permissions

Sets PostgreSQL session variables for Row-Level Security (RLS):
- app.user_id: User UUID
- app.user_role_ids_read: CSV of role UUIDs user can read
- app.user_role_ids_create: CSV of role UUIDs user can create with
- app.user_role_ids_update: CSV of role UUIDs user can update
- app.user_role_ids_delete: CSV of role UUIDs user can delete
"""

import os
import uuid
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import jwt
import structlog
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger()

# ============================================================================
# Configuration
# ============================================================================

JWT_SECRET = os.environ.get("JWT_SECRET") or \
             os.environ.get("SERVICE_JWT_SECRET") or \
             os.environ.get("SSO_JWT_SECRET") or \
             "default-service-secret-change-in-production"

JWT_ISSUER = os.environ.get("JWT_ISSUER", "ai-portal")
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "ingest-api")

# Allow legacy X-User-Id header during migration
ALLOW_LEGACY_HEADER = os.environ.get("ALLOW_LEGACY_AUTH", "true").lower() == "true"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class DocumentRole:
    """Document role with CRUD permissions."""
    id: str
    name: str
    permissions: List[str] = field(default_factory=list)
    
    @property
    def can_create(self) -> bool:
        return "create" in self.permissions
    
    @property
    def can_read(self) -> bool:
        return "read" in self.permissions
    
    @property
    def can_update(self) -> bool:
        return "update" in self.permissions
    
    @property
    def can_delete(self) -> bool:
        return "delete" in self.permissions


@dataclass
class UserContext:
    """User context extracted from JWT."""
    user_id: str
    email: Optional[str] = None
    roles: List[DocumentRole] = field(default_factory=list)
    is_legacy: bool = False  # True if authenticated via X-User-Id header
    
    def get_role_ids_by_permission(self, permission: str) -> List[str]:
        """Get role IDs where user has specific permission."""
        return [r.id for r in self.roles if permission in r.permissions]
    
    @property
    def read_role_ids(self) -> List[str]:
        return self.get_role_ids_by_permission("read")
    
    @property
    def create_role_ids(self) -> List[str]:
        return self.get_role_ids_by_permission("create")
    
    @property
    def update_role_ids(self) -> List[str]:
        return self.get_role_ids_by_permission("update")
    
    @property
    def delete_role_ids(self) -> List[str]:
        return self.get_role_ids_by_permission("delete")


# ============================================================================
# JWT Parsing
# ============================================================================

def parse_jwt_token(token: str) -> Optional[dict]:
    """
    Verify and decode a JWT token.
    
    Returns decoded payload or None if invalid.
    """
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token expired")
        return None
    except jwt.InvalidAudienceError:
        logger.warning("JWT invalid audience", expected=JWT_AUDIENCE)
        return None
    except jwt.InvalidIssuerError:
        logger.warning("JWT invalid issuer", expected=JWT_ISSUER)
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("JWT invalid token", error=str(e))
        return None


def extract_user_context(payload: dict) -> UserContext:
    """Extract UserContext from JWT payload."""
    user_id = payload.get("sub", "")
    email = payload.get("email")
    
    roles = []
    for role_data in payload.get("roles", []):
        role = DocumentRole(
            id=role_data.get("id", ""),
            name=role_data.get("name", ""),
            permissions=role_data.get("permissions", [])
        )
        roles.append(role)
    
    return UserContext(
        user_id=user_id,
        email=email,
        roles=roles,
        is_legacy=False
    )


# ============================================================================
# Middleware
# ============================================================================

class JWTAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to validate JWT tokens and extract user context.
    
    Supports both:
    1. JWT via Authorization: Bearer <token> (preferred)
    2. Legacy X-User-Id header (for backward compatibility)
    """
    
    async def dispatch(self, request: Request, call_next: Callable):
        """Process request and validate authentication."""
        
        # Skip auth for health endpoints
        if request.url.path.startswith("/health") or request.url.path == "/":
            return await call_next(request)
        
        # Try JWT authentication first
        auth_header = request.headers.get("Authorization")
        user_context = None
        
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            payload = parse_jwt_token(token)
            
            if payload:
                user_context = extract_user_context(payload)
                logger.debug(
                    "JWT authenticated",
                    user_id=user_context.user_id,
                    roles=len(user_context.roles),
                    path=request.url.path
                )
            else:
                # Invalid JWT
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"error": "Invalid or expired JWT token"}
                )
        
        # Fall back to legacy X-User-Id header if allowed
        elif ALLOW_LEGACY_HEADER:
            user_id_header = request.headers.get("X-User-Id")
            
            if user_id_header:
                try:
                    user_id = str(uuid.UUID(user_id_header))
                    user_context = UserContext(
                        user_id=user_id,
                        is_legacy=True
                    )
                    logger.debug(
                        "Legacy auth (X-User-Id)",
                        user_id=user_id,
                        path=request.url.path
                    )
                except ValueError:
                    return JSONResponse(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        content={"error": "Invalid X-User-Id format (must be UUID)"}
                    )
        
        # No authentication provided
        if not user_context:
            logger.warning(
                "Missing authentication",
                path=request.url.path,
                method=request.method
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "Missing Authorization header or X-User-Id"}
            )
        
        # Attach user context to request state
        request.state.user_id = user_context.user_id
        request.state.user_email = user_context.email
        request.state.user_roles = user_context.roles
        request.state.is_legacy_auth = user_context.is_legacy
        
        # Store permission arrays for RLS session variables
        request.state.role_ids_read = user_context.read_role_ids
        request.state.role_ids_create = user_context.create_role_ids
        request.state.role_ids_update = user_context.update_role_ids
        request.state.role_ids_delete = user_context.delete_role_ids
        
        # Process request
        response = await call_next(request)
        return response


# ============================================================================
# Database Session Variable Helper
# ============================================================================

def get_rls_session_vars(request: Request) -> dict:
    """
    Get PostgreSQL session variables for RLS from request state.
    
    Returns dict suitable for SET LOCAL statements:
    {
        "app.user_id": "user-uuid",
        "app.user_role_ids_read": "role-uuid-1,role-uuid-2",
        "app.user_role_ids_create": "role-uuid-1",
        "app.user_role_ids_update": "role-uuid-1",
        "app.user_role_ids_delete": "role-uuid-1"
    }
    """
    return {
        "app.user_id": getattr(request.state, "user_id", ""),
        "app.user_role_ids_read": ",".join(getattr(request.state, "role_ids_read", [])),
        "app.user_role_ids_create": ",".join(getattr(request.state, "role_ids_create", [])),
        "app.user_role_ids_update": ",".join(getattr(request.state, "role_ids_update", [])),
        "app.user_role_ids_delete": ",".join(getattr(request.state, "role_ids_delete", [])),
    }


async def set_rls_session_vars(conn, request: Request):
    """
    Set PostgreSQL session variables for RLS enforcement.
    
    Call this at the start of database operations to enable RLS filtering.
    """
    session_vars = get_rls_session_vars(request)
    
    for var_name, var_value in session_vars.items():
        await conn.execute(f"SET LOCAL {var_name} = '{var_value}'")
    
    logger.debug(
        "RLS session variables set",
        user_id=session_vars["app.user_id"],
        read_roles=len(session_vars["app.user_role_ids_read"].split(",")) if session_vars["app.user_role_ids_read"] else 0
    )


def set_rls_session_vars_sync(cursor, request: Request):
    """
    Set PostgreSQL session variables for RLS enforcement (synchronous version).
    
    Call this at the start of database operations to enable RLS filtering.
    """
    session_vars = get_rls_session_vars(request)
    
    for var_name, var_value in session_vars.items():
        cursor.execute(f"SET LOCAL {var_name} = %s", (var_value,))
    
    logger.debug(
        "RLS session variables set (sync)",
        user_id=session_vars["app.user_id"],
        read_roles=len(session_vars["app.user_role_ids_read"].split(",")) if session_vars["app.user_role_ids_read"] else 0
    )


# ============================================================================
# Permission Checking Utilities
# ============================================================================

def has_create_permission(request: Request, role_id: str) -> bool:
    """Check if user has create permission on a specific role."""
    return role_id in getattr(request.state, "role_ids_create", [])


def has_read_permission(request: Request, role_id: str) -> bool:
    """Check if user has read permission on a specific role."""
    return role_id in getattr(request.state, "role_ids_read", [])


def has_update_permission(request: Request, role_id: str) -> bool:
    """Check if user has update permission on a specific role."""
    return role_id in getattr(request.state, "role_ids_update", [])


def has_delete_permission(request: Request, role_id: str) -> bool:
    """Check if user has delete permission on a specific role."""
    return role_id in getattr(request.state, "role_ids_delete", [])


def has_any_create_permission(request: Request, role_ids: List[str]) -> bool:
    """Check if user has create permission on any of the specified roles."""
    user_create_roles = set(getattr(request.state, "role_ids_create", []))
    return bool(user_create_roles.intersection(role_ids))


def has_all_delete_permission(request: Request, role_ids: List[str]) -> bool:
    """Check if user has delete permission on ALL of the specified roles."""
    user_delete_roles = set(getattr(request.state, "role_ids_delete", []))
    return set(role_ids).issubset(user_delete_roles)

