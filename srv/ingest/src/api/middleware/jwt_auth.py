"""
JWT Authentication Middleware for Role-Based Access Control

Validates JWT tokens from authz and extracts:
- User identity (sub, email)
- OAuth2 scopes for operation authorization
- Role memberships for data access (RLS)

Sets PostgreSQL session variables for Row-Level Security (RLS):
- app.user_id: User UUID
- app.user_role_ids: JSON array of role UUIDs user has membership in
"""

import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set

import jwt
import structlog
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger()

# ============================================================================
# Configuration
# ============================================================================

AUTHZ_JWKS_URL = (
    os.environ.get("AUTHZ_JWKS_URL")
    or os.environ.get("JWT_JWKS_URL")
    or "http://10.96.200.210:8010/.well-known/jwks.json"
)

JWT_ISSUER = os.environ.get("AUTHZ_ISSUER") or os.environ.get("JWT_ISSUER", "busibox-authz")
JWT_AUDIENCE = os.environ.get("AUTHZ_AUDIENCE") or os.environ.get("JWT_AUDIENCE", "ingest-api")
JWT_ALGORITHMS = [a.strip() for a in os.environ.get("JWT_ALGORITHMS", "RS256").split(",") if a.strip()]

jwks_client = jwt.PyJWKClient(AUTHZ_JWKS_URL)


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class Role:
    """Role for data access filtering."""
    id: str
    name: str


@dataclass
class UserContext:
    """User context extracted from JWT."""
    user_id: str
    email: Optional[str] = None
    scopes: Set[str] = field(default_factory=set)  # OAuth2 scopes for operation authorization
    roles: List[Role] = field(default_factory=list)  # Role memberships for data access
    
    @property
    def role_ids(self) -> List[str]:
        """Get all role IDs user has membership in."""
        return [r.id for r in self.roles]
    
    def has_scope(self, scope: str) -> bool:
        """Check if user has a specific scope."""
        return scope in self.scopes
    
    def has_any_scope(self, scopes: List[str]) -> bool:
        """Check if user has any of the specified scopes."""
        return bool(self.scopes.intersection(scopes))
    
    def has_all_scopes(self, scopes: List[str]) -> bool:
        """Check if user has all of the specified scopes."""
        return set(scopes).issubset(self.scopes)


# ============================================================================
# JWT Parsing
# ============================================================================

def parse_jwt_token(token: str) -> Optional[dict]:
    """
    Verify and decode a JWT token.
    
    Returns decoded payload or None if invalid.
    """
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=JWT_ALGORITHMS,
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "aud", "jti"]},
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
    
    # Extract scopes (space-delimited string)
    scope_str = payload.get("scope", "")
    scopes = set(s for s in scope_str.split() if s)
    
    # Extract roles (for data access filtering)
    roles = []
    for role_data in payload.get("roles", []):
        role = Role(
            id=role_data.get("id", ""),
            name=role_data.get("name", ""),
        )
        roles.append(role)
    
    return UserContext(
        user_id=user_id,
        email=email,
        scopes=scopes,
        roles=roles,
    )


# ============================================================================
# Middleware
# ============================================================================

class JWTAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to validate JWT tokens and extract user context.
    
    Requires JWT via Authorization: Bearer <token>
    """
    
    async def dispatch(self, request: Request, call_next: Callable):
        """Process request and validate authentication."""
        
        # Skip auth for health endpoints
        if request.url.path.startswith("/health") or request.url.path == "/":
            return await call_next(request)
        
        # JWT authentication required
        auth_header = request.headers.get("authorization")
        user_context = None
        
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            payload = parse_jwt_token(token)
            
            if payload:
                user_context = extract_user_context(payload)
                logger.debug(
                    "JWT authenticated",
                    user_id=user_context.user_id,
                    scopes=len(user_context.scopes),
                    roles=len(user_context.roles),
                    path=request.url.path
                )
            else:
                # Invalid JWT
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"error": "Invalid or expired JWT token"}
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
                content={"error": "Missing Authorization header"}
            )
        
        # Attach user context to request state
        request.state.user_id = user_context.user_id
        request.state.user_email = user_context.email
        request.state.user_context = user_context
        request.state.scopes = user_context.scopes
        request.state.role_ids = user_context.role_ids
        
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
        "app.user_role_ids": '["role-uuid-1", "role-uuid-2"]'
    }
    """
    import json
    role_ids = getattr(request.state, "role_ids", [])
    return {
        "app.user_id": getattr(request.state, "user_id", ""),
        "app.user_role_ids": json.dumps(role_ids),
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
        role_count=len(getattr(request.state, "role_ids", []))
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
        role_count=len(getattr(request.state, "role_ids", []))
    )


# ============================================================================
# Scope Checking Utilities
# ============================================================================

def require_scope(request: Request, scope: str) -> None:
    """
    Require a specific scope. Raises HTTPException if missing.
    """
    from fastapi import HTTPException
    user_context: UserContext = getattr(request.state, "user_context", None)
    if not user_context or not user_context.has_scope(scope):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient scope: {scope} required"
        )


def require_any_scope(request: Request, scopes: List[str]) -> None:
    """
    Require any of the specified scopes. Raises HTTPException if none present.
    """
    from fastapi import HTTPException
    user_context: UserContext = getattr(request.state, "user_context", None)
    if not user_context or not user_context.has_any_scope(scopes):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient scope: one of {scopes} required"
        )


def has_scope(request: Request, scope: str) -> bool:
    """Check if user has a specific scope."""
    user_context: UserContext = getattr(request.state, "user_context", None)
    return user_context is not None and user_context.has_scope(scope)


def has_role(request: Request, role_id: str) -> bool:
    """Check if user has membership in a specific role."""
    role_ids = getattr(request.state, "role_ids", [])
    return role_id in role_ids


def has_any_role(request: Request, role_ids: List[str]) -> bool:
    """Check if user has membership in any of the specified roles."""
    user_role_ids = set(getattr(request.state, "role_ids", []))
    return bool(user_role_ids.intersection(role_ids))


# ============================================================================
# FastAPI Dependencies for Scope Checking
# ============================================================================

class ScopeChecker:
    """
    FastAPI dependency for checking OAuth2 scopes.
    
    Usage:
        @router.post("/upload")
        async def upload(request: Request, _: None = Depends(ScopeChecker("ingest.write"))):
            ...
    """
    
    def __init__(self, required_scope: str):
        self.required_scope = required_scope
    
    def __call__(self, request: Request) -> None:
        require_scope(request, self.required_scope)


class AnyScopeChecker:
    """
    FastAPI dependency for checking any of multiple OAuth2 scopes.
    
    Usage:
        @router.get("/files/{id}")
        async def get_file(request: Request, _: None = Depends(AnyScopeChecker(["ingest.read", "search.read"]))):
            ...
    """
    
    def __init__(self, required_scopes: List[str]):
        self.required_scopes = required_scopes
    
    def __call__(self, request: Request) -> None:
        require_any_scope(request, self.required_scopes)
