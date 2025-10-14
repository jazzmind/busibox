#!/usr/bin/env python3
"""
Busibox Agent API Server

FastAPI-based service providing:
- Authentication and authorization (JWT + RBAC)
- File upload/download operations
- Semantic search via vector database
- AI agent operations (RAG)
- Webhook handling for file processing events
"""

import os
import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from middleware.auth import AuthMiddleware
from middleware.logging import LoggingMiddleware
from middleware.tracing import TracingMiddleware
from routes import auth, files, search, agent, webhooks, health

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting Busibox Agent API", version=app.version)
    
    # TODO: Initialize database connection pool
    # TODO: Initialize Redis connection
    # TODO: Initialize Milvus connection
    # TODO: Verify MinIO connectivity
    
    yield
    
    # Shutdown
    logger.info("Shutting down Busibox Agent API")
    
    # TODO: Close database connections
    # TODO: Close Redis connections
    # TODO: Close Milvus connections


# Create FastAPI application
app = FastAPI(
    title="Busibox Agent API",
    description="Local LLM infrastructure platform API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware (configure for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom middleware (order matters - bottom executes first)
app.add_middleware(LoggingMiddleware)
app.add_middleware(TracingMiddleware)
# Note: AuthMiddleware is applied per-route, not globally

# Register routers
app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(files.router, prefix="/files", tags=["files"])
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(agent.router, prefix="/agent", tags=["agent"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])


@app.get("/")
async def root():
    """Root endpoint - API information."""
    return {
        "service": "Busibox Agent API",
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
    }


if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=os.getenv("API_RELOAD", "false").lower() == "true",
        log_config=None,  # Use structlog instead
    )

