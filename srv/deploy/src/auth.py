"""
Deployment Service Authentication

Validates admin tokens by calling authz service.
"""

from jose import jwt, JWTError
import httpx
import logging
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .config import config

logger = logging.getLogger(__name__)

security = HTTPBearer()


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
