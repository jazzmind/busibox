"""
Main FastAPI application for Search Service.
"""

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware.jwt_auth import JWTAuthMiddleware
from api.middleware.logging import LoggingMiddleware
from api.routes import search, health, web_search, insights, graph_search
from busibox_common import AsyncPGPoolManager
from services.insights_service import InsightsService
from shared.config import config

# Configure structured logging
import sys
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Global service instances (singletons)
# Use shared AsyncPGPoolManager for connection pooling
pg_pool = AsyncPGPoolManager.from_config(config.to_dict())
insights_service = InsightsService(config.to_dict())

# Create FastAPI app
app = FastAPI(
    title="Busibox Search API",
    description="""
    Sophisticated search API for Busibox platform.
    
    **Document Search Features:**
    - **Keyword search**: Fast BM25 full-text search
    - **Semantic search**: Dense vector similarity search
    - **Hybrid search**: Combined keyword + semantic (recommended)
    - **Reranking**: Cross-encoder reranking for improved accuracy
    - **Highlighting**: Search term highlighting in results
    - **Semantic alignment**: Visualize query-document similarity
    - **MMR**: Maximal Marginal Relevance for diverse results
    
    **Web Search Features:**
    - **Multi-provider support**: Tavily, DuckDuckGo, SerpAPI, Perplexity, Bing
    - **Centralized API key management**: Store keys securely in database
    - **Provider switching**: Change providers without app redeployment
    - **Admin endpoints**: Configure providers via API
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
app.add_middleware(JWTAuthMiddleware)

# Store services in app state for access in routes
app.state.pg_pool = pg_pool
app.state.insights_service = insights_service

# Include routers
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(graph_search.router, prefix="/search/graph", tags=["graph-search"])
app.include_router(web_search.router, prefix="/web-search", tags=["web-search"])
app.include_router(insights.router, prefix="/insights", tags=["insights"])
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
    await pg_pool.connect()
    
    # Connect Neo4j for graph-enhanced search (optional)
    try:
        from services.graph_search import GraphSearchService
        graph_search_service = GraphSearchService(config.to_dict())
        connected = await graph_search_service.connect()
        app.state.graph_search_service = graph_search_service
        if connected:
            logger.info("Neo4j graph search service connected")
        else:
            logger.info("Neo4j graph search not available (graph features disabled)")
    except Exception as e:
        logger.warning("Neo4j graph search init failed", error=str(e))
        app.state.graph_search_service = None


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("Shutting down Search API")
    await pg_pool.disconnect()
    
    # Disconnect Neo4j
    graph_svc = getattr(app.state, "graph_search_service", None)
    if graph_svc:
        await graph_svc.disconnect()


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

