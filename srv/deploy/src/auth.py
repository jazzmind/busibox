"""
Deployment Service Authentication

Uses busibox_common for JWT validation and admin role checking.

Bootstrap Mode:
During initial system bootstrap (before any users exist), we need to deploy
core apps. This is enabled by setting DEPLOY_BOOTSTRAP_TOKEN in the environment.
When this is set, requests with "Bearer bootstrap" will be accepted as admin.
This should only be used during initial Ansible provisioning.
"""

import os
import logging
from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# Import busibox_common auth utilities
from busibox_common.auth import (
    parse_jwt_token,
    extract_user_context,
    create_jwks_client,
)

from .config import config

logger = logging.getLogger(__name__)

security = HTTPBearer()

# Create JWKS client for JWT validation
jwks_client = None

# Bootstrap token for initial setup (from environment)
BOOTSTRAP_TOKEN = os.getenv("DEPLOY_BOOTSTRAP_TOKEN", "")

def get_jwks_client():
    """Lazy-load JWKS client."""
    global jwks_client
    if jwks_client is None:
        jwks_url = f"{config.authz_url}/.well-known/jwks.json"
        jwks_client = create_jwks_client(jwks_url)
    return jwks_client


async def verify_admin_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Verify that the request has a valid admin token.
    
    Supports two modes:
    1. Bootstrap mode: If DEPLOY_BOOTSTRAP_TOKEN env var is set and the token matches,
       allow access without JWT validation. Used during initial Ansible provisioning.
    2. Normal mode: Uses busibox_common to parse and validate JWT, then checks for Admin role.
    
    Returns the validated token payload with user context.
    Raises HTTPException if invalid or not admin.
    """
    token = credentials.credentials
    
    # Check for bootstrap mode first
    if BOOTSTRAP_TOKEN and token == BOOTSTRAP_TOKEN:
        logger.info("[AUTH] Bootstrap token accepted for deployment")
        return {
            "user_id": "bootstrap",
            "email": "bootstrap@system",
            "roles": [{"id": "bootstrap", "name": "Admin"}],
            "scopes": ["deploy:*"],
        }
    
    try:
        # Parse and validate JWT using busibox_common
        # Use "deploy-api" as expected audience
        logger.info(f"[AUTH] Validating token with audience=deploy-api, issuer=busibox-authz")
        
        payload = parse_jwt_token(
            token=token,
            jwks_client=get_jwks_client(),
            issuer="busibox-authz",
            audience="deploy-api",
        )
        
        if not payload:
            logger.error("[AUTH] Token validation returned None - token invalid or expired")
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        
        logger.info(f"[AUTH] Token validated successfully, extracting user context")
        
        # Extract user context
        user_context = extract_user_context(
            payload=payload,
            auth_header=f"Bearer {token}",
            token=token
        )
        
        # Check for Admin role using UserContext.role_names property
        if "Admin" not in user_context.role_names:
            logger.warning(f"[AUTH] Non-admin user {user_context.user_id} attempted deployment operation. Roles: {user_context.role_names}")
            raise HTTPException(
                status_code=403,
                detail="Admin role required for deployment operations"
            )
        
        logger.info(f"[AUTH] Admin user {user_context.user_id} authenticated for deployment")
        
        # Return validated user info (don't spread payload to avoid conflicts)
        return {
            "user_id": user_context.user_id,
            "email": user_context.email,
            "roles": [{"id": r.id, "name": r.name} for r in user_context.roles],
            "scopes": list(user_context.scopes),
        }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AUTH] Token validation error: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


def verify_token(token: str) -> dict:
    """
    Verify a JWT token synchronously (for WebSocket authentication).
    
    Uses busibox_common for parsing. Returns the validated token payload.
    Raises HTTPException if invalid.
    """
    try:
        # Parse JWT using busibox_common
        payload = parse_jwt_token(
            token=token,
            jwks_client=get_jwks_client(),
            issuer="busibox-authz",
            audience="deploy-api",
        )
        
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        
        # Extract basic user context
        user_context = extract_user_context(payload, token=token)
        
        return {
            'user_id': user_context.user_id,
            'email': user_context.email,
            'roles': [{"id": r.id, "name": r.name} for r in user_context.roles],
            'scopes': list(user_context.scopes),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AUTH] Token verification failed: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
