"""
Chat Insights Service for Search API

Manages the chat_insights collection in Milvus for storing and retrieving
conversation insights with vector embeddings for RAG.

Ported from busibox-app/src/lib/milvus/client.ts
"""

import os
import structlog
from typing import List, Dict, Optional, Any
from datetime import datetime
from pymilvus import Collection, connections, FieldSchema, CollectionSchema, DataType, utility
import httpx

logger = structlog.get_logger()

COLLECTION_NAME = "chat_insights"
DEFAULT_EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIMENSION", "1024"))


class ChatInsight:
    """Chat insight entity structure."""
    
    def __init__(
        self,
        id: str,
        user_id: str,
        content: str,
        embedding: List[float],
        conversation_id: str,
        analyzed_at: int,  # Unix timestamp
    ):
        self.id = id
        self.user_id = user_id
        self.content = content
        self.embedding = embedding
        self.conversation_id = conversation_id
        self.analyzed_at = analyzed_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "userId": self.user_id,
            "content": self.content,
            "embedding": self.embedding,
            "conversationId": self.conversation_id,
            "analyzedAt": self.analyzed_at,
        }


class InsightSearchResult:
    """Search result for chat insights."""
    
    def __init__(
        self,
        id: str,
        user_id: str,
        content: str,
        conversation_id: str,
        analyzed_at: datetime,
        score: float,
    ):
        self.id = id
        self.user_id = user_id
        self.content = content
        self.conversation_id = conversation_id
        self.analyzed_at = analyzed_at
        self.score = score
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "userId": self.user_id,
            "content": self.content,
            "conversationId": self.conversation_id,
            "analyzedAt": self.analyzed_at.isoformat(),
            "score": self.score,
        }


