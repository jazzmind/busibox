"""
Shared Authentication Utilities for Busibox Services.

This module provides common JWT authentication, token exchange, scope checking,
and RLS utilities used across all busibox services (ingest, search, agent).

Features:
- JWT token validation via JWKS (using pyjwt library)
- User context extraction (user_id, email, scopes, roles)
- OAuth2 token exchange for service-to-service calls
- OAuth2 scope checking utilities
- PostgreSQL RLS session variable helpers
- Milvus partition building utilities
- FastAPI dependencies for scope enforcement

Environment Variables:
- AUTHZ_JWKS_URL / JWT_JWKS_URL: URL to JWKS endpoint
- AUTHZ_ISSUER / JWT_ISSUER: Expected JWT issuer (default: busibox-authz)
- AUTHZ_AUDIENCE / JWT_AUDIENCE: Expected JWT audience (service-specific)
- JWT_ALGORITHMS: Comma-separated list of algorithms (default: RS256)
- AUTHZ_TOKEN_URL: Token endpoint for OAuth2 token exchange
- API_SERVICE_CLIENT_ID: Client ID for service-to-service token exchange
- API_SERVICE_CLIENT_SECRET: Client secret for token exchange
"""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Set

import httpx
import jwt
import structlog
from cachetools import TTLCache
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger()

