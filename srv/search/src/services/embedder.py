"""
Embedding service for generating query embeddings.
"""

import structlog
import httpx
from typing import List, Optional, Dict

logger = structlog.get_logger()


class EmbeddingService:
    """Service for generating embeddings for search queries."""
    
    def __init__(self, config: Dict):
        """Initialize embedding service."""
        self.config = config
        self.service_url = config.get("embedding_service_url", "http://10.96.200.30:8000")
        self.model = config.get("embedding_model", "text-embedding-3-small")
        self.embedding_dim = config.get("embedding_dim", 1536)
        self.timeout = 30.0
    
    async def embed_query(self, query: str) -> Optional[List[float]]:
        """
        Generate embedding for a search query.
        
        Args:
            query: Search query text
        
        Returns:
            Embedding vector or None on failure
        """
        try:
            logger.info(
                "Generating query embedding",
                query=query[:100],
                model=self.model,
            )
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.service_url}/v1/embeddings",
                    json={
                        "input": query,
                        "model": self.model,
                    },
                )
                
                if response.status_code != 200:
                    logger.error(
                        "Embedding service returned error",
                        status_code=response.status_code,
                        response=response.text,
                    )
                    return None
                
                data = response.json()
                embedding = data["data"][0]["embedding"]
                
                logger.debug(
                    "Query embedding generated",
                    embedding_dim=len(embedding),
                )
                
                return embedding
        
        except Exception as e:
            logger.error(
                "Failed to generate embedding",
                error=str(e),
                exc_info=True,
            )
            return None
    
    async def embed_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
        
        Returns:
            List of embedding vectors or None on failure
        """
        try:
            logger.info(
                "Generating batch embeddings",
                num_texts=len(texts),
                model=self.model,
            )
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.service_url}/v1/embeddings",
                    json={
                        "input": texts,
                        "model": self.model,
                    },
                )
                
                if response.status_code != 200:
                    logger.error(
                        "Embedding service returned error",
                        status_code=response.status_code,
                        response=response.text,
                    )
                    return None
                
                data = response.json()
                embeddings = [item["embedding"] for item in data["data"]]
                
                logger.debug(
                    "Batch embeddings generated",
                    num_embeddings=len(embeddings),
                )
                
                return embeddings
        
        except Exception as e:
            logger.error(
                "Failed to generate batch embeddings",
                error=str(e),
                exc_info=True,
            )
            return None
    
    async def health_check(self) -> bool:
        """Check if embedding service is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.service_url}/health")
                return response.status_code == 200
        except Exception as e:
            logger.error("Embedding service health check failed", error=str(e))
            return False

