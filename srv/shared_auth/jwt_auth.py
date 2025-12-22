"""
Shared JWT Authentication Utilities for Busibox Services

This module provides common JWT authentication, scope checking, and RLS
utilities used across all busibox services (ingest, search, agent).

Features:
- JWT token validation via JWKS
- User context extraction (user_id, email, scopes, roles)
- OAuth2 scope checking utilities
- PostgreSQL RLS session variable helpers
- Milvus partition building utilities
- FastAPI dependencies for scope enforcement

Environment Variables:
- AUTHZ_JWKS_URL / JWT_JWKS_URL: URL to JWKS endpoint
- AUTHZ_ISSUER / JWT_ISSUER: Expected JWT issuer (default: busibox-authz)
- AUTHZ_AUDIENCE / JWT_AUDIENCE: Expected JWT audience (service-specific)
- JWT_ALGORITHMS: Comma-separated list of algorithms (default: RS256)
"""

import json
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
# JWKS Client Factory
# ============================================================================

def create_jwks_client(jwks_url: Optional[str] = None) -> jwt.PyJWKClient:
    """
    Create a PyJWKClient for JWT validation.
    
    Args:
        jwks_url: URL to JWKS endpoint. If not provided, uses environment variables.
    
    Returns:
        Configured PyJWKClient instance.
    """
    url = jwks_url or (
        os.environ.get("AUTHZ_JWKS_URL")
        or os.environ.get("JWT_JWKS_URL")
        or "http://10.96.200.210:8010/.well-known/jwks.json"
    )
    return jwt.PyJWKClient(url)


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
    """
    User context extracted from JWT.
    
    Contains:
    - user_id: User's unique identifier (from JWT 'sub' claim)
    - email: User's email address (optional)
    - scopes: Set of OAuth2 scopes for operation authorization
    - roles: List of Role objects for data access filtering
    - authorization_header: Original auth header for passthrough to downstream services
    """
    user_id: str
    email: Optional[str] = None
    scopes: Set[str] = field(default_factory=set)
    roles: List[Role] = field(default_factory=list)
    authorization_header: Optional[str] = None
    
    @property
    def role_ids(self) -> List[str]:
        """Get all role IDs user has membership in."""
        return [r.id for r in self.roles]
    
    @property
    def role_names(self) -> List[str]:
        """Get all role names user has membership in."""
        return [r.name for r in self.roles]
    
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

def parse_jwt_token(
    token: str,
    jwks_client: jwt.PyJWKClient,
    audience: str,
    issuer: Optional[str] = None,
    algorithms: Optional[List[str]] = None,
) -> Optional[dict]:
    """
    Verify and decode a JWT token.
    
    Args:
        token: The JWT token string
        jwks_client: PyJWKClient for signature verification
        audience: Expected audience claim
        issuer: Expected issuer claim (default from env or 'busibox-authz')
        algorithms: List of allowed algorithms (default from env or ['RS256'])
    
    Returns:
        Decoded payload dict or None if invalid.
    """
    if issuer is None:
        issuer = os.environ.get("AUTHZ_ISSUER") or os.environ.get("JWT_ISSUER", "busibox-authz")
    
    if algorithms is None:
        alg_str = os.environ.get("JWT_ALGORITHMS", "RS256")
        algorithms = [a.strip() for a in alg_str.split(",") if a.strip()]
    
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=algorithms,
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud", "jti"]},
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token expired")
        return None
    except jwt.InvalidAudienceError:
        logger.warning("JWT invalid audience", expected=audience)
        return None
    except jwt.InvalidIssuerError:
        logger.warning("JWT invalid issuer", expected=issuer)
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("JWT invalid token", error=str(e))
        return None


def extract_user_context(payload: dict, auth_header: Optional[str] = None) -> UserContext:
    """
    Extract UserContext from JWT payload.
    
    Args:
        payload: Decoded JWT payload
        auth_header: Original Authorization header for passthrough
    
    Returns:
        UserContext with extracted user information.
    """
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
        authorization_header=auth_header,
    )


# ============================================================================
# Middleware
# ============================================================================

class JWTAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to validate JWT tokens and extract user context.
    
    Requires JWT via Authorization: Bearer <token>
    
    Sets on request.state:
    - user_id: User's UUID
    - user_email: User's email
    - user_context: Full UserContext object
    - scopes: Set of OAuth2 scopes
    - role_ids: List of role UUIDs
    - role_names: List of role names
    - authorization: Original auth header for passthrough
    
    Args:
        app: FastAPI/Starlette application
        audience: Expected JWT audience (e.g., 'ingest-api', 'search-api')
        jwks_url: Optional JWKS URL (defaults to env vars)
        skip_paths: List of path prefixes to skip auth (default: ['/health'])
    """
    
    def __init__(
        self,
        app,
        audience: Optional[str] = None,
        jwks_url: Optional[str] = None,
        skip_paths: Optional[List[str]] = None,
    ):
        super().__init__(app)
        self.audience = audience or os.environ.get("AUTHZ_AUDIENCE") or os.environ.get("JWT_AUDIENCE", "api")
        self.jwks_client = create_jwks_client(jwks_url)
        self.skip_paths = skip_paths or ["/health", "/"]
    
    async def dispatch(self, request: Request, call_next: Callable):
        """Process request and validate authentication."""
        
        # Skip auth for configured paths
        for path in self.skip_paths:
            if request.url.path == path or request.url.path.startswith(path + "/"):
                return await call_next(request)
        
        # JWT authentication required
        auth_header = request.headers.get("authorization")
        user_context = None
        
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            payload = parse_jwt_token(
                token,
                self.jwks_client,
                self.audience,
            )
            
            if payload:
                user_context = extract_user_context(payload, auth_header)
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
        request.state.role_names = user_context.role_names
        request.state.authorization = user_context.authorization_header
        
        # Process request
        response = await call_next(request)
        return response


# ============================================================================
# PostgreSQL RLS Session Variable Helpers
# ============================================================================

class WorkerRLSContext:
    """
    Mock request-like object for workers that need to set RLS context.
    
    Workers operate outside of HTTP request context but still need to set
    RLS session variables to enforce row-level security.
    
    Usage:
        from shared_auth.jwt_auth import WorkerRLSContext, set_rls_session_vars_sync
        
        # Create RLS context from job data
        rls_context = WorkerRLSContext(user_id=job_data["user_id"], role_ids=role_ids)
        
        # Use with database operations
        conn = postgres_service._get_connection(rls_context)
        # OR
        with conn.cursor() as cur:
            set_rls_session_vars_sync(cur, rls_context)
    """
    
    def __init__(self, user_id: str, role_ids: Optional[List[str]] = None):
        """
        Create RLS context for worker operations.
        
        Args:
            user_id: User ID who owns the data
            role_ids: List of role IDs for role-based access
        """
        self.state = type("State", (), {
            "user_id": user_id,
            "role_ids": role_ids or [],
        })()


def get_rls_session_vars(request) -> dict:
    """
    Get PostgreSQL session variables for RLS from request state.
    
    Works with both FastAPI Request objects and WorkerRLSContext.
    
    Returns dict suitable for SET statements:
    {
        "app.user_id": "user-uuid",
        "app.user_role_ids": '["role-uuid-1", "role-uuid-2"]'
    }
    """
    role_ids = getattr(request.state, "role_ids", [])
    return {
        "app.user_id": getattr(request.state, "user_id", ""),
        "app.user_role_ids": json.dumps(role_ids),
    }


async def set_rls_session_vars(conn, request):
    """
    Set PostgreSQL session variables for RLS enforcement (async).
    
    Call this at the start of database operations to enable RLS filtering.
    
    Note: Uses SET (not SET LOCAL) to persist for the connection session,
    ensuring variables are available even inside nested transaction blocks.
    
    Args:
        conn: asyncpg connection
        request: FastAPI request or WorkerRLSContext with user context
    """
    session_vars = get_rls_session_vars(request)
    
    for var_name, var_value in session_vars.items():
        await conn.execute(f"SET {var_name} = '{var_value}'")
    
    logger.debug(
        "RLS session variables set",
        user_id=session_vars["app.user_id"],
        role_count=len(getattr(request.state, "role_ids", []))
    )


def set_rls_session_vars_sync(cursor, request):
    """
    Set PostgreSQL session variables for RLS enforcement (sync).
    
    Call this at the start of database operations to enable RLS filtering.
    
    Note: Uses SET (not SET LOCAL) to persist for the connection session,
    ensuring variables are available even inside nested transaction blocks.
    
    Args:
        cursor: psycopg2 cursor
        request: FastAPI request or WorkerRLSContext with user context
    """
    session_vars = get_rls_session_vars(request)
    
    for var_name, var_value in session_vars.items():
        cursor.execute(f"SET {var_name} = %s", (var_value,))
    
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
    
    Args:
        request: FastAPI request with user context
        scope: Required scope (e.g., 'ingest.write')
    
    Raises:
        HTTPException: 403 if scope is missing
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
    
    Args:
        request: FastAPI request with user context
        scopes: List of acceptable scopes
    
    Raises:
        HTTPException: 403 if no scope matches
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


# ============================================================================
# Milvus Partition Utilities
# ============================================================================

def get_accessible_partitions(request: Request) -> List[str]:
    """
    Build list of Milvus partitions user can access.
    
    Returns partition names:
    - personal_{user_id}: User's personal documents
    - role_{role_id}: Shared documents by role
    
    Args:
        request: FastAPI request with user context
    
    Returns:
        List of partition names user can access
    """
    user_id = getattr(request.state, "user_id", None)
    role_ids = getattr(request.state, "role_ids", [])
    
    partitions = []
    
    # Personal partition
    if user_id:
        partitions.append(f"personal_{user_id}")
    
    # Role-based partitions
    for role_id in role_ids:
        partitions.append(f"role_{role_id}")
    
    return partitions


def get_partition_names_for_search(request: Request, include_personal: bool = True) -> List[str]:
    """
    Get partition names for a search query.
    
    Args:
        request: FastAPI request with user context
        include_personal: Whether to include personal partition (default True)
    
    Returns:
        List of partition names to search. Empty list means no access.
    """
    user_id = getattr(request.state, "user_id", None)
    role_ids = getattr(request.state, "role_ids", [])
    
    partitions = []
    
    if include_personal and user_id:
        partitions.append(f"personal_{user_id}")
    
    for role_id in role_ids:
        partitions.append(f"role_{role_id}")
    
    return partitions

