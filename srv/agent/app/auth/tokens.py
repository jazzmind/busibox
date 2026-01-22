"""
JWT Token validation and OAuth2 Token Exchange for Agent Service.

This module provides token validation and exchange functionality for the agent service.
Uses busibox_common.auth for shared JWT utilities.

Token Exchange Strategy:
- Uses Zero Trust token exchange (subject_token mode) - NO client credentials
- The user's JWT is passed as subject_token to AuthZ
- AuthZ verifies the signature and issues a new audience-bound token
- This eliminates the need for service-specific OAuth clients

Delegation Tokens:
- For long-running tasks, use create_delegation_token() to get a long-lived token
- Delegation tokens are stored in authz DB and can be revoked
- They can be used as subject_token for subsequent token exchanges
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx

from busibox_common.auth import (
    create_jwks_client,
    parse_jwt_token,
    exchange_token_zero_trust,
)

from app.config.settings import get_settings
from app.schemas.auth import Principal, TokenExchangeResponse

settings = get_settings()
logger = logging.getLogger(__name__)

# Default delegation token TTL: 3 years in seconds
DEFAULT_DELEGATION_TTL = 94608000  # 3 years

# Cached clients (lazy initialization)
_jwks_client = None


def _get_jwks_client():
    """Get or create JWKS client."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = create_jwks_client(str(settings.auth_jwks_url))
    return _jwks_client


def _extract_scopes(claims: Dict) -> List[str]:
    """Extract scopes from JWT claims."""
    if "scope" in claims and isinstance(claims["scope"], str):
        return claims["scope"].split()
    if "scp" in claims and isinstance(claims["scp"], list):
        return [str(scope) for scope in claims["scp"]]
    return []


def _extract_roles(claims: Dict) -> List[str]:
    """
    Extract role IDs from claims.
    
    The authz service returns roles as objects: [{"id": "...", "name": "..."}]
    but the Principal model expects a list of strings (role IDs).
    """
    roles_claim = claims.get("roles", [])
    if not roles_claim:
        return []
    
    roles: List[str] = []
    for role in roles_claim:
        if isinstance(role, str):
            roles.append(role)
        elif isinstance(role, dict):
            role_id = role.get("id") or role.get("name")
            if role_id:
                roles.append(str(role_id))
    
    return roles


async def validate_bearer(token: str) -> Principal:
    """
    Validate a bearer token and return a Principal.
    """
    jwks_client = _get_jwks_client()
    audience = settings.auth_audience or "agent-api"
    
    payload = parse_jwt_token(
        token,
        jwks_client,
        audience,
        issuer=settings.auth_issuer,
    )
    
    if not payload:
        raise ValueError("Invalid or expired JWT token")
    
    return Principal(
        sub=payload.get("sub", ""),
        scopes=_extract_scopes(payload),
        roles=_extract_roles(payload),
        email=payload.get("email"),
        token=token,
    )


async def exchange_token(
    principal: Principal, scopes: List[str], purpose: str
) -> TokenExchangeResponse:
    """
    Exchange a user token for a downstream token using Zero Trust token exchange.
    
    Uses the user's JWT as subject_token - NO client credentials required.
    Tokens are audience-bound to a single downstream service.
    
    Args:
        principal: The authenticated user's principal (must include their JWT token)
        scopes: Requested scopes for the new token
        purpose: Purpose description to determine target audience
        
    Returns:
        TokenExchangeResponse with the new audience-bound token
        
    Raises:
        ValueError: If token exchange fails or principal has no token
    """
    if not principal.token:
        raise ValueError("Principal must have a token for Zero Trust exchange")
    
    audience = _audience_for_purpose(purpose, scopes)
    
    # Use Zero Trust exchange - pass user's JWT as subject_token
    access_token = await exchange_token_zero_trust(
        subject_token=principal.token,
        target_audience=audience,
        user_id=principal.sub,
        scopes=" ".join(scopes),
        authz_url=str(settings.auth_token_url),
    )
    
    if not access_token:
        raise ValueError(f"Token exchange failed for audience {audience}")
    
    # Use 3 years expiry for delegation tokens, shorter for regular exchanges
    # This aligns with the authz service's default access_token_ttl
    if "delegation" in purpose.lower() or "task" in purpose.lower():
        # Long-lived delegation tokens (3 years)
        expires_at = datetime.now(timezone.utc) + timedelta(days=365 * 3)
    else:
        # Regular short-lived tokens (15 minutes)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=900)
    
    return TokenExchangeResponse(
        access_token=access_token,
        token_type="bearer",
        expires_at=expires_at,
        scopes=scopes,
    )


