"""
Main FastAPI application for Docs API Service.

A lightweight service that exposes busibox documentation and OpenAPI
specifications via REST API, enabling ai-portal to fetch documentation
from deployed environments where filesystem access is not available.
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import docs, openapi, health

# Create FastAPI app
app = FastAPI(
    title="Busibox Docs API",
    description="""
    Documentation API for Busibox platform.
    
    **Documentation Features:**
    - Serve markdown documentation with frontmatter parsing
    - Category-based organization (user/developer)
    - Navigation support (prev/next links)
    
    **OpenAPI Features:**
    - List available service API specifications
    - Serve raw YAML OpenAPI specs for client generation
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

# Include routers
app.include_router(docs.router, prefix="/docs", tags=["documentation"])
app.include_router(openapi.router, prefix="/openapi", tags=["openapi"])
app.include_router(health.router, prefix="/health", tags=["health"])


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Busibox Docs API",
        "version": "1.0.0",
        "status": "operational",
        "endpoints": {
            "docs": "/docs/{category}",
            "openapi": "/openapi",
            "health": "/health/live",
            "swagger": "/docs",
        },
    }


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.environ.get("SERVICE_PORT", "8004"))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        reload=False,
    )
