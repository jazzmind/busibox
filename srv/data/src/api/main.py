"""
FastAPI application for Busibox Data Service.

This API provides endpoints for:
- File upload with chunked streaming
- Real-time status tracking via SSE
- File metadata retrieval and deletion
- Health checks

The API is internal-only and deployed to data-lxc container.
"""

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware.jwt_auth import JWTAuthMiddleware
from api.middleware.logging import LoggingMiddleware
from api.routes import content, data, embeddings, extract, files, graph, health, libraries, markdown, roles, status, upload, authz, test_docs
from api.services.redis_service import RedisService
from api.services.postgres import PostgresService
from shared.config import Config

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

# Global service instances (singletons)
config = Config().to_dict()
pg_service = PostgresService(config)
redis_service = RedisService(config)

# Create FastAPI application
app = FastAPI(
    title="Busibox Data Service API",
    description="""
## Data Management API (formerly Data Service)

This API provides endpoints for managing both unstructured documents (files) and
structured data (like Notion/Coda databases). It handles file uploads, processing,
and tracking, as well as structured data document operations.

### Features

- **File Upload**: Chunked streaming upload with metadata
- **Real-time Status**: Server-Sent Events (SSE) for processing updates
- **Hybrid Search**: Dense semantic + sparse BM25 + visual ColPali embeddings
- **Content Deduplication**: Automatic detection and vector reuse
- **Structured Data**: Create and query data documents with SQL-like queries
- **Redis Caching**: High-performance caching for frequently accessed data
- **Health Monitoring**: Service health checks

### Pipeline Stages

1. **Upload** → File stored in MinIO
2. **Parsing** → Text extraction (Marker, TATR, OCR)
3. **Classification** → Document type and language detection
4. **Chunking** → Semantic text chunking (400-800 tokens)
5. **Embedding** → Dense (FastEmbed bge-large 1024-d) + BM25 + ColPali pooled
6. **Indexing** → Store in Milvus vector database

### Authentication

All endpoints require authentication via:
- `Authorization: Bearer <JWT>` header (required) - JWT with user identity and role permissions

JWT tokens contain user identity and document role memberships with CRUD permissions,
enabling Row-Level Security (RLS) enforcement in the database.

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
            "description": "File upload with chunked streaming, metadata, and role assignment",
        },
        {
            "name": "Search",
            "description": "Semantic document search with hybrid retrieval",
        },
        {
            "name": "Embeddings",
            "description": "Text embedding generation with FastEmbed",
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
            "name": "Roles",
            "description": "Document role management (add/remove roles, share documents)",
        },
        {
            "name": "Health",
            "description": "Service health checks and diagnostics",
        },
        {
            "name": "Libraries",
            "description": "Library management - create, list, update, delete document libraries",
        },
        {
            "name": "Data",
            "description": "Structured data documents - create, query, and manage data like Notion/Coda databases",
        },
        {
            "name": "Graph",
            "description": "Knowledge graph visualization and exploration - entities, relationships, and knowledge maps",
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
app.add_middleware(JWTAuthMiddleware)

# Include routers
app.include_router(upload.router, prefix="/upload", tags=["Upload"])
app.include_router(content.router, prefix="/data", tags=["Content Data"])
app.include_router(embeddings.router, prefix="/api", tags=["Embeddings"])
app.include_router(status.router, prefix="/status", tags=["Status"])
app.include_router(files.router, prefix="/files", tags=["Files"])
app.include_router(markdown.router, prefix="/files", tags=["Markdown"])
app.include_router(roles.router, prefix="/files", tags=["Roles"])
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(extract.router, tags=["Extract"])  # Remote Marker extraction
app.include_router(authz.router, prefix="/authz", tags=["Authz"])
app.include_router(libraries.router, prefix="/libraries", tags=["Libraries"])
app.include_router(graph.router, prefix="/data/graph", tags=["Graph"])  # Must be before data router (has /{id} catch-all)
app.include_router(data.router, prefix="/data", tags=["Data"])
app.include_router(test_docs.router, tags=["Test Docs"])


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    logger.info("Data API starting up")
    await pg_service.connect()
    
    # Connect Redis for data document caching
    try:
        await redis_service.connect()
        logger.info("Redis service connected for data caching")
    except Exception as e:
        logger.warning("Redis connection failed (caching disabled)", error=str(e))
    
    # Connect Neo4j for graph database (optional)
    try:
        from services.graph_service import get_graph_service
        graph_service = await get_graph_service()
        app.state.graph_service = graph_service
        if graph_service.available:
            logger.info("Neo4j graph service connected")
        else:
            logger.info("Neo4j graph service not available (graph features disabled)")
    except Exception as e:
        logger.warning("Neo4j connection failed (graph features disabled)", error=str(e))
        app.state.graph_service = None
    
    # Run library migration from AI Portal if configured
    from api.services.library_migration import run_migration_if_needed
    await run_migration_if_needed(pg_service.pool)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("Data API shutting down")
    await redis_service.disconnect()
    await pg_service.disconnect()
    
    # Disconnect Neo4j
    if hasattr(app.state, "graph_service") and app.state.graph_service:
        await app.state.graph_service.disconnect()


@app.get("/")
async def root():
    """
    Root endpoint - API information.
    
    Returns basic API information and links to documentation.
    """
    return {
        "service": "busibox-data-api",
        "version": "2.0.0",
        "status": "running",
        "documentation": {
            "swagger_ui": "/docs",
            "redoc": "/redoc",
            "openapi_json": "/openapi.json",
        },
        "endpoints": {
            "files": {
                "upload": "/upload",
                "status": "/status/{file_id}",
                "metadata": "/files/{file_id}",
                "libraries": "/libraries",
            },
            "data": {
                "documents": "/data",
                "records": "/data/{document_id}/records",
                "query": "/data/{document_id}/query",
            },
            "health": "/health",
        }
    }

