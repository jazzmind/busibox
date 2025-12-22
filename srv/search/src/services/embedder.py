"""
Embedding service for generating query embeddings.

Uses OAuth2 Token Exchange to get a token with the correct audience (ingest-api)
while preserving user identity and roles for RLS enforcement.
"""

import structlog
import httpx
from typing import List, Optional, Dict

from services.token_exchange import TokenExchangeService

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
        
        # Token exchange for service-to-service auth
        self.token_exchange = TokenExchangeService(config)
    
    async def embed_query(
        self, 
        query: str, 
        user_id: Optional[str] = None,
        authorization: Optional[str] = None
    ) -> Optional[List[float]]:
        """
        Generate embedding for a search query.
        
        Uses token exchange to get a token with audience=ingest-api while
        preserving the user's identity and roles for RLS enforcement.
        
        Args:
            query: Search query text
            user_id: User ID for token exchange (required for RLS)
            authorization: Original authorization header (unused, kept for compatibility)
        
        Returns:
            Embedding vector or None on failure
        """
        try:
            logger.info(
                "Generating query embedding",
                query=query[:100],
                model=self.model,
            )
            
            # Get token for calling ingest service
            headers = {}
            if user_id:
                # Use token exchange to get a token with ingest-api audience
                ingest_token = await self.token_exchange.get_token_for_service(
                    user_id=user_id,
                    target_audience="ingest-api",
                )
                if ingest_token:
                    headers["Authorization"] = f"Bearer {ingest_token}"
                else:
                    logger.warning(
                        "Token exchange failed, attempting without auth",
                        user_id=user_id,
                    )
            
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
        
        Uses token exchange to get a token with audience=ingest-api while
        preserving the user's identity and roles for RLS enforcement.
        
        Args:
            texts: List of texts to embed
            user_id: User ID for token exchange (required for RLS)
            authorization: Original authorization header (unused, kept for compatibility)
        
        Returns:
            List of embedding vectors or None on failure
        """
        try:
            logger.info(
                "Generating batch embeddings",
                num_texts=len(texts),
                model=self.model,
            )
            
            # Get token for calling ingest service
            headers = {}
            if user_id:
                # Use token exchange to get a token with ingest-api audience
                ingest_token = await self.token_exchange.get_token_for_service(
                    user_id=user_id,
                    target_audience="ingest-api",
                )
                if ingest_token:
                    headers["Authorization"] = f"Bearer {ingest_token}"
                else:
                    logger.warning(
                        "Token exchange failed, attempting without auth",
                        user_id=user_id,
                    )
            
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