def _audience_for_purpose(purpose: str, scopes: List[str]) -> str:
    """
    Infer the downstream audience from purpose and/or scopes.

    We keep this mapping local to agent-server so we don't have to change its DB schema
    (TokenGrant is keyed on scopes), while still ensuring tokens are audience-bound.
    """
    p = (purpose or "").lower()
    if "ingest" in p:
        return "ingest-api"
    if "search" in p or "rag" in p:
        return "search-api"
    # fallback: infer by scope prefix
    for s in scopes:
        if s.startswith("ingest."):
            return "ingest-api"
        if s.startswith("search."):
            return "search-api"
    return "agent-api"


async def get_service_token(user_token: str, user_id: str, target_audience: str) -> str:
    """
    Get a token for calling a downstream service on behalf of a user.
    
    Uses Zero Trust token exchange - passes the user's JWT as subject_token.
    NO client credentials are used.
    
    Args:
        user_token: The user's current JWT token
        user_id: The user ID (for logging/caching)
        target_audience: The target service audience (e.g., "ingest-api")
        
    Returns:
        Bearer token string (without "Bearer " prefix)
        
    Raises:
        ValueError: If token exchange fails
    """
    access_token = await exchange_token_zero_trust(
        subject_token=user_token,
        target_audience=target_audience,
        user_id=user_id,
        scopes="read write",
        authz_url=str(settings.auth_token_url),
    )
    
    if not access_token:
        raise ValueError(f"Token exchange failed for audience {target_audience}")
    
    return access_token


async def create_delegation_token(
    subject_token: str,
    name: str,
    scopes: List[str],
    expires_in_seconds: int = DEFAULT_DELEGATION_TTL,
) -> TokenExchangeResponse:
    """
    Create a long-lived delegation token for background tasks.
    
    This calls the authz /oauth/delegation endpoint to create a delegation token
    that can be used for token exchange even after the original session expires.
    
    The delegation token:
    - Is stored in authz DB and can be revoked
    - Has a configurable TTL (default 3 years)
    - Can be used as subject_token for subsequent token exchanges
    - Preserves the user's identity and scopes
    
    Args:
        subject_token: The user's current session JWT (to authorize the delegation)
        name: Human-readable name for the delegation (e.g., "Task: Daily Report")
        scopes: Scopes to delegate (must be subset of user's scopes)
        expires_in_seconds: TTL in seconds (default 3 years, max 3 years)
        
    Returns:
        TokenExchangeResponse with the delegation token and expiry
        
    Raises:
        ValueError: If delegation creation fails
    """
    # Build authz delegation URL (same host as token URL, different path)
    token_url = str(settings.auth_token_url)
    delegation_url = token_url.replace("/oauth/token", "/oauth/delegation")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                delegation_url,
                json={
                    "subject_token": subject_token,
                    "name": name,
                    "scopes": scopes,
                    "expires_in_seconds": min(expires_in_seconds, DEFAULT_DELEGATION_TTL),
                },
            )
            
            if response.status_code != 200:
                error_detail = response.text[:200]
                logger.error(
                    "Delegation token creation failed",
                    status_code=response.status_code,
                    response=error_detail,
                )
                raise ValueError(f"Delegation token creation failed: {error_detail}")
            
            data = response.json()
            delegation_token = data.get("delegation_token")
            expires_at_str = data.get("expires_at")
            
            if not delegation_token:
                raise ValueError("No delegation_token in response")
            
            # Parse expires_at from ISO format
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            
            logger.info(
                "Delegation token created",
                name=name,
                jti=data.get("jti"),
                expires_in=data.get("expires_in"),
                scopes=scopes,
            )
            
            return TokenExchangeResponse(
                access_token=delegation_token,
                token_type="bearer",
                expires_at=expires_at,
                scopes=scopes,
            )
            
    except httpx.RequestError as e:
        logger.error("Delegation token request error", error=str(e))
        raise ValueError(f"Delegation token request failed: {e}") from e
