"""
Embeddings API routes.

Provides embedding generation endpoints for external services.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import structlog

from processors.embedder import Embedder, MODEL_DIMENSIONS
from shared.config import Config

logger = structlog.get_logger()

router = APIRouter(prefix="/embeddings", tags=["embeddings"])


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


# Initialize embedder (shared across requests)
config = Config().to_dict()
embedder = Embedder(config)


@router.post("", response_model=EmbeddingResponse)
async def create_embeddings(
    embedding_request: EmbeddingRequest,
    request: Request,
):
    """
    Generate embeddings for text input.
    
    OpenAI-compatible API endpoint for generating embeddings.
    Uses FastEmbed with bge-large-en-v1.5 (1024-d).
    
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
        
        # Validate model - allow the configured model or compatible aliases
        configured_model = embedder.model_name
        # Extract base model name without BAAI/ prefix for comparison
        configured_base = configured_model.replace("BAAI/", "")
        
        if embedding_request.model:
            requested_base = embedding_request.model.replace("BAAI/", "")
            if requested_base != configured_base and embedding_request.model not in MODEL_DIMENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Model '{embedding_request.model}' not supported. Currently configured: '{configured_model}'"
                )
        
        logger.info(
            "Generating embeddings",
            user_id=user_id,
            text_count=len(texts),
            model=embedding_request.model,
        )
        
        # Generate embeddings
        embeddings = await embedder.embed_chunks(texts)
        
        # Format response in OpenAI-compatible format
        data = [
            EmbeddingData(
                embedding=embedding,
                index=i,
            )
            for i, embedding in enumerate(embeddings)
        ]
        
        # Calculate token usage (rough estimate: 4 chars per token)
        total_chars = sum(len(text) for text in texts)
        prompt_tokens = total_chars // 4
        
        response = EmbeddingResponse(
            data=data,
            model=configured_base,
            usage={
                "prompt_tokens": prompt_tokens,
                "total_tokens": prompt_tokens,
            }
        )
        
        logger.info(
            "Embeddings generated successfully",
            user_id=user_id,
            embedding_count=len(embeddings),
            dimension=len(embeddings[0]) if embeddings else 0,
        )
        
        return response
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Embedding generation failed",
            user_id=user_id,
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
    
    # Return the currently configured model
    configured_model = embedder.model_name
    configured_base = configured_model.replace("BAAI/", "")
    dimension = embedder.dimension
    
    return {
        "object": "list",
        "data": [
            {
                "id": configured_base,
                "object": "model",
                "owned_by": "BAAI",
                "dimension": dimension,
                "description": f"FastEmbed {configured_base} ({dimension}-d)",
            }
        ]
    }

