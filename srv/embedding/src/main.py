"""
Busibox Embedding API

Dedicated embedding service that loads the FastEmbed model once at startup
and provides HTTP API for all embedding consumers (data-api, data-worker, search-api).

Supports Matryoshka models (e.g. nomic-embed-text-v1.5) with automatic
dimension truncation and L2 renormalization via EMBEDDING_DIMENSION env var.

Endpoints:
- POST /embed - Generate embeddings for single or batch text
- GET /health - Health check
- GET /info - Model information
"""

import time
from contextlib import asynccontextmanager
from typing import List, Optional, Union

import numpy as np
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
    native_dimension: int
    matryoshka: bool
    model_loaded: bool


class InfoResponse(BaseModel):
    """Response body for /info endpoint."""
    model: str
    dimension: int
    native_dimension: int
    matryoshka: bool
    matryoshka_dimensions: List[int]
    batch_size: int
    available_models: dict


# =============================================================================
# Global State
# =============================================================================

# Embedding model instance (loaded at startup)
embedder: Optional[TextEmbedding] = None
model_loaded: bool = False


# =============================================================================
# Matryoshka Truncation
# =============================================================================

def truncate_and_renormalize(embeddings: List[np.ndarray], target_dim: int) -> List[np.ndarray]:
    """
    Truncate embeddings to target_dim and L2-renormalize.
    
    Matryoshka models pack the most important information into the first N
    dimensions.  After slicing we must renormalize so cosine similarity
    still works correctly.
    """
    result = []
    for emb in embeddings:
        truncated = emb[:target_dim]
        norm = np.linalg.norm(truncated)
        if norm > 0:
            truncated = truncated / norm
        result.append(truncated)
    return result


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
        native_dimension=config.native_dimension,
        output_dimension=config.dimension,
        matryoshka=config.matryoshka,
        truncate=config.truncate,
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
        warmup_embs = list(embedder.embed(["warmup"]))
        
        # Verify truncation produces correct output dimension
        if config.truncate:
            truncated = truncate_and_renormalize(warmup_embs, config.dimension)
            actual_dim = len(truncated[0])
            logger.info(
                "Matryoshka truncation verified",
                native_dim=len(warmup_embs[0]),
                truncated_dim=actual_dim,
                target_dim=config.dimension,
            )
        
        load_time = time.time() - start_time
        model_loaded = True
        
        logger.info(
            "Model loaded and ready",
            model=config.model_name,
            output_dimension=config.dimension,
            native_dimension=config.native_dimension,
            matryoshka=config.matryoshka,
            truncate=config.truncate,
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
    description="Dedicated embedding service with Matryoshka truncation support",
    version="2.0.0",
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
    
    If the model supports Matryoshka and EMBEDDING_DIMENSION is set below the
    native dimension, embeddings are truncated and L2-renormalized automatically.
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
        # Generate full-dimension embeddings
        embeddings_generator = embedder.embed(texts, batch_size=config.batch_size)
        raw_embeddings = list(embeddings_generator)
        
        # Apply Matryoshka truncation + renormalization if configured
        if config.truncate:
            processed = truncate_and_renormalize(raw_embeddings, config.dimension)
            embeddings = [emb.tolist() for emb in processed]
        else:
            embeddings = [emb.tolist() for emb in raw_embeddings]
        
        duration = time.time() - start_time
        logger.info(
            "Embeddings generated",
            count=len(embeddings),
            dimension=config.dimension,
            truncated=config.truncate,
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
        native_dimension=config.native_dimension,
        matryoshka=config.matryoshka,
        model_loaded=model_loaded,
    )


@app.get("/info", response_model=InfoResponse)
async def info() -> InfoResponse:
    """Get model information."""
    return InfoResponse(
        model=config.model_name,
        dimension=config.dimension,
        native_dimension=config.native_dimension,
        matryoshka=config.matryoshka,
        matryoshka_dimensions=config.matryoshka_dimensions,
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
        "native_dimension": config.native_dimension,
        "matryoshka": config.matryoshka,
        "status": "ready" if model_loaded else "loading",
    }
