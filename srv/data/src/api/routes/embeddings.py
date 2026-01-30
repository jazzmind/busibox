"""
Embeddings API routes.

Provides embedding generation endpoints for external services.
Proxies requests to the dedicated embedding-api service.
"""

import os
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import structlog

from shared.config import Config

logger = structlog.get_logger()

router = APIRouter(prefix="/embeddings", tags=["embeddings"])

# Config instance
config = Config()

# Embedding API URL from environment
EMBEDDING_API_URL = config.embedding_api_url or os.getenv("EMBEDDING_API_URL", "http://embedding-api:8005")

# HTTP client for embedding service calls
_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    """Get or create HTTP client for embedding API calls."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=60.0)
    return _http_client


class EmbeddingRequest(BaseModel):
    """Request model for embedding generation."""
    
    input: str | List[str] = Field(
        ...,
        description="Text or list of texts to embed"
    )
    model: Optional[str] = Field(
        default=None,
        description="Embedding model name (optional, uses configured model if not specified)"
    )
    encoding_format: Optional[str] = Field(
        default="float",
        description="Encoding format (only 'float' supported)"
    )


class EmbeddingData(BaseModel):
    """Single embedding result."""
    
    object: str = "embedding"
    embedding: List[float]
    index: int


class EmbeddingResponse(BaseModel):
    """Response model for embedding generation."""
    
    object: str = "list"
    data: List[EmbeddingData]
    model: str
    usage: dict


@router.post("", response_model=EmbeddingResponse)
async def create_embeddings(
    embedding_request: EmbeddingRequest,
    request: Request,
):
    """
    Generate embeddings for text input.
    
    OpenAI-compatible API endpoint for generating embeddings.
    Proxies requests to the dedicated embedding-api service.
    
    Args:
        embedding_request: Embedding request with text input
        request: FastAPI request (for user_id from middleware)
    
    Returns:
        Embeddings in OpenAI-compatible format
    
    Example:
        ```
        POST /api/embeddings
        {
            "input": "Hello, world!",
            "model": "bge-large-en-v1.5"
        }
        ```
    """
    try:
        # Get user_id from middleware (set by AuthMiddleware)
        user_id = request.state.user_id
        if not user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")
        
        # Normalize input to list
        if isinstance(embedding_request.input, str):
            texts = [embedding_request.input]
        else:
            texts = embedding_request.input
        
        if not texts:
            raise HTTPException(status_code=400, detail="Input cannot be empty")
        
        logger.info(
            "Proxying embedding request to embedding-api",
            user_id=user_id,
            text_count=len(texts),
            embedding_api_url=EMBEDDING_API_URL,
        )
        
        # Call embedding-api service
        client = get_http_client()
        response = await client.post(
            f"{EMBEDDING_API_URL}/embed",
            json={"input": texts, "model": embedding_request.model},
        )
        
        if response.status_code != 200:
            error_detail = response.text
            logger.error(
                "Embedding API returned error",
                status_code=response.status_code,
                error=error_detail,
            )
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Embedding service error: {error_detail}"
            )
        
        result = response.json()
        
        # Convert to OpenAI-compatible format
        data = [
            EmbeddingData(
                embedding=item["embedding"],
                index=item["index"],
            )
            for item in result["data"]
        ]
        
        # Calculate token usage (rough estimate: 4 chars per token)
        total_chars = sum(len(text) for text in texts)
        prompt_tokens = total_chars // 4
        
        openai_response = EmbeddingResponse(
            data=data,
            model=result.get("model", "bge-large-en-v1.5"),
            usage={
                "prompt_tokens": prompt_tokens,
                "total_tokens": prompt_tokens,
            }
        )
        
        logger.info(
            "Embeddings generated successfully via embedding-api",
            user_id=user_id,
            embedding_count=len(data),
            dimension=result.get("dimension", len(data[0].embedding) if data else 0),
        )
        
        return openai_response
    
    except httpx.RequestError as e:
        logger.error(
            "Failed to connect to embedding-api",
            error=str(e),
            embedding_api_url=EMBEDDING_API_URL,
        )
        raise HTTPException(
            status_code=503,
            detail=f"Embedding service unavailable: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Embedding generation failed",
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Embedding generation failed: {str(e)}"
        )


@router.get("/models")
async def list_models(request: Request):
    """
    List available embedding models.
    
    Args:
        request: FastAPI request (for user_id from middleware)
    
    Returns:
        List of available models with metadata
    """
    # Get user_id from middleware (set by AuthMiddleware)
    user_id = request.state.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        # Get model info from embedding-api
        client = get_http_client()
        response = await client.get(f"{EMBEDDING_API_URL}/info")
        
        if response.status_code != 200:
            # Fallback to default values
            model = "bge-large-en-v1.5"
            dimension = config.embedding_dimension
        else:
            info = response.json()
            model = info.get("model", "bge-large-en-v1.5").replace("BAAI/", "")
            dimension = info.get("dimension", config.embedding_dimension)
        
        return {
            "object": "list",
            "data": [
                {
                    "id": model,
                    "object": "model",
                    "owned_by": "BAAI",
                    "dimension": dimension,
                    "description": f"FastEmbed {model} ({dimension}-d) via embedding-api",
                }
            ]
        }
    
    except httpx.RequestError as e:
        logger.warning(
            "Failed to get model info from embedding-api, using defaults",
            error=str(e),
        )
        return {
            "object": "list",
            "data": [
                {
                    "id": "bge-large-en-v1.5",
                    "object": "model",
                    "owned_by": "BAAI",
                    "dimension": config.embedding_dimension,
                    "description": f"FastEmbed bge-large-en-v1.5 ({config.embedding_dimension}-d)",
                }
            ]
        }
