"""
Logging Middleware for Structured Logging

Implements structured JSON logging for all API requests with:
- Request/response logging
- Timing information
- Trace ID correlation
- Contextual information (user, endpoint, method)
"""

import time
from typing import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Message

logger = structlog.get_logger()


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for structured request/response logging."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log request and response with timing."""
        start_time = time.time()
        
        # Get trace ID (set by TracingMiddleware)
        trace_id = request.state.trace_id if hasattr(request.state, "trace_id") else "unknown"
        
        # Get user ID (set by AuthMiddleware)
        user_id = request.state.user_id if hasattr(request.state, "user_id") else None
        
        # Log request
        logger.info(
            "request_started",
            trace_id=trace_id,
            user_id=user_id,
            method=request.method,
            path=request.url.path,
            query_params=dict(request.query_params),
            client_ip=request.client.host if request.client else None,
        )
        
        try:
            # Process request
            response = await call_next(request)
            
            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000
            
            # Log response
            logger.info(
                "request_completed",
                trace_id=trace_id,
                user_id=user_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )
            
            return response
            
        except Exception as e:
            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000
            
            # Log error
            logger.error(
                "request_failed",
                trace_id=trace_id,
                user_id=user_id,
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
                error=str(e),
                exc_info=True,
            )
            
            raise