class InsightsService:
    """Service for managing chat insights in Milvus."""
    
    def __init__(self, config: Dict):
        """Initialize insights service."""
        self.config = config
        self.host = config.get("milvus_host", "localhost")
        self.port = config.get("milvus_port", 19530)
        # Use dedicated embedding-api service (no auth required for internal services)
        self.embedding_service_url = config.get("embedding_service_url", "http://embedding-api:8005")
        self.connected = False
        self.collection: Optional[Collection] = None
    
    def connect(self):
        """Connect to Milvus."""
        if not self.connected:
            connections.connect(
                alias="insights",
                host=self.host,
                port=self.port,
            )
            self.connected = True
            logger.info("Connected to Milvus for insights", host=self.host, port=self.port)
    
    def disconnect(self):
        """Disconnect from Milvus."""
        if self.connected:
            connections.disconnect(alias="insights")
            self.connected = False
            logger.info("Disconnected from Milvus for insights")
    
    def initialize_collection(self):
        """
        Initialize the chat_insights collection in Milvus.
        
        Creates the collection with schema if it doesn't exist.
        This should be called during application setup.
        """
        self.connect()
        
        # Check if collection exists
        if utility.has_collection(COLLECTION_NAME, using="insights"):
            logger.info(f"Collection {COLLECTION_NAME} already exists")
            self.collection = Collection(COLLECTION_NAME, using="insights")
            return
        
        # Create collection with schema
        fields = [
            FieldSchema(
                name="id",
                dtype=DataType.VARCHAR,
                is_primary=True,
                max_length=100,
                description="Insight ID",
            ),
            FieldSchema(
                name="userId",
                dtype=DataType.VARCHAR,
                max_length=100,
                description="User ID who owns this insight",
            ),
            FieldSchema(
                name="content",
                dtype=DataType.VARCHAR,
                max_length=5000,
                description="The insight text",
            ),
            FieldSchema(
                name="embedding",
                dtype=DataType.FLOAT_VECTOR,
                dim=DEFAULT_EMBEDDING_DIM,
                description="Vector embedding of the insight",
            ),
            FieldSchema(
                name="conversationId",
                dtype=DataType.VARCHAR,
                max_length=100,
                description="Source conversation ID",
            ),
            FieldSchema(
                name="analyzedAt",
                dtype=DataType.INT64,
                description="Unix timestamp when insight was extracted",
            ),
        ]
        
        schema = CollectionSchema(
            fields=fields,
            description="Chat conversation insights with embeddings for RAG",
        )
        
        self.collection = Collection(
            name=COLLECTION_NAME,
            schema=schema,
            using="insights",
        )
        
        # Create HNSW index on embedding field
        index_params = {
            "index_type": "HNSW",
            "metric_type": "L2",  # Euclidean distance
            "params": {
                "M": 16,  # Number of connections
                "efConstruction": 200,  # Construction time parameter
            },
        }
        
        self.collection.create_index(
            field_name="embedding",
            index_params=index_params,
        )
        
        # Load collection into memory
        self.collection.load()
        
        logger.info(f"Collection {COLLECTION_NAME} created and loaded")
    
    def insert_insights(self, insights: List[ChatInsight]):
        """
        Insert insights into Milvus.
        
        Args:
            insights: List of ChatInsight objects to insert
        """
        if not insights:
            return
        
        self.connect()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        # Prepare data for insertion
        data = [
            [i.id for i in insights],  # id
            [i.user_id for i in insights],  # userId
            [i.content for i in insights],  # content
            [i.embedding for i in insights],  # embedding
            [i.conversation_id for i in insights],  # conversationId
            [i.analyzed_at for i in insights],  # analyzedAt
        ]
        
        self.collection.insert(data)
        logger.info(f"Inserted {len(insights)} insights into {COLLECTION_NAME}")
    
    async def generate_embedding(
        self,
        text: str,
        user_id: str,
        authorization: Optional[str] = None,
    ) -> List[float]:
        """
        Generate embedding for text using dedicated embedding-api service.
        
        Args:
            text: Text to embed
            user_id: User ID (for logging/tracing only - embedding-api doesn't require auth)
            authorization: Bearer token (not used - embedding-api is internal service)
            
        Returns:
            Embedding vector (1024 dimensions)
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            # embedding-api is an internal service that doesn't require auth
            # Uses OpenAI-compatible format: {"input": "text"} -> {"data": [{"embedding": [...]}]}
            response = await client.post(
                f"{self.embedding_service_url}/embed",
                json={"input": text},
            )
            response.raise_for_status()
            data = response.json()
            
            # Parse OpenAI-compatible response format
            embedding_data = data.get("data", [])
            if not embedding_data:
                raise ValueError("No embeddings returned from service")
            
            return embedding_data[0].get("embedding", [])
    
    async def search_insights(
        self,
        query: str,
        user_id: str,
        authorization: Optional[str] = None,
        limit: int = 3,
        score_threshold: float = 0.7,
    ) -> List[InsightSearchResult]:
        """
        Search for relevant insights based on query.
        
        Args:
            query: Search query text
            user_id: User ID to filter results
            authorization: Bearer token for authorization
            limit: Maximum number of results
            score_threshold: Maximum L2 distance threshold (lower is better)
            
        Returns:
            List of relevant insights with scores
        """
        self.connect()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        # Generate embedding for query
        query_embedding = await self.generate_embedding(query, user_id, authorization)
        
        # Search with user filter
        search_params = {
            "metric_type": "L2",
            "params": {"nprobe": 10},
        }
        
        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=limit,
            expr=f'userId == "{user_id}"',
            output_fields=["id", "userId", "content", "conversationId", "analyzedAt"],
        )
        
        # Parse and filter results
        insights = []
        
        for hits in results:
            for hit in hits:
                score = hit.distance
                
                # Filter by score threshold (lower is better for L2 distance)
                if score > score_threshold:
                    continue
                
                # Extract fields
                entity = hit.entity
                insight_id = entity.get("id")
                result_user_id = entity.get("userId")
                content = entity.get("content")
                conversation_id = entity.get("conversationId", "")
                analyzed_at = entity.get("analyzedAt", 0)
                
                # Filter by userId (double-check in case expr didn't work)
                if result_user_id != user_id:
                    continue
                
                if not insight_id or not content:
                    continue  # Skip invalid results
                
                insights.append(
                    InsightSearchResult(
                        id=str(insight_id),
                        user_id=str(result_user_id),
                        content=str(content),
                        conversation_id=str(conversation_id),
                        analyzed_at=datetime.fromtimestamp(analyzed_at) if analyzed_at else datetime.now(),
                        score=score,
                    )
                )
        
        return insights
    
    def delete_conversation_insights(self, conversation_id: str, user_id: str):
        """
        Delete insights for a conversation.
        
        Args:
            conversation_id: Conversation ID
            user_id: User ID (for authorization)
        """
        self.connect()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        expr = f'conversationId == "{conversation_id}" && userId == "{user_id}"'
        self.collection.delete(expr)
        
        logger.info(
            "Deleted conversation insights",
            conversation_id=conversation_id,
            user_id=user_id,
        )
    
    def delete_user_insights(self, user_id: str):
        """
        Delete all insights for a user (for account deletion/cleanup).
        
        Args:
            user_id: User ID
        """
        self.connect()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        expr = f'userId == "{user_id}"'
        self.collection.delete(expr)
        
        logger.info("Deleted user insights", user_id=user_id)
    
    def get_user_insight_count(self, user_id: str) -> int:
        """
        Get insight count for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            Number of insights
        """
        self.connect()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        results = self.collection.query(
            expr=f'userId == "{user_id}"',
            output_fields=["id"],
        )
        
        return len(results)
    
    def flush_collection(self):
        """
        Flush collection to ensure data persistence.
        
        Call this after batch inserts for data durability.
        """
        self.connect()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        self.collection.flush()
        logger.info(f"Flushed collection {COLLECTION_NAME}")
    
    def get_collection_stats(self) -> Dict[str, Any]:
        """Get collection statistics."""
        self.connect()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        stats = self.collection.num_entities
        
        return {
            "rowCount": stats,
            "collectionName": COLLECTION_NAME,
        }
    
    def check_health(self) -> bool:
        """Health check for Milvus connection."""
        try:
            self.connect()
            # Try to list collections as a health check
            collections = utility.list_collections(using="insights")
            return True
        except Exception as e:
            logger.error("Insights service health check failed", error=str(e))
            return False

