"""
Logging middleware for request/response logging.

Logs all requests with structured logging including:
- Request method, path, user_id
- Response status code
- Processing time
- Error details (if any)
"""

import time
from typing import Callable

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger()


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all requests and responses."""
    
    async def dispatch(self, request: Request, call_next: Callable):
        """Process request and log details."""
        start_time = time.time()
        
        # Extract user ID if available
        user_id = getattr(request.state, "user_id", None)
        
        # Log request
        logger.info(
            "Request started",
            method=request.method,
            path=request.url.path,
            user_id=user_id,
            client_ip=request.client.host if request.client else None,
        )
        
        # Process request
        try:
            response = await call_next(request)
            processing_time = time.time() - start_time
            
            # Log response
            logger.info(
                "Request completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                processing_time_ms=round(processing_time * 1000, 2),
                user_id=user_id,
            )
            
            return response
        except Exception as e:
            processing_time = time.time() - start_time
            
            # Log error
            logger.error(
                "Request failed",
                method=request.method,
                path=request.url.path,
                error=str(e),
                processing_time_ms=round(processing_time * 1000, 2),
                user_id=user_id,
                exc_info=True,
            )
            raise

