"""
Embeddings API routes.

Provides embedding generation endpoints for external services.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
import structlog

from api.middleware.auth import verify_bearer_token
from processors.embedder import Embedder
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
        default="bge-large-en-v1.5",
        description="Embedding model name (currently only bge-large-en-v1.5 supported)"
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
    request: EmbeddingRequest,
    user_id: str = Depends(verify_bearer_token)
):
    """
    Generate embeddings for text input.
    
    OpenAI-compatible API endpoint for generating embeddings.
    Uses FastEmbed with bge-large-en-v1.5 (1024-d).
    
    Args:
        request: Embedding request with text input
        user_id: User ID from bearer token authentication
    
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
        # Normalize input to list
        if isinstance(request.input, str):
            texts = [request.input]
        else:
            texts = request.input
        
        if not texts:
            raise HTTPException(status_code=400, detail="Input cannot be empty")
        
        # Validate model
        if request.model and request.model != "bge-large-en-v1.5":
            raise HTTPException(
                status_code=400,
                detail=f"Model '{request.model}' not supported. Only 'bge-large-en-v1.5' is available."
            )
        
        logger.info(
            "Generating embeddings",
            user_id=user_id,
            text_count=len(texts),
            model=request.model,
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
            model="bge-large-en-v1.5",
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
async def list_models(user_id: str = Depends(verify_bearer_token)):
    """
    List available embedding models.
    
    Returns:
        List of available models with metadata
    """
    return {
        "object": "list",
        "data": [
            {
                "id": "bge-large-en-v1.5",
                "object": "model",
                "owned_by": "BAAI",
                "dimension": 1024,
                "description": "High-quality English embeddings (1024-d)",
            }
        ]
    }

