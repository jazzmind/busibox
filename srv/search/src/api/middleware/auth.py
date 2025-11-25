"""
Authentication middleware for Search API.

Supports both:
- JWT passthrough (Authorization: Bearer <token>) - preferred
- Legacy X-User-Id header - for backwards compatibility

When JWT is present, it's stored in request state for passthrough to downstream services.
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
        
        # Check for Authorization header (JWT passthrough)
        auth_header = request.headers.get("authorization")
        user_id = request.headers.get("x-user-id")
        
        # Store authorization header for passthrough to downstream services
        request.state.authorization = auth_header
        
        if not user_id and not auth_header:
            logger.warning(
                "Request missing authentication",
                path=request.url.path,
            )
            raise HTTPException(
                status_code=401,
                detail="User not authenticated - missing X-User-Id or Authorization header"
            )
        
        # Attach user_id to request state (from header or will be extracted by downstream)
        request.state.user_id = user_id
        
        logger.debug(
            "Request authenticated",
            user_id=user_id,
            has_jwt=bool(auth_header),
            path=request.url.path,
        )
        
        response = await call_next(request)
        return response

