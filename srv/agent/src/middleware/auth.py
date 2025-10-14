"""
Authentication Middleware

Handles JWT token validation and user context:
- Validates JWT tokens from Authorization header
- Extracts user ID and roles
- Stores in request state for RBAC checks
- Returns 401 for invalid/missing tokens
"""

from typing import Callable

from fastapi import Request, Response, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware for JWT authentication (stub - to be implemented)."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Validate JWT token and extract user context."""
        # TODO: Implement JWT validation
        # 1. Extract token from Authorization header
        # 2. Verify token signature
        # 3. Check expiration
        # 4. Extract user_id and roles
        # 5. Store in request.state
        
        # Stub implementation - set placeholder values
        request.state.user_id = None
        request.state.roles = []
        
        # Process request
        response = await call_next(request)
        
        return response


def require_auth(request: Request):
    """
    Dependency function to require authentication.
    
    Usage in routes:
        @router.get("/protected")
        async def protected_route(request: Request, _: None = Depends(require_auth)):
            user_id = request.state.user_id
            ...
    """
    if not hasattr(request.state, "user_id") or request.state.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return None


def require_permission(permission: str):
    """
    Dependency function to require specific permission.
    
    Args:
        permission: Permission string (e.g., "file.upload", "admin.manage_users")
    
    Usage in routes:
        @router.delete("/files/{file_id}")
        async def delete_file(
            file_id: str,
            request: Request,
            _: None = Depends(require_permission("file.delete"))
        ):
            ...
    """
    def check_permission(request: Request):
        # Ensure user is authenticated
        require_auth(request)
        
        # TODO: Implement permission checking
        # 1. Get user roles from request.state
        # 2. Check if any role has the required permission
        # 3. Raise HTTPException if not authorized
        
        # Stub implementation - allow all for now
        return None
    
    return check_permission

