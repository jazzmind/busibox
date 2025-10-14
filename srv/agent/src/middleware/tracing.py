"""
Tracing Middleware for Request Tracking

Generates and propagates trace IDs for distributed tracing:
- Generates UUID trace ID per request
- Adds trace ID to request state
- Adds trace ID to response headers
- Enables end-to-end request tracking
"""

import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class TracingMiddleware(BaseHTTPMiddleware):
    """Middleware for trace ID generation and propagation."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Generate or extract trace ID and add to context."""
        # Check for existing trace ID in request headers
        trace_id = request.headers.get("X-Trace-ID")
        
        # Generate new trace ID if not present
        if not trace_id:
            trace_id = str(uuid.uuid4())
        
        # Store in request state for access by other middleware/routes
        request.state.trace_id = trace_id
        
        # Process request
        response = await call_next(request)
        
        # Add trace ID to response headers
        response.headers["X-Trace-ID"] = trace_id
        
        return response

