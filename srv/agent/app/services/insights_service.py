"""
Chat Insights Service for Agent API

Manages insights collections in Milvus for storing and retrieving
conversation and task insights with vector embeddings for RAG.

Migrated from search-api to agent-api as insights are agent memories/context.

Supports:
- Chat/conversation insights (original functionality)
- Task insights/memories (new for agent tasks)
- Multiple embedding models via partitions
- Dynamic embedding dimensions from model registry

Embedding Strategy:
- Each embedding model gets its own partition in the collection
- Partition names are derived from model names (e.g., "bge_large_en_v1_5")
- Queries route to the appropriate partition based on the query's embedding model
- Future: Support for Matryoshka embeddings with dimension truncation
"""

import logging
import os
import re
import time
import uuid
from typing import List, Dict, Optional, Any, Tuple
from datetime import datetime, timezone
from pymilvus import Collection, connections, FieldSchema, CollectionSchema, DataType, utility
import httpx

logger = logging.getLogger(__name__)

COLLECTION_NAME = "chat_insights"
TASK_INSIGHTS_COLLECTION = "task_insights"

# Default embedding dimension (can be overridden by model registry)
DEFAULT_EMBEDDING_DIM = 1024


def get_embedding_dimension() -> int:
    """Get embedding dimension from model registry or environment."""
    try:
        from busibox_common.llm import get_registry
        registry = get_registry()
        return registry.get_embedding_dimension("embedding")
    except Exception:
        pass
    
    # Fallback to environment variable
    return int(os.environ.get("EMBEDDING_DIMENSION", DEFAULT_EMBEDDING_DIM))


