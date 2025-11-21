"""
Embedding generation with FastEmbed.

Uses BAAI/bge-large-en-v1.5 (1024-d) for high-quality local embeddings.
Runs on CPU, no external service dependencies.
"""

from typing import List, Optional

import structlog
from fastembed import TextEmbedding

logger = structlog.get_logger()


class Embedder:
    """Generate embeddings with FastEmbed (bge-large-en-v1.5)."""
    
    def __init__(self, config: dict):
        """
        Initialize embedder with configuration.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.model_name = config.get("fastembed_model", "BAAI/bge-large-en-v1.5")
        self.dimension = 1024  # bge-large-en-v1.5 dimension
        self.batch_size = config.get("embedding_batch_size", 32)
        
        # Initialize FastEmbed model (lazy loading)
        self.embedder: Optional[TextEmbedding] = None
        
        logger.info(
            "Embedder initialized",
            model=self.model_name,
            dimension=self.dimension,
            batch_size=self.batch_size,
        )
    
    def _init_embedder(self):
        """Initialize FastEmbed model if not already initialized."""
        if self.embedder is None:
            logger.info(
                "Loading FastEmbed model",
                model=self.model_name,
            )
            self.embedder = TextEmbedding(model_name=self.model_name)
            logger.info(
                "FastEmbed model loaded",
                model=self.model_name,
            )
    
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
        
        Args:
            chunks: List of text chunks
        
        Returns:
            List of embedding vectors (1024-d)
        
        Raises:
            Exception: If embedding generation fails
        """
        if not chunks:
            return []
        
        self._init_embedder()
        
        logger.info(
            "Generating embeddings",
            chunk_count=len(chunks),
            model=self.model_name,
        )
        
        try:
            # FastEmbed handles batching internally
            embeddings_generator = self.embedder.embed(chunks, batch_size=self.batch_size)
            embeddings = [embedding.tolist() for embedding in embeddings_generator]
            
            logger.info(
                "Embeddings generated successfully",
                chunk_count=len(chunks),
                dimension=len(embeddings[0]) if embeddings else 0,
            )
            
            return embeddings
            
        except Exception as e:
            logger.error(
                "Embedding generation failed",
                error=str(e),
                chunk_count=len(chunks),
                exc_info=True,
            )
            raise
    
    def get_embedding_dimension(self) -> int:
        """Get the dimension of embeddings being generated."""
        return self.dimension
