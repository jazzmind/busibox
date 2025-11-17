"""
Embedding generation with vLLM primary and FastEmbed fallback.

Tries to use vLLM via liteLLM for embeddings first (better quality, GPU-accelerated).
Falls back to FastEmbed (local ONNX) if vLLM is unavailable.
"""

from typing import List, Optional
import asyncio

import structlog
from fastembed import TextEmbedding

logger = structlog.get_logger()


class Embedder:
    """Generate embeddings with vLLM primary and FastEmbed fallback."""
    
    def __init__(self, config: dict):
        """
        Initialize embedder with configuration.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.litellm_base_url = config.get("litellm_base_url")
        self.litellm_api_key = config.get("litellm_api_key", "")
        
        # Primary: vLLM via liteLLM (Qwen3-Embedding-8B, 4096 dims)
        self.primary_model = config.get("embedding_model", "qwen3-embedding")
        self.primary_dimension = 4096
        
        # Fallback: FastEmbed (BAAI/bge-base-en-v1.5, 768 dims)
        self.fallback_model = "BAAI/bge-base-en-v1.5"
        self.fallback_dimension = 768
        
        self.batch_size = config.get("embedding_batch_size", 32)
        
        # Track which backend is being used
        self.using_fallback = False
        self.fallback_embedder: Optional[TextEmbedding] = None
        
        logger.info(
            "Initializing hybrid embedder",
            primary_model=self.primary_model,
            primary_dimension=self.primary_dimension,
            fallback_model=self.fallback_model,
            fallback_dimension=self.fallback_dimension,
            litellm_url=self.litellm_base_url,
        )
    
    def _init_fallback(self):
        """Initialize FastEmbed fallback if not already initialized."""
        if self.fallback_embedder is None:
            logger.warning(
                "Initializing FastEmbed fallback",
                model=self.fallback_model,
            )
            self.fallback_embedder = TextEmbedding(model_name=self.fallback_model)
            self.using_fallback = True
    
    async def _try_litellm_embedding(self, chunks: List[str]) -> Optional[List[List[float]]]:
        """
        Try to generate embeddings via liteLLM/vLLM.
        
        Returns None if liteLLM is unavailable or fails.
        """
        if not self.litellm_base_url:
            logger.debug("No liteLLM base URL configured, skipping vLLM")
            return None
        
        if not self.litellm_api_key:
            logger.warning(
                "No liteLLM API key configured (LITELLM_API_KEY not set), falling back to FastEmbed",
                litellm_url=self.litellm_base_url,
            )
            return None
        
        try:
            from openai import AsyncOpenAI
            
            # Use OpenAI SDK to call liteLLM proxy (OpenAI-compatible API)
            client = AsyncOpenAI(
                base_url=self.litellm_base_url,
                api_key=self.litellm_api_key,
            )
            
            all_embeddings = []
            
            for i in range(0, len(chunks), self.batch_size):
                batch = chunks[i:i + self.batch_size]
                
                # Call liteLLM proxy using OpenAI SDK
                # The proxy routes qwen3-embedding to vLLM
                response = await client.embeddings.create(
                    model=self.primary_model,  # "qwen3-embedding"
                    input=batch,
                    timeout=30.0,
                )
                
                batch_embeddings = [item.embedding for item in response.data]
                all_embeddings.extend(batch_embeddings)
            
            logger.info(
                "Embeddings generated via vLLM",
                chunk_count=len(chunks),
                model=self.primary_model,
                dimension=len(all_embeddings[0]) if all_embeddings else 0,
            )
            
            return all_embeddings
            
        except Exception as e:
            logger.warning(
                "vLLM embedding failed, will use FastEmbed fallback",
                error=str(e),
                error_type=type(e).__name__,
            )
            return None
    
    def _fallback_embedding(self, chunks: List[str]) -> List[List[float]]:
        """Generate embeddings using FastEmbed fallback."""
        self._init_fallback()
        
        logger.info(
            "Generating embeddings via FastEmbed fallback",
            chunk_count=len(chunks),
            model=self.fallback_model,
        )
        
        # FastEmbed handles batching internally
        embeddings_generator = self.fallback_embedder.embed(chunks, batch_size=self.batch_size)
        embeddings = [embedding.tolist() for embedding in embeddings_generator]
        
        logger.info(
            "Embeddings generated via FastEmbed",
            chunk_count=len(chunks),
            dimension=len(embeddings[0]) if embeddings else 0,
        )
        
        return embeddings
    
    async def embed_single(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding for a single text string.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector or None if failed
        """
        embeddings = await self.embed_chunks([text])
        return embeddings[0] if embeddings else None
    
    async def embed_chunks(
        self,
        chunks: List[str],
    ) -> List[List[float]]:
        """
        Generate embeddings for text chunks.
        
        Tries vLLM first, falls back to FastEmbed if unavailable.
        
        Args:
            chunks: List of text chunks
        
        Returns:
            List of embedding vectors
        
        Raises:
            Exception: If both vLLM and FastEmbed fail
        """
        if not chunks:
            return []
        
        logger.info(
            "Generating embeddings",
            chunk_count=len(chunks),
            using_fallback=self.using_fallback,
        )
        
        try:
            # Try vLLM first (unless we're already using fallback)
            if not self.using_fallback:
                embeddings = await self._try_litellm_embedding(chunks)
                if embeddings is not None:
                    return embeddings
                
                # vLLM failed, switch to fallback permanently for this session
                logger.warning("Switching to FastEmbed fallback for remainder of session")
                self.using_fallback = True
            
            # Use FastEmbed fallback
            return self._fallback_embedding(chunks)
            
        except Exception as e:
            logger.error(
                "All embedding methods failed",
                error=str(e),
                chunk_count=len(chunks),
                exc_info=True,
            )
            raise
    
    def get_embedding_dimension(self) -> int:
        """Get the dimension of embeddings being generated."""
        return self.fallback_dimension if self.using_fallback else self.primary_dimension
