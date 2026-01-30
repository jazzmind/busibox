"""
Embedding API Client.

Client for calling the dedicated embedding-api service.
Replaces local FastEmbed model loading for faster worker restarts.
"""

import os
from typing import List, Optional

import httpx
import structlog

logger = structlog.get_logger()


class EmbeddingClient:
    """Client for the dedicated embedding-api service."""
    
    def __init__(self, config: dict):
        """
        Initialize embedding client.
        
        Args:
            config: Configuration dictionary with embedding_api_url and embedding_dimension
        """
        self.config = config
        self.api_url = config.get("embedding_api_url") or os.getenv("EMBEDDING_API_URL", "http://embedding-api:8005")
        self.dimension = config.get("embedding_dimension", 1024)
        self.batch_size = config.get("embedding_batch_size", 32)
        
        # HTTP client with longer timeout for batch operations
        self._client: Optional[httpx.Client] = None
        
        logger.info(
            "EmbeddingClient initialized",
            api_url=self.api_url,
            dimension=self.dimension,
            batch_size=self.batch_size,
        )
    
    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.Client(timeout=120.0)  # 2 minute timeout for large batches
        return self._client
    
    def close(self):
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None
    
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
    
    async def embed_chunks(self, chunks: List[str]) -> List[List[float]]:
        """
        Generate embeddings for text chunks.
        
        Args:
            chunks: List of text chunks
        
        Returns:
            List of embedding vectors
        
        Raises:
            Exception: If embedding generation fails
        """
        if not chunks:
            return []
        
        logger.info(
            "Generating embeddings via embedding-api",
            chunk_count=len(chunks),
            api_url=self.api_url,
        )
        
        try:
            # Use synchronous client (worker runs in sync context)
            client = self._get_client()
            response = client.post(
                f"{self.api_url}/embed",
                json={"input": chunks},
            )
            
            if response.status_code != 200:
                error_detail = response.text
                logger.error(
                    "Embedding API returned error",
                    status_code=response.status_code,
                    error=error_detail,
                )
                raise Exception(f"Embedding service error ({response.status_code}): {error_detail}")
            
            result = response.json()
            embeddings = [item["embedding"] for item in result["data"]]
            
            logger.info(
                "Embeddings generated successfully",
                chunk_count=len(chunks),
                embedding_count=len(embeddings),
                dimension=result.get("dimension", len(embeddings[0]) if embeddings else 0),
            )
            
            return embeddings
            
        except httpx.RequestError as e:
            logger.error(
                "Failed to connect to embedding-api",
                error=str(e),
                api_url=self.api_url,
            )
            raise Exception(f"Embedding service unavailable: {str(e)}")
        except Exception as e:
            logger.error(
                "Embedding generation failed",
                error=str(e),
                exc_info=True,
            )
            raise
    
    def embed_chunks_sync(self, chunks: List[str]) -> List[List[float]]:
        """
        Synchronous version of embed_chunks for use in sync contexts.
        
        Args:
            chunks: List of text chunks
        
        Returns:
            List of embedding vectors
        """
        if not chunks:
            return []
        
        logger.info(
            "Generating embeddings via embedding-api (sync)",
            chunk_count=len(chunks),
            api_url=self.api_url,
        )
        
        try:
            client = self._get_client()
            response = client.post(
                f"{self.api_url}/embed",
                json={"input": chunks},
            )
            
            if response.status_code != 200:
                error_detail = response.text
                logger.error(
                    "Embedding API returned error",
                    status_code=response.status_code,
                    error=error_detail,
                )
                raise Exception(f"Embedding service error ({response.status_code}): {error_detail}")
            
            result = response.json()
            embeddings = [item["embedding"] for item in result["data"]]
            
            logger.info(
                "Embeddings generated successfully",
                chunk_count=len(chunks),
                embedding_count=len(embeddings),
                dimension=result.get("dimension", len(embeddings[0]) if embeddings else 0),
            )
            
            return embeddings
            
        except httpx.RequestError as e:
            logger.error(
                "Failed to connect to embedding-api",
                error=str(e),
                api_url=self.api_url,
            )
            raise Exception(f"Embedding service unavailable: {str(e)}")
    
    def get_embedding_dimension(self) -> int:
        """Get the dimension of embeddings being generated."""
        return self.dimension
    
    def warmup(self):
        """
        Verify embedding service is available.
        
        Unlike the local Embedder, no warmup is needed since the embedding-api
        loads its model at startup. This just verifies connectivity.
        """
        logger.info("Checking embedding-api connectivity", api_url=self.api_url)
        try:
            client = self._get_client()
            response = client.get(f"{self.api_url}/health")
            if response.status_code == 200:
                health = response.json()
                if health.get("model_loaded"):
                    logger.info(
                        "Embedding-api is ready",
                        model=health.get("model"),
                        dimension=health.get("dimension"),
                    )
                else:
                    logger.warning("Embedding-api is still loading model")
            else:
                logger.warning(
                    "Embedding-api health check returned non-200",
                    status_code=response.status_code,
                )
        except Exception as e:
            logger.warning(
                "Could not verify embedding-api connectivity",
                error=str(e),
            )
