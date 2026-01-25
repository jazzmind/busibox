"""
Deployment Service Authentication

Validates admin tokens by calling authz service.
"""

from jose import jwt, JWTError
import httpx
import logging
from typing import List
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .config import config

logger = logging.getLogger(__name__)

security = HTTPBearer()


def _has_scope(scopes: List[str], required_scope: str) -> bool:
    """
    Check if any of the provided scopes match the required scope.
    
    Supports wildcard matching (e.g., "busibox-admin.*" matches "busibox-admin.read").
    """
    for scope in scopes:
        if scope == required_scope:
            return True
        # Check wildcard match (e.g., "busibox-admin.*")
        if scope.endswith(".*"):
            prefix = scope[:-2]  # Remove ".*"
            if required_scope.startswith(prefix):
                return True
            # Also match if required scope is just the prefix
            if required_scope == prefix:
                return True
        # Check if scope is broader (e.g., scope is "busibox-admin" and required is "busibox-admin.read")
        if required_scope.startswith(scope + "."):
            return True
    return False


def _get_user_scopes(token_payload: dict) -> List[str]:
    """
    Extract scopes from token payload.
    
    Looks for scopes in roles (role.scopes) and directly on the token.
    """
    scopes = []
    
    # Get scopes from roles
    roles = token_payload.get('roles', [])
    if isinstance(roles, list):
        for role in roles:
            if isinstance(role, dict):
                role_scopes = role.get('scopes', [])
                if isinstance(role_scopes, list):
                    scopes.extend(role_scopes)
    
    # Get direct scopes from token
    token_scopes = token_payload.get('scopes', [])
    if isinstance(token_scopes, list):
        scopes.extend(token_scopes)
    
    return scopes


async def verify_admin_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Verify that the request has a valid admin token.
    
    The token is a session JWT from AI Portal. We:
    1. Decode the JWT to extract user info and roles (roles are embedded in the token)
    2. Verify the signature via authz JWKS (optional - for now we trust ai-portal)
    3. Check if the user has Admin role
    
    Returns the validated token payload.
    Raises HTTPException if invalid or not admin.
    """
    token = credentials.credentials
    
    try:
        # Decode JWT without verification first to check claims
        # (In production, we should verify signature via JWKS)
        try:
            unverified = jwt.get_unverified_claims(token)
        except JWTError as e:
            logger.error(f"Failed to decode JWT: {e}")
            raise HTTPException(status_code=401, detail="Invalid token format")
        
        user_id = unverified.get('sub')
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token: missing user ID")
        
        # Check if token has roles embedded (session JWTs from ai-portal have this)
        roles = unverified.get('roles', [])
        
        # Check for Admin role
        is_admin = any(
            r.get('name') == 'Admin' for r in roles
        ) if isinstance(roles, list) else False
        
        if not is_admin:
            # Fallback: try to fetch roles from authz service
            logger.info(f"No Admin role in token, checking authz service for user {user_id}")
            async with httpx.AsyncClient() as client:
                roles_response = await client.get(
                    f"{config.authz_url}/api/v1/users/{user_id}/roles",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10.0
                )
                
                if roles_response.status_code == 200:
                    roles = roles_response.json()
                    is_admin = any(
                        r.get('name') == 'Admin' or r.get('role', {}).get('name') == 'Admin'
                        for r in roles
                    )
                else:
                    logger.warning(f"Failed to get user roles from authz: {roles_response.status_code}")
        
        if not is_admin:
            logger.warning(f"Non-admin user {user_id} attempted deployment operation")
            raise HTTPException(
                status_code=403,
                detail="Admin role required for deployment operations"
            )
        
        logger.info(f"Admin user {user_id} authenticated for deployment")
        return {"user_id": user_id, "roles": roles, **unverified}
            
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to authz service: {e}")
        raise HTTPException(
            status_code=503,
            detail="Authentication service unavailable"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token validation error: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


async def verify_busibox_admin_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Verify that the request has a valid token with busibox-admin scope.
    
    This is specifically for system management operations that require
    the busibox-admin.* scope (installation, service management, etc.).
    
    Returns the validated token payload with user_id, roles, and scopes.
    Raises HTTPException if invalid or missing required scope.
    """
    token = credentials.credentials
    
    try:
        # Decode JWT without verification first to check claims
        try:
            unverified = jwt.get_unverified_claims(token)
        except JWTError as e:
            logger.error(f"Failed to decode JWT: {e}")
            raise HTTPException(status_code=401, detail="Invalid token format")
        
        user_id = unverified.get('sub')
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token: missing user ID")
        
        # Get roles from token
        roles = unverified.get('roles', [])
        
        # Check for Admin role (admins have implicit busibox-admin access)
        is_admin = any(
            r.get('name') == 'Admin' for r in roles
        ) if isinstance(roles, list) else False
        
        # Get all scopes from roles
        scopes = _get_user_scopes(unverified)
        
        # Check for busibox-admin scope
        has_busibox_admin = _has_scope(scopes, "busibox-admin") or is_admin
        
        if not has_busibox_admin:
            # Fallback: try to fetch roles from authz service
            logger.info(f"No busibox-admin scope in token, checking authz for user {user_id}")
            async with httpx.AsyncClient() as client:
                roles_response = await client.get(
                    f"{config.authz_url}/api/v1/users/{user_id}/roles",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10.0
                )
                
                if roles_response.status_code == 200:
                    fetched_roles = roles_response.json()
                    # Check for Admin role
                    is_admin = any(
                        r.get('name') == 'Admin' or r.get('role', {}).get('name') == 'Admin'
                        for r in fetched_roles
                    )
                    # Extract scopes from fetched roles
                    for r in fetched_roles:
                        role_data = r.get('role', r)
                        if isinstance(role_data, dict):
                            role_scopes = role_data.get('scopes', [])
                            if isinstance(role_scopes, list):
                                scopes.extend(role_scopes)
                    
                    has_busibox_admin = _has_scope(scopes, "busibox-admin") or is_admin
                else:
                    logger.warning(f"Failed to get user roles from authz: {roles_response.status_code}")
        
        if not has_busibox_admin:
            logger.warning(f"User {user_id} lacks busibox-admin scope for system operation")
            raise HTTPException(
                status_code=403,
                detail="busibox-admin scope required for system management operations"
            )
        
        logger.info(f"User {user_id} authenticated for system management (admin={is_admin})")
        return {
            "user_id": user_id,
            "roles": roles,
            "scopes": scopes,
            "is_admin": is_admin,
            **unverified
        }
            
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to authz service: {e}")
        raise HTTPException(
            status_code=503,
            detail="Authentication service unavailable"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token validation error: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
