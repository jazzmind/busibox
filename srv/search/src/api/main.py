"""
Main FastAPI application for Search Service.
"""

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware.auth import AuthMiddleware
from api.middleware.logging import LoggingMiddleware
from api.routes import search, health
from shared.config import config

# Configure structured logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()

# Create FastAPI app
app = FastAPI(
    title="Busibox Search API",
    description="""
    Sophisticated search API for Busibox platform.
    
    Features:
    - **Keyword search**: Fast BM25 full-text search
    - **Semantic search**: Dense vector similarity search
    - **Hybrid search**: Combined keyword + semantic (recommended)
    - **Reranking**: Cross-encoder reranking for improved accuracy
    - **Highlighting**: Search term highlighting in results
    - **Semantic alignment**: Visualize query-document similarity
    - **MMR**: Maximal Marginal Relevance for diverse results
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure based on your needs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add custom middleware
app.add_middleware(LoggingMiddleware)
app.add_middleware(AuthMiddleware)

# Include routers
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(health.router, prefix="/health", tags=["health"])


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    logger.info(
        "Starting Search API",
        service_name=config.service_name,
        port=config.service_port,
        milvus_host=config.milvus_host,
        milvus_collection=config.milvus_collection,
    )


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("Shutting down Search API")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Busibox Search API",
        "version": "1.0.0",
        "status": "operational",
        "docs": "/docs",
        "health": "/health",
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=config.service_port,
        log_level=config.log_level.lower(),
        reload=False,
    )

