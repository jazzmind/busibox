"""
JWT-based authentication for authz admin endpoints.

This module provides helpers for verifying access tokens and checking scopes.
It enables Zero Trust authentication for admin operations - instead of using
static admin tokens, callers authenticate with JWTs that have specific scopes.

Supported authentication methods (in order of precedence):
1. Access token with required scopes (audience: authz-api)
2. Service account (client_credentials) with allowed_scopes
3. Legacy admin token (deprecated, will be removed)
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


@dataclass
class AuthContext:
    """Authentication context for a request."""
    auth_type: str  # "jwt", "service_account", or "admin_token"
    actor_id: str  # User ID (for JWT) or client_id (for service account)
    scopes: Set[str]  # Available scopes for this request
    email: Optional[str] = None  # User email (for JWT only)
    
    def has_scope(self, scope: str) -> bool:
        """Check if this auth context has a specific scope."""
        return scope in self.scopes
    
    def has_any_scope(self, scopes: List[str]) -> bool:
        """Check if this auth context has any of the specified scopes."""
        return bool(self.scopes & set(scopes))
    
    def require_scope(self, scope: str) -> None:
        """Raise HTTPException if scope is not present."""
        if not self.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope: {scope}",
            )


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
    3. Legacy admin token (deprecated)
    
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
        
        # Check if it's the legacy admin token first (for backward compatibility)
        if config.admin_token and token == config.admin_token:
            # Admin token has all scopes
            return AuthContext(
                auth_type="admin_token",
                actor_id="system",
                scopes={"*"},  # Wildcard - admin token can do anything
            )
        
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
    
    # Try legacy admin token in header (deprecated)
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        if config.admin_token and token == config.admin_token:
            return AuthContext(
                auth_type="admin_token",
                actor_id="system",
                scopes={"*"},
            )
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: valid access token, service account credentials, or admin token required",
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
