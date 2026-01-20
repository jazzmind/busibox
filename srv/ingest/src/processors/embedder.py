"""
Embedding generation with FastEmbed.

Supports multiple BGE models:
- BAAI/bge-small-en-v1.5 (384-d, ~134MB) - Fast, good for local dev
- BAAI/bge-base-en-v1.5 (768-d, ~438MB) - Balanced
- BAAI/bge-large-en-v1.5 (1024-d, ~1.3GB) - Best quality, production

Runs on CPU, no external service dependencies.
"""

from typing import List, Optional

import structlog
from fastembed import TextEmbedding

logger = structlog.get_logger()

# Model dimensions for FastEmbed BGE models
MODEL_DIMENSIONS = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    # Short aliases
    "bge-small-en-v1.5": 384,
    "bge-base-en-v1.5": 768,
    "bge-large-en-v1.5": 1024,
}

# Default model - use production model (bge-large-en-v1.5, 1024-d)
# For dev with faster downloads, set FASTEMBED_MODEL=BAAI/bge-small-en-v1.5
DEFAULT_MODEL = "BAAI/bge-large-en-v1.5"


class Embedder:
    """Generate embeddings with FastEmbed (configurable BGE model)."""
    
    def __init__(self, config: dict):
        """
        Initialize embedder with configuration.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.model_name = config.get("fastembed_model", DEFAULT_MODEL)
        
        # Get dimension from model lookup, default to small model dimension
        self.dimension = MODEL_DIMENSIONS.get(self.model_name, 384)
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
    
    def warmup(self):
        """
        Warm up the embedding model by loading it and running a test embedding.
        
        Call this at application startup to avoid cold-start latency on first request.
        """
        logger.info("Warming up embedding model", model=self.model_name)
        self._init_embedder()
        
        # Run a test embedding to fully initialize the model
        test_text = "warmup"
        list(self.embedder.embed([test_text]))
        
        logger.info(
            "Embedding model warmed up",
            model=self.model_name,
            dimension=self.dimension,
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
