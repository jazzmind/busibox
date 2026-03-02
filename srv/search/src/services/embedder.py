"""
Embedding service for generating query embeddings.

Calls the dedicated embedding-api service directly (no authentication needed
for internal service-to-service calls).
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
        # Use embedding-api directly (internal service, no auth needed)
        self.api_url = config.get("embedding_api_url", "http://embedding-api:8005")
        self.model = config.get("embedding_model", "bge-large-en-v1.5")
        self.embedding_dim = config.get("embedding_dim", 768)
        self.timeout = 30.0
        
        logger.info(
            "EmbeddingService initialized",
            api_url=self.api_url,
            model=self.model,
            embedding_dim=self.embedding_dim,
        )
    
    async def embed_query(
        self, 
        query: str, 
        user_id: Optional[str] = None,
        authorization: Optional[str] = None
    ) -> Optional[List[float]]:
        """
        Generate embedding for a search query.
        
        Calls the dedicated embedding-api service directly.
        No authentication needed for internal service calls.
        
        Args:
            query: Search query text
            user_id: User ID (unused - kept for API compatibility)
            authorization: Authorization header (unused - kept for API compatibility)
        
        Returns:
            Embedding vector or None on failure
        """
        try:
            logger.info(
                "Generating query embedding via embedding-api",
                query=query[:100],
                api_url=self.api_url,
            )
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.api_url}/embed",
                    json={"input": query},
                )
                
                if response.status_code != 200:
                    logger.error(
                        "Embedding API returned error",
                        status_code=response.status_code,
                        response=response.text,
                    )
                    return None
                
                data = response.json()
                embedding = data["data"][0]["embedding"]
                
                logger.debug(
                    "Query embedding generated",
                    embedding_dim=len(embedding),
                    model=data.get("model"),
                )
                
                return embedding
        
        except httpx.RequestError as e:
            logger.error(
                "Failed to connect to embedding-api",
                error=str(e),
                api_url=self.api_url,
            )
            return None
        except Exception as e:
            logger.error(
                "Failed to generate embedding",
                error=str(e),
                exc_info=True,
            )
            return None
    
    async def embed_batch(
        self, 
        texts: List[str], 
        user_id: Optional[str] = None,
        authorization: Optional[str] = None
    ) -> Optional[List[List[float]]]:
        """
        Generate embeddings for multiple texts.
        
        Calls the dedicated embedding-api service directly.
        No authentication needed for internal service calls.
        
        Args:
            texts: List of texts to embed
            user_id: User ID (unused - kept for API compatibility)
            authorization: Authorization header (unused - kept for API compatibility)
        
        Returns:
            List of embedding vectors or None on failure
        """
        try:
            logger.info(
                "Generating batch embeddings via embedding-api",
                num_texts=len(texts),
                api_url=self.api_url,
            )
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.api_url}/embed",
                    json={"input": texts},
                )
                
                if response.status_code != 200:
                    logger.error(
                        "Embedding API returned error",
                        status_code=response.status_code,
                        response=response.text,
                    )
                    return None
                
                data = response.json()
                embeddings = [item["embedding"] for item in data["data"]]
                
                logger.debug(
                    "Batch embeddings generated",
                    num_embeddings=len(embeddings),
                    model=data.get("model"),
                )
                
                return embeddings
        
        except httpx.RequestError as e:
            logger.error(
                "Failed to connect to embedding-api",
                error=str(e),
                api_url=self.api_url,
            )
            return None
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
                response = await client.get(f"{self.api_url}/health")
                if response.status_code == 200:
                    health = response.json()
                    return health.get("model_loaded", False)
                return False
        except Exception as e:
            logger.error("Embedding service health check failed", error=str(e))
            return False
