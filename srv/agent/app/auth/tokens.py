"""
JWT Token validation and OAuth2 Token Exchange for Agent Service.

This module provides token validation and exchange functionality for the agent service.
Uses busibox_common.auth for shared JWT utilities.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from busibox_common.auth import (
    create_jwks_client,
    parse_jwt_token,
    TokenExchangeClient,
)

from app.config.settings import get_settings
from app.schemas.auth import Principal, TokenExchangeResponse

settings = get_settings()
logger = logging.getLogger(__name__)

# Cached clients (lazy initialization)
_jwks_client = None
_token_exchange_client = None


def _get_jwks_client():
    """Get or create JWKS client."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = create_jwks_client(str(settings.auth_jwks_url))
    return _jwks_client


def _get_token_exchange_client():
    """Get or create token exchange client."""
    global _token_exchange_client
    if _token_exchange_client is None:
        _token_exchange_client = TokenExchangeClient(
            token_url=str(settings.auth_token_url),
            client_id=settings.auth_client_id,
            client_secret=settings.auth_client_secret,
        )
    return _token_exchange_client


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
    Exchange a user token for a downstream token using OAuth2 token exchange (RFC 8693 style).
    Tokens are audience-bound to a single downstream service.
    """
    client = _get_token_exchange_client()
    audience = _audience_for_purpose(purpose, scopes)
    
    access_token = await client.get_token_for_service(
        user_id=principal.sub,
        target_audience=audience,
        scope=" ".join(scopes),
    )
    
    if not access_token:
        raise ValueError(f"Token exchange failed for audience {audience}")
    
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=840)
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