def model_name_to_partition(model_name: str) -> str:
    """
    Convert model name to valid Milvus partition name.
    
    Milvus partition names must start with letter/underscore and contain only
    letters, numbers, underscores.
    
    Examples:
        "bge-large-en-v1.5" -> "bge_large_en_v1_5"
        "BAAI/bge-large-en-v1.5" -> "baai_bge_large_en_v1_5"
    """
    # Lowercase and replace invalid chars with underscores
    name = model_name.lower()
    name = re.sub(r'[^a-z0-9]', '_', name)
    # Remove consecutive underscores
    name = re.sub(r'_+', '_', name)
    # Remove leading/trailing underscores
    name = name.strip('_')
    # Ensure starts with letter
    if name and not name[0].isalpha():
        name = 'model_' + name
    return name or 'default'


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
        model_name: str = "bge-large-en-v1.5",  # Embedding model used
    ):
        self.id = id
        self.user_id = user_id
        self.content = content
        self.embedding = embedding
        self.conversation_id = conversation_id
        self.analyzed_at = analyzed_at
        self.model_name = model_name
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "userId": self.user_id,
            "content": self.content,
            "embedding": self.embedding,
            "conversationId": self.conversation_id,
            "analyzedAt": self.analyzed_at,
            "modelName": self.model_name,
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
        self.embedding_service_url = config.get("embedding_service_url", "http://10.96.200.206:8002")
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
            logger.info(f"Connected to Milvus for insights at {self.host}:{self.port}")
    
    def disconnect(self):
        """Disconnect from Milvus."""
        if self.connected:
            connections.disconnect(alias="insights")
            self.connected = False
            logger.info("Disconnected from Milvus for insights")
    
    def initialize_collection(self, embedding_dim: Optional[int] = None):
        """
        Initialize the chat_insights collection in Milvus.
        
        Creates the collection with schema if it doesn't exist.
        This should be called during application setup.
        
        Args:
            embedding_dim: Optional embedding dimension override (default: from model registry)
        """
        self.connect()
        
        dim = embedding_dim or get_embedding_dimension()
        
        # Check if collection exists
        if utility.has_collection(COLLECTION_NAME, using="insights"):
            logger.info(f"Collection {COLLECTION_NAME} already exists")
            self.collection = Collection(COLLECTION_NAME, using="insights")
            return
        
        logger.info(f"Creating collection {COLLECTION_NAME} with embedding dimension {dim}")
        
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
                dim=dim,  # Dynamic dimension from model registry
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
            FieldSchema(
                name="modelName",
                dtype=DataType.VARCHAR,
                max_length=100,
                description="Embedding model name (for model migration tracking)",
            ),
        ]
        
        schema = CollectionSchema(
            fields=fields,
            description="Chat conversation insights with embeddings for RAG",
            # Enable dynamic fields for future flexibility
            enable_dynamic_field=True,
        )
        
        self.collection = Collection(
            name=COLLECTION_NAME,
            schema=schema,
            using="insights",
        )
        
        # Create HNSW index on embedding field
        index_params = {
            "index_type": "HNSW",
            "metric_type": "COSINE",  # Cosine similarity (better for semantic search)
            "params": {
                "M": 16,  # Number of connections
                "efConstruction": 200,  # Construction time parameter
            },
        }
        
        self.collection.create_index(
            field_name="embedding",
            index_params=index_params,
        )
        
        logger.info(
            f"Created collection {COLLECTION_NAME}",
            extra={"dimension": dim, "metric": "COSINE"}
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
        
        # Get expected dimension from collection schema
        try:
            schema = self.collection.schema
            embedding_field = next(
                (f for f in schema.fields if f.name == "embedding"), None
            )
            expected_dim = embedding_field.params.get("dim", 1024) if embedding_field else 1024
        except Exception:
            expected_dim = get_embedding_dimension()
        
        # Validate embeddings have correct dimension
        valid_insights = []
        for insight in insights:
            if len(insight.embedding) != expected_dim:
                logger.warning(
                    f"Skipping insight with invalid embedding dimension: "
                    f"got {len(insight.embedding)}, expected {expected_dim}, "
                    f"model: {insight.model_name}"
                )
                continue
            valid_insights.append(insight)
        
        if not valid_insights:
            logger.warning("No valid insights to insert after dimension validation")
            return
        
        # Prepare data for insertion (order must match schema field order)
        data = [
            [i.id for i in valid_insights],  # id
            [i.user_id for i in valid_insights],  # userId
            [i.content for i in valid_insights],  # content
            [i.embedding for i in valid_insights],  # embedding
            [i.conversation_id for i in valid_insights],  # conversationId
            [i.analyzed_at for i in valid_insights],  # analyzedAt
            [i.model_name for i in valid_insights],  # modelName
        ]
        
        self.collection.insert(data)
        logger.info(
            f"Inserted {len(valid_insights)} insights into {COLLECTION_NAME}",
            extra={
                "count": len(valid_insights),
                "model": valid_insights[0].model_name if valid_insights else "unknown",
                "dimension": expected_dim,
            }
        )
    
    async def generate_embedding(
        self,
        text: str,
        user_id: str,
        authorization: Optional[str] = None,
    ) -> List[float]:
        """
        Generate embedding for text using ingest service.
        
        Args:
            text: Text to embed
            user_id: User ID for authorization
            authorization: Bearer token for authorization
            
        Returns:
            Embedding vector (1024 dimensions)
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Note: In production, this should use a service-to-service token
            # For now, pass through the user's authorization if available
            headers = {}
            if authorization:
                headers["Authorization"] = authorization
            
            response = await client.post(
                f"{self.embedding_service_url}/api/embeddings",
                json={"input": text},  # OpenAI-compatible format
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            
            # Parse OpenAI-compatible response format: {"data": [{"embedding": [...]}]}
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
            f"Deleted conversation insights: conversation_id={conversation_id}, user_id={user_id}"
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
        
        logger.info(f"Deleted user insights: user_id={user_id}")
    
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
            logger.error(f"Insights service health check failed: {e}")
            return False

    # =========================================================================
    # Task Insights - Memories for Agent Tasks
    # =========================================================================
    
    def initialize_task_insights_collection(self, embedding_dim: Optional[int] = None):
        """
        Initialize the task_insights collection in Milvus.
        
        Task insights store execution results/memories for agent tasks,
        enabling tasks to avoid duplicates and maintain context.
        
        Args:
            embedding_dim: Optional embedding dimension override (default: from model registry)
        """
        self.connect()
        
        dim = embedding_dim or get_embedding_dimension()
        
        # Check if collection exists
        if utility.has_collection(TASK_INSIGHTS_COLLECTION, using="insights"):
            logger.info(f"Collection {TASK_INSIGHTS_COLLECTION} already exists")
            return
        
        logger.info(f"Creating collection {TASK_INSIGHTS_COLLECTION} with embedding dimension {dim}")
        
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
                name="taskId",
                dtype=DataType.VARCHAR,
                max_length=100,
                description="Task ID this insight belongs to",
            ),
            FieldSchema(
                name="userId",
                dtype=DataType.VARCHAR,
                max_length=100,
                description="User ID who owns this task",
            ),
            FieldSchema(
                name="content",
                dtype=DataType.VARCHAR,
                max_length=10000,
                description="The insight/result content",
            ),
            FieldSchema(
                name="embedding",
                dtype=DataType.FLOAT_VECTOR,
                dim=dim,  # Dynamic dimension from model registry
                description="Vector embedding of the insight",
            ),
            FieldSchema(
                name="executionId",
                dtype=DataType.VARCHAR,
                max_length=100,
                description="Task execution ID that generated this insight",
            ),
            FieldSchema(
                name="createdAt",
                dtype=DataType.INT64,
                description="Unix timestamp when insight was created",
            ),
            FieldSchema(
                name="modelName",
                dtype=DataType.VARCHAR,
                max_length=100,
                description="Embedding model name (for model migration tracking)",
            ),
        ]
        
        schema = CollectionSchema(
            fields=fields,
            description="Task insights/memories with embeddings for deduplication and context",
            enable_dynamic_field=True,
        )
        
        collection = Collection(
            name=TASK_INSIGHTS_COLLECTION,
            schema=schema,
            using="insights",
        )
        
        # Create HNSW index on embedding field
        index_params = {
            "index_type": "HNSW",
            "metric_type": "COSINE",  # Cosine similarity (better for semantic search)
            "params": {
                "M": 16,
                "efConstruction": 200,
            },
        }
        
        collection.create_index(
            field_name="embedding",
            index_params=index_params,
        )
        
        # Load collection into memory
        collection.load()
        
        logger.info(
            f"Collection {TASK_INSIGHTS_COLLECTION} created and loaded",
            extra={"dimension": dim, "metric": "COSINE"}
        )
    
    def _get_task_insights_collection(self) -> Collection:
        """Get or create task insights collection."""
        self.connect()
        
        if not utility.has_collection(TASK_INSIGHTS_COLLECTION, using="insights"):
            self.initialize_task_insights_collection()
        
        return Collection(TASK_INSIGHTS_COLLECTION, using="insights")
    
    async def insert_task_insight(
        self,
        task_id: str,
        user_id: str,
        content: str,
        execution_id: str,
        authorization: Optional[str] = None,
    ) -> str:
        """
        Insert a task insight/memory into Milvus.
        
        Args:
            task_id: Task UUID
            user_id: User ID
            content: Insight content (e.g., summary of results)
            execution_id: Execution UUID that generated this insight
            authorization: Bearer token for embedding generation
            
        Returns:
            Insight ID
        """
        collection = self._get_task_insights_collection()
        
        # Generate embedding for the content
        embedding = await self.generate_embedding(content, user_id, authorization)
        
        # Generate unique ID
        insight_id = str(uuid.uuid4())
        created_at = int(time.time())
        
        # Insert into collection
        data = [
            [insight_id],  # id
            [task_id],  # taskId
            [user_id],  # userId
            [content[:10000]],  # content (truncate if needed)
            [embedding],  # embedding
            [execution_id],  # executionId
            [created_at],  # createdAt
        ]
        
        collection.insert(data)
        
        logger.info(
            f"Inserted task insight: task_id={task_id}, insight_id={insight_id}"
        )
        
        return insight_id
    
    async def search_task_insights(
        self,
        task_id: str,
        query: str,
        user_id: str,
        authorization: Optional[str] = None,
        limit: int = 10,
        score_threshold: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant task insights.
        
        Args:
            task_id: Task UUID to search within
            query: Search query text
            user_id: User ID for authorization
            authorization: Bearer token for embedding generation
            limit: Maximum number of results
            score_threshold: Maximum L2 distance (lower is better)
            
        Returns:
            List of relevant insights with scores
        """
        collection = self._get_task_insights_collection()
        
        # Generate embedding for query
        query_embedding = await self.generate_embedding(query, user_id, authorization)
        
        # Search with task filter
        search_params = {
            "metric_type": "L2",
            "params": {"nprobe": 10},
        }
        
        results = collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=limit,
            expr=f'taskId == "{task_id}" && userId == "{user_id}"',
            output_fields=["id", "taskId", "userId", "content", "executionId", "createdAt"],
        )
        
        insights = []
        for hits in results:
            for hit in hits:
                score = hit.distance
                
                # Filter by score threshold
                if score > score_threshold:
                    continue
                
                entity = hit.entity
                insights.append({
                    "id": entity.get("id"),
                    "taskId": entity.get("taskId"),
                    "userId": entity.get("userId"),
                    "content": entity.get("content"),
                    "executionId": entity.get("executionId"),
                    "createdAt": datetime.fromtimestamp(
                        entity.get("createdAt", 0)
                    ).isoformat() if entity.get("createdAt") else None,
                    "score": score,
                })
        
        return insights
    
    def get_task_insights(
        self,
        task_id: str,
        user_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get all insights for a task (no semantic search).
        
        Args:
            task_id: Task UUID
            user_id: User ID for authorization
            limit: Maximum number of results
            
        Returns:
            List of insights ordered by creation time (newest first)
        """
        collection = self._get_task_insights_collection()
        
        results = collection.query(
            expr=f'taskId == "{task_id}" && userId == "{user_id}"',
            output_fields=["id", "taskId", "userId", "content", "executionId", "createdAt"],
            limit=limit,
        )
        
        # Sort by createdAt descending
        results.sort(key=lambda x: x.get("createdAt", 0), reverse=True)
        
        insights = []
        for r in results:
            insights.append({
                "id": r.get("id"),
                "taskId": r.get("taskId"),
                "userId": r.get("userId"),
                "content": r.get("content"),
                "executionId": r.get("executionId"),
                "createdAt": datetime.fromtimestamp(
                    r.get("createdAt", 0)
                ).isoformat() if r.get("createdAt") else None,
            })
        
        return insights
    
    def get_task_insight_count(self, task_id: str, user_id: str) -> int:
        """Get insight count for a task."""
        collection = self._get_task_insights_collection()
        
        results = collection.query(
            expr=f'taskId == "{task_id}" && userId == "{user_id}"',
            output_fields=["id"],
        )
        
        return len(results)
    
    def delete_task_insights(self, task_id: str, user_id: str):
        """
        Delete all insights for a task.
        
        Args:
            task_id: Task UUID
            user_id: User ID for authorization
        """
        collection = self._get_task_insights_collection()
        
        expr = f'taskId == "{task_id}" && userId == "{user_id}"'
        collection.delete(expr)
        
        logger.info(f"Deleted task insights: task_id={task_id}, user_id={user_id}")
    
    def purge_old_task_insights(
        self,
        task_id: str,
        user_id: str,
        keep_count: int = 50,
    ) -> int:
        """
        Purge old insights for a task, keeping only the most recent.
        
        Args:
            task_id: Task UUID
            user_id: User ID for authorization
            keep_count: Number of recent insights to keep
            
        Returns:
            Number of insights deleted
        """
        collection = self._get_task_insights_collection()
        
        # Get all insights for the task
        results = collection.query(
            expr=f'taskId == "{task_id}" && userId == "{user_id}"',
            output_fields=["id", "createdAt"],
        )
        
        if len(results) <= keep_count:
            return 0  # Nothing to purge
        
        # Sort by createdAt ascending (oldest first)
        results.sort(key=lambda x: x.get("createdAt", 0))
        
        # Get IDs to delete (oldest ones beyond keep_count)
        delete_count = len(results) - keep_count
        ids_to_delete = [r["id"] for r in results[:delete_count]]
        
        # Delete by IDs
        for insight_id in ids_to_delete:
            collection.delete(f'id == "{insight_id}"')
        
        logger.info(
            f"Purged {delete_count} old insights for task {task_id}, keeping {keep_count}"
        )
        
        return delete_count
    
    async def build_task_context(
        self,
        task_id: str,
        user_id: str,
        query: str,
        authorization: Optional[str] = None,
        context_limit: int = 10,
    ) -> str:
        """
        Build context string from task insights for agent execution.
        
        This retrieves relevant prior results to include in the agent's
        context, helping it avoid duplicates and maintain continuity.
        
        Args:
            task_id: Task UUID
            user_id: User ID
            query: The task query/prompt for semantic search
            authorization: Bearer token for embedding generation
            context_limit: Max insights to include
            
        Returns:
            Formatted context string
        """
        insights = await self.search_task_insights(
            task_id=task_id,
            query=query,
            user_id=user_id,
            authorization=authorization,
            limit=context_limit,
        )
        
        if not insights:
            return ""
        
        context_parts = [
            "## Prior Task Results (avoid duplicating this information):\n"
        ]
        
        for i, insight in enumerate(insights, 1):
            created_at = insight.get("createdAt", "Unknown time")
            content = insight.get("content", "")
            context_parts.append(f"\n### Result {i} ({created_at}):\n{content}\n")
        
        return "\n".join(context_parts)
