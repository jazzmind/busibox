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
        self.service_url = config.get("embedding_service_url", "http://10.96.200.206:8002")
        self.model = config.get("embedding_model", "bge-large-en-v1.5")
        self.embedding_dim = config.get("embedding_dim", 1024)
        self.timeout = 30.0
    
    async def embed_query(
        self, 
        query: str, 
        user_id: Optional[str] = None,
        authorization: Optional[str] = None
    ) -> Optional[List[float]]:
        """
        Generate embedding for a search query.
        
        Args:
            query: Search query text
            user_id: User ID for authentication (legacy, optional)
            authorization: Authorization header value for JWT passthrough (preferred)
        
        Returns:
            Embedding vector or None on failure
        """
        try:
            logger.info(
                "Generating query embedding",
                query=query[:100],
                model=self.model,
            )
            
            # Prepare headers - prefer JWT passthrough, fall back to X-User-Id
            headers = {}
            if authorization:
                headers["Authorization"] = authorization
            if user_id:
                headers["X-User-Id"] = user_id
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.service_url}/api/embeddings",
                    json={
                        "input": query,
                        "model": self.model,
                    },
                    headers=headers,
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
    
    async def embed_batch(
        self, 
        texts: List[str], 
        user_id: Optional[str] = None,
        authorization: Optional[str] = None
    ) -> Optional[List[List[float]]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            user_id: User ID for authentication (legacy, optional)
            authorization: Authorization header value for JWT passthrough (preferred)
        
        Returns:
            List of embedding vectors or None on failure
        """
        try:
            logger.info(
                "Generating batch embeddings",
                num_texts=len(texts),
                model=self.model,
            )
            
            # Prepare headers - prefer JWT passthrough, fall back to X-User-Id
            headers = {}
            if authorization:
                headers["Authorization"] = authorization
            if user_id:
                headers["X-User-Id"] = user_id
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.service_url}/api/embeddings",
                    json={
                        "input": texts,
                        "model": self.model,
                    },
                    headers=headers,
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

