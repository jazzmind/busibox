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
from api.routes import files, health, search, status, upload

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
    description="""
## Document Ingestion & Processing API

This API provides endpoints for uploading, processing, and tracking documents through
the Busibox ingestion pipeline.

### Features

- **File Upload**: Chunked streaming upload with metadata
- **Real-time Status**: Server-Sent Events (SSE) for processing updates
- **Hybrid Search**: Dense semantic + sparse BM25 + visual ColPali embeddings
- **Content Deduplication**: Automatic detection and vector reuse
- **Health Monitoring**: Service health checks

### Pipeline Stages

1. **Upload** → File stored in MinIO
2. **Parsing** → Text extraction (Marker, TATR, OCR)
3. **Classification** → Document type and language detection
4. **Chunking** → Semantic text chunking (400-800 tokens)
5. **Embedding** → Dense (text-embedding-3-small) + BM25 + ColPali
6. **Indexing** → Store in Milvus vector database

### Authentication

All endpoints require `X-User-Id` header for user identification and access control.

### Rate Limits

- Upload: 100 MB max file size
- Concurrent uploads: 10 per user
- Status polling: 1 request/second recommended

### Support

For issues or questions, contact the Busibox infrastructure team.
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {
            "name": "Upload",
            "description": "File upload with chunked streaming and metadata",
        },
        {
            "name": "Search",
            "description": "Semantic document search with hybrid retrieval",
        },
        {
            "name": "Status",
            "description": "Real-time processing status via SSE and polling",
        },
        {
            "name": "Files",
            "description": "File metadata retrieval and deletion",
        },
        {
            "name": "Health",
            "description": "Service health checks and diagnostics",
        },
    ],
    contact={
        "name": "Busibox Infrastructure Team",
        "email": "infra@busibox.local",
    },
    license_info={
        "name": "Internal Use Only",
    },
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
app.include_router(search.router, prefix="/search", tags=["Search"])
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
    """
    Root endpoint - API information.
    
    Returns basic API information and links to documentation.
    """
    return {
        "service": "busibox-ingestion-api",
        "version": "1.0.0",
        "status": "running",
        "documentation": {
            "swagger_ui": "/docs",
            "redoc": "/redoc",
            "openapi_json": "/openapi.json",
        },
        "endpoints": {
            "upload": "/upload",
            "search": "/search",
            "status": "/status/{file_id}",
            "files": "/files/{file_id}",
            "health": "/health",
        }
    }

