"""
Authentication middleware for Search API.
"""

import structlog
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger()


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to extract and validate user authentication."""
    
    async def dispatch(self, request: Request, call_next):
        """Process request and extract user_id."""
        
        # Skip auth for health endpoint
        if request.url.path == "/health":
            return await call_next(request)
        
        # Get user ID from X-User-Id header (set by upstream API gateway)
        user_id = request.headers.get("x-user-id")
        
        if not user_id:
            logger.warning(
                "Request missing user ID",
                path=request.url.path,
                headers=dict(request.headers),
            )
            raise HTTPException(
                status_code=401,
                detail="User not authenticated - missing X-User-Id header"
            )
        
        # Attach user_id to request state
        request.state.user_id = user_id
        
        logger.debug(
            "Request authenticated",
            user_id=user_id,
            path=request.url.path,
        )
        
        response = await call_next(request)
        return response

