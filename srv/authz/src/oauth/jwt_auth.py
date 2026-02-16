"""
JWT-based authentication for authz admin endpoints.

This module provides helpers for verifying access tokens and checking scopes.
It enables Zero Trust authentication for admin operations - instead of using
static admin tokens, callers authenticate with JWTs that have specific scopes.

Supported authentication methods (in order of precedence):
1. Access token with required scopes (audience: authz-api)
2. Session JWT for self-service operations (audience: busibox-portal, typ: session)
3. Service account (client_credentials) with allowed_scopes

Note: Legacy admin token support has been removed for security.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

import jwt
from fastapi import HTTPException, Request, status

from config import Config
from oauth.client_auth import verify_client_secret
from oauth.keys import load_private_key

config = Config()


def _scope_matches(granted_scope: str, required_scope: str) -> bool:
    """
    Check if a granted scope matches a required scope.
    
    Supports glob-style wildcards:
    - "authz.*" matches "authz.users.read", "authz.roles.write", etc.
    - "authz.users.*" matches "authz.users.read", "authz.users.write"
    - Exact matches always work: "authz.users.read" matches "authz.users.read"
    
    Note: Wildcards only work in granted scopes (from roles), not required scopes.
    """
    # Exact match
    if granted_scope == required_scope:
        return True
    
    # Glob match: "authz.*" matches "authz.users.read"
    if granted_scope.endswith(".*"):
        prefix = granted_scope[:-1]  # "authz." from "authz.*"
        return required_scope.startswith(prefix)
    
    return False


@dataclass
class AuthContext:
    """Authentication context for a request."""
    auth_type: str  # "jwt", "session", or "service_account"
    actor_id: str  # User ID (for JWT/session) or client_id (for service account)
    scopes: Set[str]  # Available scopes for this request
    email: Optional[str] = None  # User email (for JWT/session only)
    roles: Optional[List[dict]] = None  # User roles (for session JWT only)
    
    def has_scope(self, scope: str) -> bool:
        """
        Check if this auth context has a specific scope.
        
        Supports glob-style wildcards in granted scopes:
        - If granted "authz.*", will match required "authz.users.read"
        """
        return any(_scope_matches(granted, scope) for granted in self.scopes)
    
    def has_any_scope(self, scopes: List[str]) -> bool:
        """
        Check if this auth context has any of the specified scopes.
        
        Supports glob-style wildcards in granted scopes.
        """
        return any(self.has_scope(required) for required in scopes)
    
    def require_scope(self, scope: str) -> None:
        """Raise HTTPException if scope is not present."""
        if not self.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope: {scope}",
            )
    
    def has_role(self, role_name: str) -> bool:
        """Check if this auth context has a specific role by name."""
        if not self.roles:
            return False
        return any(r.get("name") == role_name for r in self.roles)
    
    def is_admin(self) -> bool:
        """Check if this auth context has the admin role."""
        return self.has_role("admin")


async def verify_access_token(
    token: str,
    db,
    required_audience: str = "authz-api",
) -> Tuple[str, str, Set[str]]:
    """
    Verify an access token JWT signed by authz.
    
    Returns (user_id, email, scopes) if valid.
    Raises HTTPException if invalid.
    """
    await db.connect()
    
    # Get the active signing key's public key for verification
    row = await db.get_active_signing_key()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="no_signing_key_configured"
        )
    
    kid = row["kid"]
    alg = row["alg"]
    
    # Load private key to extract public key
    private_pem = row["private_key_pem"]
    private_key = load_private_key(private_pem, config.key_encryption_passphrase)
    public_key = private_key.public_key()
    
    try:
        # First decode without verification to get the header
        token_kid = jwt.get_unverified_header(token).get("kid")
        
        # Verify the token was signed by our key
        if token_kid != kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_token_key"
            )
        
        # Verify the signature and claims
        claims = jwt.decode(
            token,
            public_key,
            algorithms=[alg],
            issuer=config.issuer,
            audience=required_audience,
            options={"require": ["exp", "iat", "sub", "typ"]}
        )
        
        # Verify token type is access
        token_type = claims.get("typ")
        if token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_token_type"
            )
        
        user_id = claims["sub"]
        email = claims.get("email", "")
        scope_str = claims.get("scope", "")
        scopes = set(scope_str.split()) if scope_str else set()
        
        return user_id, email, scopes
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token_expired"
        )
    except jwt.InvalidAudienceError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_audience"
        )
    except jwt.InvalidIssuerError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_issuer"
        )
    except jwt.DecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid_token: {str(e)}"
        )


async def verify_session_token(
    token: str,
    db,
) -> Tuple[str, str, List[dict]]:
    """
    Verify a session JWT signed by authz.
    
    Session JWTs are used for self-service operations where the user
    is accessing/modifying their own resources.
    
    Returns (user_id, email, roles) if valid.
    Raises HTTPException if invalid.
    """
    await db.connect()
    
    # Get the active signing key's public key for verification
    row = await db.get_active_signing_key()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="no_signing_key_configured"
        )
    
    kid = row["kid"]
    alg = row["alg"]
    
    # Load private key to extract public key
    private_pem = row["private_key_pem"]
    private_key = load_private_key(private_pem, config.key_encryption_passphrase)
    public_key = private_key.public_key()
    
    try:
        # First decode without verification to get the header
        token_kid = jwt.get_unverified_header(token).get("kid")
        
        # Verify the token was signed by our key
        if token_kid != kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_token_key"
            )
        
        # Verify the signature and claims
        # Session tokens have audience "busibox-portal"
        claims = jwt.decode(
            token,
            public_key,
            algorithms=[alg],
            issuer=config.issuer,
            audience="busibox-portal",
            options={"require": ["exp", "iat", "sub", "jti", "typ"]}
        )
        
        # Verify token type is session
        token_type = claims.get("typ")
        if token_type != "session":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_token_type"
            )
        
        # Check if session has been revoked (jti = session_id)
        jti = claims["jti"]
        session = await db.get_session_by_id(jti)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="session_revoked"
            )
        
        user_id = claims["sub"]
        email = claims.get("email", "")
        roles = claims.get("roles", [])
        
        return user_id, email, roles
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token_expired"
        )
    except jwt.InvalidAudienceError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_audience"
        )
    except jwt.InvalidIssuerError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_issuer"
        )
    except jwt.DecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid_token: {str(e)}"
        )


async def authenticate_self_service(
    request: Request,
    db,
    required_user_id: Optional[str] = None,
) -> AuthContext:
    """
    Authenticate a request for self-service operations using session JWT.
    
    Self-service operations allow users to access/modify their own resources
    without needing additional scopes or token exchange.
    
    Args:
        request: FastAPI request
        db: PostgresService instance  
        required_user_id: Optional user ID to check ownership. If provided,
                         the session's user must match this ID.
        
    Returns:
        AuthContext with user info from session JWT
        
    Raises:
        HTTPException if authentication fails or ownership check fails
    """
    auth_header = request.headers.get("authorization", "")
    
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing session token",
        )
    
    token = auth_header[7:]
    
    try:
        user_id, email, roles = await verify_session_token(token, db)
        
        # Check ownership if required
        if required_user_id and user_id != required_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot access another user's resources",
            )
        
        return AuthContext(
            auth_type="session",
            actor_id=user_id,
            scopes=set(),  # Session tokens don't have scopes
            email=email,
            roles=roles,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid session token: {str(e)}",
        )


async def authenticate_request(
    request: Request,
    db,
    required_scopes: Optional[List[str]] = None,
) -> AuthContext:
    """
    Authenticate a request and return the auth context.
    
    Tries authentication methods in order:
    1. Bearer token (JWT access token with audience=authz-api)
    2. Client credentials in request body (service account)
    
    Args:
        request: FastAPI request
        db: PostgresService instance
        required_scopes: Optional list of scopes - at least one must be present
        
    Returns:
        AuthContext with actor info and available scopes
        
    Raises:
        HTTPException if authentication fails or required scopes missing
    """
    auth_header = request.headers.get("authorization", "")
    
    # Try Bearer token (JWT)
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        
        # Try to verify as JWT
        try:
            user_id, email, scopes = await verify_access_token(token, db, "authz-api")
            ctx = AuthContext(
                auth_type="jwt",
                actor_id=user_id,
                scopes=scopes,
                email=email,
            )
            
            # Check required scopes
            if required_scopes:
                if not ctx.has_any_scope(required_scopes):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Insufficient scopes. Required one of: {required_scopes}",
                    )
            
            return ctx
            
        except HTTPException:
            # JWT verification failed - will try other methods below
            pass
    
    # Try client credentials in body (service account)
    try:
        body = await request.json()
        client_id = body.get("client_id")
        client_secret = body.get("client_secret")
        
        if client_id and client_secret:
            await db.connect()
            client = await db.get_oauth_client(client_id)
            if client and client.get("is_active"):
                if verify_client_secret(client_secret, client["client_secret_hash"]):
                    allowed_scopes = set(client.get("allowed_scopes", []))
                    ctx = AuthContext(
                        auth_type="service_account",
                        actor_id=client_id,
                        scopes=allowed_scopes,
                    )
                    
                    # Check required scopes
                    if required_scopes:
                        if not ctx.has_any_scope(required_scopes):
                            raise HTTPException(
                                status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Service account lacks required scopes. Required one of: {required_scopes}",
                            )
                    
                    return ctx
    except Exception:
        pass
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: valid access token or service account credentials required",
    )


async def require_auth(
    request: Request,
    db,
    scopes: Optional[List[str]] = None,
) -> AuthContext:
    """
    Convenience function to require authentication with optional scope check.
    
    This is the main entry point for admin endpoints.
    
    Example:
        @router.post("/admin/users")
        async def create_user(request: Request):
            auth = await require_auth(request, db, scopes=["authz.users.write"])
            # auth.actor_id contains the authenticated user/service
            ...
    """
    return await authenticate_request(request, db, scopes)


async def require_auth_or_self_service(
    request: Request,
    db,
    self_service_user_id: str,
    admin_scopes: Optional[List[str]] = None,
) -> AuthContext:
    """
    Authenticate a request, allowing either:
    1. Access token/service account with required scopes (for admin operations)
    2. Session JWT where user is accessing their own resources (self-service)
    
    This is the main entry point for endpoints that support both admin access
    and user self-service.
    
    Args:
        request: FastAPI request
        db: PostgresService instance
        self_service_user_id: User ID being accessed. If the session JWT's sub
                             matches this, access is granted without scope checks.
        admin_scopes: Scopes required for admin access (accessing other users)
        
    Returns:
        AuthContext with actor info
        
    Example:
        @router.get("/passkeys/user/{user_id}")
        async def list_user_passkeys(request: Request, user_id: str):
            # User can list their own passkeys (self-service)
            # OR admin can list any user's passkeys (with scope)
            auth = await require_auth_or_self_service(
                request, db, 
                self_service_user_id=user_id,
                admin_scopes=["authz.passkeys.read"]
            )
            ...
    """
    auth_header = request.headers.get("authorization", "")
    
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
        )
    
    token = auth_header[7:]
    
    # First, try to verify as session JWT for self-service
    try:
        user_id, email, roles = await verify_session_token(token, db)
        
        # If this is self-service (user accessing their own resource), allow it
        if user_id == self_service_user_id:
            return AuthContext(
                auth_type="session",
                actor_id=user_id,
                scopes=set(),
                email=email,
                roles=roles,
            )
        
        # User is trying to access someone else's resource with session token
        # Check if they have admin role
        has_admin = any(r.get("name") == "admin" for r in roles)
        if has_admin:
            return AuthContext(
                auth_type="session",
                actor_id=user_id,
                scopes=set(),
                email=email,
                roles=roles,
            )
        
        # Not self-service and not admin - fall through to try access token
        
    except HTTPException:
        # Not a valid session token - try as access token
        pass
    
    # Try as access token (with scopes check)
    try:
        user_id, email, scopes = await verify_access_token(token, db, "authz-api")
        ctx = AuthContext(
            auth_type="jwt",
            actor_id=user_id,
            scopes=scopes,
            email=email,
        )
        
        # Check required scopes for admin access
        if admin_scopes:
            if not ctx.has_any_scope(admin_scopes):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Insufficient scopes. Required one of: {admin_scopes}",
                )
        
        return ctx
        
    except HTTPException:
        pass
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: valid access token or session token required",
    )
