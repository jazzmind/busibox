"""
FastAPI application for Busibox Ingestion Service.

This API provides endpoints for:
- File upload with chunked streaming
- Real-time status tracking via SSE
- File metadata retrieval and deletion
- Health checks

The API is internal-only and deployed to ingest-lxc container.
"""

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware.auth import AuthMiddleware
from api.middleware.logging import LoggingMiddleware
from api.routes import files, health, status, upload

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

# Create FastAPI application
app = FastAPI(
    title="Busibox Ingestion Service API",
    description="Internal API for document upload, processing, and status tracking",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Add CORS middleware (for internal network access)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Internal network only - no external access
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add custom middleware
app.add_middleware(LoggingMiddleware)
app.add_middleware(AuthMiddleware)

# Include routers
app.include_router(upload.router, prefix="/upload", tags=["Upload"])
app.include_router(status.router, prefix="/status", tags=["Status"])
app.include_router(files.router, prefix="/files", tags=["Files"])
app.include_router(health.router, prefix="/health", tags=["Health"])


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    logger.info("Ingestion API starting up")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("Ingestion API shutting down")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "busibox-ingestion-api",
        "version": "1.0.0",
        "status": "running"
    }