# Token exchange grant type constant
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"


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
    - token: The raw JWT token string (for token exchange)
    """
    user_id: str
    email: Optional[str] = None
    scopes: Set[str] = field(default_factory=set)
    roles: List[Role] = field(default_factory=list)
    authorization_header: Optional[str] = None
    token: Optional[str] = None
    
    @property
    def role_ids(self) -> List[str]:
        """Get all role IDs user has membership in."""
        return [r.id for r in self.roles]
    
    @property
    def role_names(self) -> List[str]:
        """Get all role names user has membership in."""
        return [r.name for r in self.roles]
    
    def has_scope(self, scope: str) -> bool:
        """
        Check if user has a specific scope.
        
        Supports wildcard matching:
        - Exact match: 'ingest.write' matches 'ingest.write'
        - Wildcard: 'ingest.*' matches 'ingest.write', 'ingest.read', etc.
        - Nested wildcard: 'authz.*' matches 'authz.users.read', 'authz.roles.write', etc.
        - Full wildcard: '*' matches everything
        """
        # Direct match
        if scope in self.scopes:
            return True
        
        # Check for full wildcard '*'
        if '*' in self.scopes:
            return True
        
        # Check for wildcard scopes at each level
        # e.g., for 'authz.users.read', check 'authz.users.*' and 'authz.*'
        parts = scope.split('.')
        for i in range(len(parts) - 1, 0, -1):
            prefix = '.'.join(parts[:i])
            wildcard_scope = f"{prefix}.*"
            if wildcard_scope in self.scopes:
                return True
        
        return False
    
    def has_any_scope(self, scopes: List[str]) -> bool:
        """Check if user has any of the specified scopes (supports wildcards)."""
        return any(self.has_scope(scope) for scope in scopes)
    
    def has_all_scopes(self, scopes: List[str]) -> bool:
        """Check if user has all of the specified scopes (supports wildcards)."""
        return all(self.has_scope(scope) for scope in scopes)


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


def extract_user_context(
    payload: dict, 
    auth_header: Optional[str] = None,
    token: Optional[str] = None,
) -> UserContext:
    """
    Extract UserContext from JWT payload.
    
    Args:
        payload: Decoded JWT payload
        auth_header: Original Authorization header for passthrough
        token: Raw JWT token string (for token exchange)
    
    Returns:
        UserContext with extracted user information.
    """
    user_id = payload.get("sub", "")
    email = payload.get("email")
    
    # Extract scopes (space-delimited string or list)
    scope_claim = payload.get("scope", "")
    if isinstance(scope_claim, str):
        scopes = set(s for s in scope_claim.split() if s)
    elif isinstance(scope_claim, list):
        scopes = set(str(s) for s in scope_claim if s)
    else:
        scopes = set()
    
    # Also check for 'scp' claim (used by some providers)
    scp_claim = payload.get("scp", [])
    if isinstance(scp_claim, list):
        scopes.update(str(s) for s in scp_claim if s)
    
    # Extract roles (for data access filtering)
    roles = []
    for role_data in payload.get("roles", []):
        if isinstance(role_data, str):
            # Just a role ID/name string
            roles.append(Role(id=role_data, name=role_data))
        elif isinstance(role_data, dict):
            # Role object from authz: {"id": "...", "name": "..."}
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
        token=token,
    )


# ============================================================================
# Token Exchange
# ============================================================================

# Global token cache for service-to-service token exchange
# TTL is set to 1 day to support long-lived delegation tokens while still refreshing periodically
# For short-lived tokens, a new token will be fetched when needed anyway
_token_cache: TTLCache = TTLCache(maxsize=1000, ttl=86400)  # 1 day cache


class TokenExchangeClient:
    """
    Client for OAuth2 Token Exchange (RFC 8693).
    
    Used for service-to-service authentication where one service needs to
    call another service on behalf of a user.
    
    The exchanged token will have:
    - The correct audience for the target service
    - The original user's identity (sub) and roles preserved
    - Proper RLS enforcement in the downstream service
    
    Usage:
        client = TokenExchangeClient()
        token = await client.get_token_for_service(
            user_id="user-uuid",
            target_audience="ingest-api",
        )
        if token:
            headers = {"Authorization": f"Bearer {token}"}
    """
    
    def __init__(
        self,
        token_url: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        timeout: float = 10.0,
    ):
        """
        Initialize token exchange client.
        
        Args:
            token_url: OAuth2 token endpoint URL (default from env)
            client_id: OAuth2 client ID (default from env)
            client_secret: OAuth2 client secret (default from env)
            timeout: HTTP request timeout in seconds
        """
        self.token_url = token_url or os.environ.get(
            "AUTHZ_TOKEN_URL", "http://10.96.200.210:8010/oauth/token"
        )
        self.client_id = client_id or os.environ.get(
            "API_SERVICE_CLIENT_ID", "api-service"
        )
        self.client_secret = client_secret or os.environ.get(
            "API_SERVICE_CLIENT_SECRET", ""
        )
        self.timeout = timeout
    
    @classmethod
    def from_config(cls, config: Dict) -> "TokenExchangeClient":
        """
        Create a TokenExchangeClient from a config dictionary.
        
        Args:
            config: Dictionary with authz_token_url, api_service_client_id,
                    api_service_client_secret keys
        
        Returns:
            Configured TokenExchangeClient instance
        """
        return cls(
            token_url=config.get("authz_token_url"),
            client_id=config.get("api_service_client_id"),
            client_secret=config.get("api_service_client_secret"),
        )
    
    async def get_token_for_service(
        self,
        user_id: str,
        target_audience: str,
        scope: str = "read write",
        use_cache: bool = True,
    ) -> Optional[str]:
        """
        Get a token for calling another service on behalf of a user.
        
        Args:
            user_id: The user ID to impersonate (from the incoming request's JWT)
            target_audience: The audience of the target service (e.g., "ingest-api")
            scope: Requested scopes (space-separated)
            use_cache: Whether to use cached tokens (default True)
        
        Returns:
            Access token string, or None if exchange fails
        """
        # Check cache first
        if use_cache:
            cache_key = f"{user_id}:{target_audience}"
            cached_token = _token_cache.get(cache_key)
            if cached_token:
                logger.debug(
                    "Using cached token for service call",
                    user_id=user_id,
                    target_audience=target_audience,
                )
                return cached_token
        
        if not self.client_secret:
            logger.error(
                "API service client secret not configured",
                client_id=self.client_id,
            )
            return None
        
        try:
            logger.info(
                "Exchanging token for service call",
                user_id=user_id,
                target_audience=target_audience,
            )
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.token_url,
                    data={
                        "grant_type": TOKEN_EXCHANGE_GRANT,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "requested_subject": user_id,
                        "audience": target_audience,
                        "scope": scope,
                    },
                )
                
                if response.status_code != 200:
                    logger.error(
                        "Token exchange failed",
                        status_code=response.status_code,
                        response=response.text[:200],
                        user_id=user_id,
                        target_audience=target_audience,
                    )
                    return None
                
                data = response.json()
                access_token = data.get("access_token")
                
                if access_token and use_cache:
                    # Cache the token
                    cache_key = f"{user_id}:{target_audience}"
                    _token_cache[cache_key] = access_token
                    logger.debug(
                        "Token exchange successful",
                        user_id=user_id,
                        target_audience=target_audience,
                        expires_in=data.get("expires_in"),
                    )
                
                return access_token
        
        except Exception as e:
            logger.error(
                "Token exchange error",
                error=str(e),
                user_id=user_id,
                target_audience=target_audience,
                exc_info=True,
            )
            return None
    
    async def exchange_token(
        self,
        user_context: UserContext,
        scopes: List[str],
        purpose: str,
    ) -> Optional[Dict]:
        """
        Exchange a user token for a downstream token.
        
        This is a higher-level method that infers the target audience from
        the purpose and returns full token response data.
        
        Args:
            user_context: Current user context with token
            scopes: List of scopes to request
            purpose: Purpose string (e.g., "ingest", "search", "rag")
        
        Returns:
            Dict with access_token, token_type, expires_at, scopes or None
        """
        audience = self._audience_for_purpose(purpose, scopes)
        
        token = await self.get_token_for_service(
            user_id=user_context.user_id,
            target_audience=audience,
            scope=" ".join(scopes),
        )
        
        if not token:
            return None
        
        # Standard response format
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=840)  # ~14 min
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": expires_at,
            "scopes": scopes,
        }
    
    def _audience_for_purpose(self, purpose: str, scopes: List[str]) -> str:
        """
        Infer the downstream audience from purpose and/or scopes.
        
        Args:
            purpose: Purpose string (e.g., "ingest", "search", "rag")
            scopes: List of requested scopes
        
        Returns:
            Target audience string (e.g., "ingest-api")
        """
        p = (purpose or "").lower()
        if "ingest" in p:
            return "ingest-api"
        if "search" in p or "rag" in p:
            return "search-api"
        
        # Fallback: infer by scope prefix
        for s in scopes:
            if s.startswith("ingest."):
                return "ingest-api"
            if s.startswith("search."):
                return "search-api"
        
        return "agent-api"


# Legacy alias for backward compatibility
TokenExchangeService = TokenExchangeClient


# ============================================================================
# Zero Trust Token Exchange (Preferred Method)
# ============================================================================

# Cache for Zero Trust tokens: key = "zt:{user_id}:{audience}"
_zero_trust_cache: TTLCache = TTLCache(maxsize=1000, ttl=840)  # 14 min TTL


@dataclass
class TokenExchangeResult:
    """Result from token exchange operation."""
    access_token: str
    expires_in: int  # seconds until expiry


async def exchange_token_zero_trust(
    subject_token: str,
    target_audience: str,
    user_id: str,
    scopes: str = "",
    authz_url: Optional[str] = None,
    use_cache: bool = True,
) -> Optional[TokenExchangeResult]:
    """
    Exchange a user's token for a downstream service token (Zero Trust).
    
    This uses RFC 8693 token exchange with the user's JWT as subject_token.
    NO client credentials are used - the user's token cryptographically proves identity.
    
    The authz service will:
    1. Verify the subject_token is signed by authz and not expired
    2. Look up the user's roles and aggregate their scopes
    3. Issue a new token with the requested audience
    
    Args:
        subject_token: The user's current JWT token (any audience accepted)
        target_audience: Target service audience (e.g., "ingest-api", "search-api")
        user_id: User ID for logging and caching
        scopes: Requested scopes (optional, scopes come from RBAC)
        authz_url: Token endpoint URL (defaults to env var AUTHZ_TOKEN_URL)
        use_cache: Whether to cache tokens (default True)
        
    Returns:
        TokenExchangeResult with access_token and expires_in, or None if exchange fails
    """
    # Check cache first
    if use_cache:
        cache_key = f"zt:{user_id}:{target_audience}"
        cached_result = _zero_trust_cache.get(cache_key)
        if cached_result:
            logger.debug(
                "Using cached Zero Trust token",
                user_id=user_id,
                target_audience=target_audience,
            )
            return cached_result
    
    # Get authz URL
    token_url = authz_url or os.environ.get(
        "AUTHZ_TOKEN_URL",
        os.environ.get("AUTH_TOKEN_URL", "http://authz-api:8010/oauth/token")
    )
    
    try:
        logger.info(
            "Zero Trust token exchange",
            user_id=user_id,
            target_audience=target_audience,
        )
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": TOKEN_EXCHANGE_GRANT,
                    "subject_token": subject_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                    "audience": target_audience,
                    "scope": scopes,
                },
            )
            
            if response.status_code != 200:
                logger.error(
                    "Zero Trust token exchange failed",
                    status_code=response.status_code,
                    response=response.text[:200],
                    user_id=user_id,
                    target_audience=target_audience,
                )
                return None
            
            data = response.json()
            access_token = data.get("access_token")
            expires_in = data.get("expires_in", 900)  # Default 15 min if not provided
            
            if access_token:
                logger.info(
                    "Zero Trust token exchange successful",
                    user_id=user_id,
                    target_audience=target_audience,
                    expires_in=expires_in,
                )
                
                result = TokenExchangeResult(access_token=access_token, expires_in=expires_in)
                
                # Cache the token
                if use_cache:
                    cache_key = f"zt:{user_id}:{target_audience}"
                    _zero_trust_cache[cache_key] = result
                
                return result
            
            return None
    
    except Exception as e:
        logger.error(
            "Zero Trust token exchange error",
            error=str(e),
            user_id=user_id,
            target_audience=target_audience,
            exc_info=True,
        )
        return None


def clear_zero_trust_cache():
    """Clear the Zero Trust token cache (useful for testing)."""
    _zero_trust_cache.clear()


def clear_token_cache():
    """Clear the token exchange cache (useful for testing)."""
    _token_cache.clear()


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
                user_context = extract_user_context(payload, auth_header, token)
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
        from busibox_common.auth import WorkerRLSContext, set_rls_session_vars_sync
        
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
        "app.user_role_ids_read": "role-uuid-1,role-uuid-2",  # CSV for SELECT
        "app.user_role_ids_create": "role-uuid-1,role-uuid-2",  # CSV for INSERT
        "app.user_role_ids_update": "role-uuid-1,role-uuid-2",  # CSV for UPDATE
        "app.user_role_ids_delete": "role-uuid-1,role-uuid-2",  # CSV for DELETE
    }
    
    Note: Role IDs are formatted as CSV (not JSON) to work with PostgreSQL's
    string_to_array function in RLS policies.
    """
    role_ids = getattr(request.state, "role_ids", [])
    # Convert role_ids list to CSV string (RLS policies use string_to_array)
    role_ids_csv = ",".join(role_ids) if role_ids else ""
    
    return {
        "app.user_id": getattr(request.state, "user_id", ""),
        # For now, all CRUD operations get the same roles
        # In the future, this could be refined based on role-specific permissions
        "app.user_role_ids_read": role_ids_csv,
        "app.user_role_ids_create": role_ids_csv,
        "app.user_role_ids_update": role_ids_csv,
        "app.user_role_ids_delete": role_ids_csv,
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
