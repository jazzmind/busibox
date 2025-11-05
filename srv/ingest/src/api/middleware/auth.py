"""
Authentication middleware for validating user context.

The API expects user context in headers (X-User-Id) from apps-lxc.
This middleware validates the user ID format and attaches it to request state.
"""

import uuid
from typing import Callable

import structlog
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger()


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to validate user context from headers."""
    
    async def dispatch(self, request: Request, call_next: Callable):
        """Process request and validate user context."""
        # Skip auth for health endpoints
        if request.url.path.startswith("/health") or request.url.path == "/":
            return await call_next(request)
        
        # Extract user ID from header
        user_id_header = request.headers.get("X-User-Id")
        
        if not user_id_header:
            logger.warning(
                "Missing user ID header",
                path=request.url.path,
                method=request.method,
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "Missing X-User-Id header"}
            )
        
        # Validate UUID format
        try:
            user_id = uuid.UUID(user_id_header)
        except ValueError:
            logger.warning(
                "Invalid user ID format",
                path=request.url.path,
                user_id_header=user_id_header,
            )
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "Invalid X-User-Id format (must be UUID)"}
            )
        
        # Attach user ID to request state
        request.state.user_id = str(user_id)
        
        # Process request
        response = await call_next(request)
        return response

