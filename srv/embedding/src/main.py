"""
Busibox Embedding API

Dedicated embedding service that loads the FastEmbed model once at startup
and provides HTTP API for all embedding consumers (ingest-api, ingest-worker, search-api).

Endpoints:
- POST /embed - Generate embeddings for single or batch text
- GET /health - Health check
- GET /info - Model information
"""

import time
from contextlib import asynccontextmanager
from typing import List, Optional, Union

import structlog
from fastapi import FastAPI, HTTPException
from fastembed import TextEmbedding
from pydantic import BaseModel, Field

from config import config, MODEL_DIMENSIONS

# Configure structured logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()


# =============================================================================
# Request/Response Models (OpenAI-compatible format)
# =============================================================================

class EmbedRequest(BaseModel):
    """Request body for /embed endpoint."""
    input: Union[str, List[str]] = Field(
        ...,
        description="Text or list of texts to embed",
    )
    model: Optional[str] = Field(
        None,
        description="Model name (optional, ignored - uses configured model)",
    )


class EmbeddingData(BaseModel):
    """Single embedding result."""
    embedding: List[float] = Field(..., description="Embedding vector")
    index: int = Field(..., description="Index in the input list")


class EmbedResponse(BaseModel):
    """Response body for /embed endpoint."""
    data: List[EmbeddingData] = Field(..., description="List of embeddings")
    model: str = Field(..., description="Model used for embedding")
    dimension: int = Field(..., description="Embedding dimension")


class HealthResponse(BaseModel):
    """Response body for /health endpoint."""
    status: str
    model: str
    dimension: int
    model_loaded: bool


class InfoResponse(BaseModel):
    """Response body for /info endpoint."""
    model: str
    dimension: int
    batch_size: int
    available_models: dict


# =============================================================================
# Global State
# =============================================================================

# Embedding model instance (loaded at startup)
embedder: Optional[TextEmbedding] = None
model_loaded: bool = False


# =============================================================================
# Application Lifecycle
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - load model at startup."""
    global embedder, model_loaded
    
    logger.info(
        "Starting Embedding API",
        model=config.model_name,
        dimension=config.dimension,
        batch_size=config.batch_size,
        cache_dir=config.cache_dir,
    )
    
    # Load model at startup
    start_time = time.time()
    
    # Check if model is already cached
    cached = False
    if config.cache_dir:
        import os
        # FastEmbed normalizes model names for cache directory
        model_normalized = config.model_name.replace("/", "_").replace(":", "_")
        model_path = os.path.join(config.cache_dir, model_normalized)
        if os.path.exists(model_path):
            cached = True
            logger.info(
                "Found cached model",
                model=config.model_name,
                cache_path=model_path,
            )
        else:
            logger.info(
                "Model not cached, will download",
                model=config.model_name,
                expected_path=model_path,
            )
    
    logger.info(
        "Loading FastEmbed model",
        model=config.model_name,
        from_cache=cached,
    )
    
    try:
        # Initialize TextEmbedding with cache_dir if configured
        if config.cache_dir:
            embedder = TextEmbedding(
                model_name=config.model_name,
                cache_dir=config.cache_dir,
            )
        else:
            embedder = TextEmbedding(model_name=config.model_name)
        
        # Warmup: run a test embedding to fully initialize
        logger.info("Warming up model")
        list(embedder.embed(["warmup"]))
        
        load_time = time.time() - start_time
        model_loaded = True
        
        logger.info(
            "Model loaded and ready",
            model=config.model_name,
            dimension=config.dimension,
            load_time_seconds=round(load_time, 2),
            from_cache=cached,
        )
    except Exception as e:
        logger.error("Failed to load model", error=str(e), exc_info=True)
        raise
    
    yield
    
    # Cleanup on shutdown
    logger.info("Shutting down Embedding API")


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="Busibox Embedding API",
    description="Dedicated embedding service for text embedding generation",
    version="1.0.0",
    lifespan=lifespan,
)


# =============================================================================
# Endpoints
# =============================================================================

@app.post("/embed", response_model=EmbedResponse)
async def embed(request: EmbedRequest) -> EmbedResponse:
    """
    Generate embeddings for text input.
    
    Accepts a single string or list of strings.
    Returns OpenAI-compatible embedding response.
    """
    global embedder
    
    if not model_loaded or embedder is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    # Normalize input to list
    texts = request.input if isinstance(request.input, list) else [request.input]
    
    if not texts:
        return EmbedResponse(
            data=[],
            model=config.model_name,
            dimension=config.dimension,
        )
    
    logger.info("Generating embeddings", count=len(texts))
    start_time = time.time()
    
    try:
        # Generate embeddings
        embeddings_generator = embedder.embed(texts, batch_size=config.batch_size)
        embeddings = [emb.tolist() for emb in embeddings_generator]
        
        duration = time.time() - start_time
        logger.info(
            "Embeddings generated",
            count=len(embeddings),
            duration_seconds=round(duration, 3),
        )
        
        # Build response
        data = [
            EmbeddingData(embedding=emb, index=i)
            for i, emb in enumerate(embeddings)
        ]
        
        return EmbedResponse(
            data=data,
            model=config.model_name,
            dimension=config.dimension,
        )
        
    except Exception as e:
        logger.error("Embedding generation failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Embedding generation failed: {str(e)}")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(
        status="healthy" if model_loaded else "loading",
        model=config.model_name,
        dimension=config.dimension,
        model_loaded=model_loaded,
    )


@app.get("/info", response_model=InfoResponse)
async def info() -> InfoResponse:
    """Get model information."""
    return InfoResponse(
        model=config.model_name,
        dimension=config.dimension,
        batch_size=config.batch_size,
        available_models=MODEL_DIMENSIONS,
    )


@app.get("/")
async def root():
    """Root endpoint - basic info."""
    return {
        "service": "Busibox Embedding API",
        "model": config.model_name,
        "dimension": config.dimension,
        "status": "ready" if model_loaded else "loading",
    }
