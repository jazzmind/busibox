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
import math
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
DEFAULT_EMBEDDING_DIM = 768


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
    
    # Valid categories for insights
    VALID_CATEGORIES = {"preference", "fact", "goal", "context", "pending_question", "other"}
    
    def __init__(
        self,
        id: str,
        user_id: str,
        content: str,
        embedding: List[float],
        conversation_id: str,
        analyzed_at: int,  # Unix timestamp
        model_name: str = "bge-large-en-v1.5",  # Embedding model used
        category: str = "other",  # Category: preference, fact, goal, context, pending_question, other
    ):
        self.id = id
        self.user_id = user_id
        self.content = content
        self.embedding = embedding
        self.conversation_id = conversation_id
        self.analyzed_at = analyzed_at
        self.model_name = model_name
        self.category = category if category in self.VALID_CATEGORIES else "other"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "userId": self.user_id,
            "content": self.content,
            "category": self.category,
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
        category: str = "other",
    ):
        self.id = id
        self.user_id = user_id
        self.content = content
        self.conversation_id = conversation_id
        self.analyzed_at = analyzed_at
        self.score = score
        self.category = category
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "userId": self.user_id,
            "content": self.content,
            "conversationId": self.conversation_id,
            "analyzedAt": self.analyzed_at.isoformat(),
            "score": self.score,
            "category": self.category,
        }


class InsightsService:
    """Service for managing chat insights in Milvus."""
    
    def __init__(self, config: Dict):
        """Initialize insights service."""
        self.config = config
        self.host = config.get("milvus_host", "localhost")
        self.port = config.get("milvus_port", 19530)
        # Use dedicated embedding-api service (no auth required for internal services)
        # Strip trailing slashes to avoid double slashes in URLs
        embedding_url = config.get("embedding_service_url", "http://embedding-api:8005")
        self.embedding_service_url = str(embedding_url).rstrip("/")
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
    
    def initialize_collection(self, embedding_dim: Optional[int] = None, force_recreate: bool = False):
        """
        Initialize the chat_insights collection in Milvus.
        
        Creates the collection with schema if it doesn't exist.
        If the collection exists but has an incompatible schema (missing fields),
        it will be dropped and recreated.
        
        Args:
            embedding_dim: Optional embedding dimension override (default: from model registry)
            force_recreate: If True, drop and recreate collection even if it exists
        """
        self.connect()
        
        dim = embedding_dim or get_embedding_dimension()
        
        # Expected fields in the current schema (includes category for filtering)
        EXPECTED_FIELDS = {"id", "userId", "content", "embedding", "conversationId", "analyzedAt", "modelName", "category"}
        
        # Check if collection exists
        if utility.has_collection(COLLECTION_NAME, using="insights"):
            existing_collection = Collection(COLLECTION_NAME, using="insights")
            existing_fields = {field.name for field in existing_collection.schema.fields}
            
            # Check if schema is compatible
            missing_fields = EXPECTED_FIELDS - existing_fields
            
            if missing_fields and not force_recreate:
                logger.warning(
                    f"Collection {COLLECTION_NAME} exists but is missing fields: {missing_fields}. Recreating..."
                )
                force_recreate = True
            
            if force_recreate:
                logger.info(f"Dropping collection {COLLECTION_NAME} for recreation")
                utility.drop_collection(COLLECTION_NAME, using="insights")
            else:
                logger.info(f"Collection {COLLECTION_NAME} already exists with compatible schema")
                self.collection = existing_collection
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
            FieldSchema(
                name="category",
                dtype=DataType.VARCHAR,
                max_length=50,
                description="Insight category: preference, fact, goal, context, pending_question, other",
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
        
        # Expected fields in the current schema
        EXPECTED_FIELDS = {"id", "userId", "content", "embedding", "conversationId", "analyzedAt", "modelName"}
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        # Check if collection schema is compatible (has all expected fields)
        existing_fields = {field.name for field in self.collection.schema.fields}
        missing_fields = EXPECTED_FIELDS - existing_fields
        
        if missing_fields:
            logger.warning(
                f"Collection {COLLECTION_NAME} is missing fields: {missing_fields}. Recreating collection..."
            )
            # Drop and recreate the collection with correct schema
            utility.drop_collection(COLLECTION_NAME, using="insights")
            self.collection = None
            self.initialize_collection()
        
        # Get expected dimension from collection schema
        try:
            schema = self.collection.schema
            embedding_field = next(
                (f for f in schema.fields if f.name == "embedding"), None
            )
            expected_dim = embedding_field.params.get("dim", 768) if embedding_field else 768
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
            [i.category for i in valid_insights],  # category
        ]
        
        self.collection.insert(data)
        
        # Flush to ensure data is persisted and queryable
        self.collection.flush()
        
        # Ensure collection is loaded for queries
        try:
            self.collection.load()
        except Exception as e:
            # Collection might already be loaded
            logger.debug(f"Collection load (may already be loaded): {e}")
        
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
        Generate embedding for text using dedicated embedding-api service.
        
        The dedicated embedding service (embedding-api:8005) does not require authentication.
        
        Args:
            text: Text to embed
            user_id: User ID (for logging/tracing only - embedding-api doesn't require auth)
            authorization: Bearer token (not used - embedding-api is internal service)
            
        Returns:
            Embedding vector
        """
        async with httpx.AsyncClient(timeout=120.0) as client:  # 2 minutes for embedding generation
            # embedding-api uses /embed endpoint with OpenAI-compatible format
            # No authentication required for internal service
            response = await client.post(
                f"{self.embedding_service_url}/embed",
                json={"input": text},
            )
            response.raise_for_status()
            data = response.json()
            
            # embedding-api returns OpenAI-compatible format:
            # {"data": [{"embedding": [...], "index": 0}], "model": "...", "dimension": ...}
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
        apply_temporal_decay: bool = True,
        half_life_days: float = 30.0,
    ) -> List[InsightSearchResult]:
        """
        Search for relevant insights based on query.
        
        Args:
            query: Search query text
            user_id: User ID to filter results
            authorization: Bearer token for authorization
            limit: Maximum number of results
            score_threshold: Maximum cosine distance threshold (lower is better, 0=identical)
            apply_temporal_decay: If True, older insights are downranked
            half_life_days: Recency half-life used for temporal decay
            
        Returns:
            List of relevant insights with scores
        """
        self.connect()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        # Generate embedding for query
        query_embedding = await self.generate_embedding(query, user_id, authorization)
        
        # Search with user filter
        # Note: Index was created with COSINE metric, search must match
        search_params = {
            "metric_type": "COSINE",
            "params": {"nprobe": 10},
        }
        
        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=limit,
            expr=f'userId == "{user_id}"',
            output_fields=["id", "userId", "content", "conversationId", "analyzedAt", "category"],
        )
        
        # Parse and filter results
        insights = []
        
        for hits in results:
            for hit in hits:
                # For COSINE metric, distance is (1 - cosine_similarity)
                # So lower distance = higher similarity (0 = identical, 2 = opposite)
                raw_score = hit.distance
                
                # Filter by score threshold (lower is better for COSINE distance)
                if raw_score > score_threshold:
                    continue
                
                # Extract fields
                entity = hit.entity
                insight_id = entity.get("id")
                result_user_id = entity.get("userId")
                content = entity.get("content")
                conversation_id = entity.get("conversationId", "")
                analyzed_at = entity.get("analyzedAt", 0)
                category = entity.get("category", "other")
                
                # Filter by userId (double-check in case expr didn't work)
                if result_user_id != user_id:
                    continue
                
                if not insight_id or not content:
                    continue  # Skip invalid results
                
                # Apply temporal decay to prefer recent memories.
                analyzed_dt = datetime.fromtimestamp(analyzed_at) if analyzed_at else datetime.now()
                score = raw_score
                if apply_temporal_decay and half_life_days > 0:
                    age_days = max(
                        0.0,
                        (datetime.now() - analyzed_dt).total_seconds() / 86400.0,
                    )
                    decay_lambda = math.log(2) / half_life_days
                    # Lower is better. Increase score for older insights.
                    score = raw_score * math.exp(decay_lambda * age_days)

                insights.append(
                    InsightSearchResult(
                        id=str(insight_id),
                        user_id=str(result_user_id),
                        content=str(content),
                        conversation_id=str(conversation_id),
                        analyzed_at=analyzed_dt,
                        score=score,
                        category=str(category) if category else "other",
                    )
                )
        insights.sort(key=lambda x: x.score)
        return insights[:limit]
    
    def get_conversation_insights(self, conversation_id: str, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all insights for a specific conversation.
        
        Args:
            conversation_id: Conversation ID
            user_id: User ID (for authorization)
            
        Returns:
            List of insight dictionaries with id, content, category, etc.
        """
        self.connect()
        
        # Ensure collection exists (create if not)
        if not utility.has_collection(COLLECTION_NAME, using="insights"):
            logger.info(f"Collection {COLLECTION_NAME} does not exist, creating...")
            self.initialize_collection()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        # Ensure collection is loaded for queries
        try:
            self.collection.load()
        except Exception as e:
            logger.debug(f"Collection load (may already be loaded): {e}")
        
        # Get available fields from schema to handle missing 'category' field gracefully
        available_fields = {field.name for field in self.collection.schema.fields}
        output_fields = ["id", "userId", "content", "conversationId", "analyzedAt", "modelName"]
        if "category" in available_fields:
            output_fields.append("category")
        
        expr = f'conversationId == "{conversation_id}" && userId == "{user_id}"'
        try:
            results = self.collection.query(
                expr=expr,
                output_fields=output_fields,
            )
            # Convert Milvus query result to plain list of dicts to avoid iteration bugs
            plain_results = [dict(r) for r in results] if results else []
        except Exception as e:
            logger.warning(f"Error querying conversation insights: {e}")
            return []
        
        logger.info(f"Found {len(plain_results)} existing insights for conversation {conversation_id}")
        
        return plain_results
    
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
        
        # Ensure collection exists (create if not)
        if not utility.has_collection(COLLECTION_NAME, using="insights"):
            logger.info(f"Collection {COLLECTION_NAME} does not exist, creating...")
            self.initialize_collection()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        # Ensure collection is loaded for queries
        try:
            self.collection.load()
        except Exception as e:
            # Collection might already be loaded
            logger.debug(f"Collection load (may already be loaded): {e}")
        
        results = self.collection.query(
            expr=f'userId == "{user_id}"',
            output_fields=["id"],
        )
        
        logger.info(f"User {user_id} has {len(results)} insights")
        
        return len(results)
    
    def list_user_insights(
        self,
        user_id: str,
        category: Optional[str] = None,
        conversation_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        List insights for a user with pagination and optional category filter.
        
        When conversation_id is provided, thread-scoped categories (goal, context)
        are filtered to that conversation while profile categories remain cross-thread.
        
        Args:
            user_id: User ID
            category: Optional category filter
            conversation_id: Optional conversation ID for thread-scoped filtering
            offset: Number of results to skip
            limit: Maximum number of results to return
            
        Returns:
            Tuple of (list of insights, total count)
        """
        self.connect()
        
        # Ensure collection exists (create if not)
        if not utility.has_collection(COLLECTION_NAME, using="insights"):
            logger.info(f"Collection {COLLECTION_NAME} does not exist, creating...")
            self.initialize_collection()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        # Ensure collection is loaded for queries
        try:
            self.collection.load()
        except Exception as e:
            logger.debug(f"Collection load (may already be loaded): {e}")
        
        # Get available fields from schema
        available_fields = {field.name for field in self.collection.schema.fields}
        has_category = "category" in available_fields
        
        # Build filter expression
        expr_parts = [f'userId == "{user_id}"']
        if category and category in ChatInsight.VALID_CATEGORIES and has_category:
            expr_parts.append(f'category == "{category}"')
        expr = " && ".join(expr_parts)
        
        # Build output fields based on available schema
        output_fields = ["id", "userId", "content", "conversationId", "analyzedAt", "modelName"]
        if has_category:
            output_fields.append("category")
        
        # Query all matching insights (Milvus doesn't support offset/limit well on query)
        try:
            results = self.collection.query(
                expr=expr,
                output_fields=output_fields,
            )
        except Exception as e:
            logger.warning(f"Error querying insights: {e}")
            return [], 0
        
        # Thread-scoped filtering: goal and context belong to specific conversations
        _THREAD_SCOPED = {"goal", "context"}
        if conversation_id:
            results = [
                r for r in results
                if r.get("category") not in _THREAD_SCOPED
                or r.get("conversationId", "") == conversation_id
            ]
        
        total_count = len(results)
        
        # Sort by analyzedAt descending (newest first) and apply pagination
        results.sort(key=lambda x: x.get("analyzedAt", 0), reverse=True)
        paginated_results = results[offset:offset + limit]
        
        logger.info(f"Listed {len(paginated_results)} insights for user {user_id} (total: {total_count}, category: {category}, conversation: {conversation_id})")
        
        return paginated_results, total_count

    def list_insights_by_category(
        self,
        user_id: str,
        category: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        List recent insights for a user filtered to a single category.

        Args:
            user_id: User ID
            category: Insight category (must be in ChatInsight.VALID_CATEGORIES)
            limit: Maximum number of records to return

        Returns:
            List of insight dicts sorted by analyzedAt descending
        """
        if category not in ChatInsight.VALID_CATEGORIES:
            return []

        self.connect()

        if not utility.has_collection(COLLECTION_NAME, using="insights"):
            logger.info(f"Collection {COLLECTION_NAME} does not exist, creating...")
            self.initialize_collection()

        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")

        try:
            self.collection.load()
        except Exception as e:
            logger.debug(f"Collection load (may already be loaded): {e}")

        available_fields = {field.name for field in self.collection.schema.fields}
        if "category" not in available_fields:
            return []

        try:
            results = self.collection.query(
                expr=f'userId == "{user_id}" && category == "{category}"',
                output_fields=["id", "userId", "content", "conversationId", "analyzedAt", "modelName", "category"],
            )
        except Exception as e:
            logger.warning(f"Error querying insights by category: {e}")
            return []

        results.sort(key=lambda x: x.get("analyzedAt", 0), reverse=True)
        return results[: max(1, min(limit, 50))]
    
    def get_category_counts(self, user_id: str) -> Dict[str, int]:
        """
        Get insight counts by category for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            Dictionary mapping category to count
        """
        self.connect()
        
        # Ensure collection exists (create if not)
        if not utility.has_collection(COLLECTION_NAME, using="insights"):
            logger.info(f"Collection {COLLECTION_NAME} does not exist, creating...")
            self.initialize_collection()
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        # Check if category field exists
        available_fields = {field.name for field in self.collection.schema.fields}
        if "category" not in available_fields:
            # No category field - return empty
            return {}
        
        # Ensure collection is loaded for queries
        try:
            self.collection.load()
        except Exception as e:
            logger.debug(f"Collection load (may already be loaded): {e}")
        
        # Query all insights for user with category field
        results = self.collection.query(
            expr=f'userId == "{user_id}"',
            output_fields=["category"],
        )
        
        # Count by category
        counts: Dict[str, int] = {}
        for r in results:
            cat = r.get("category", "other")
            counts[cat] = counts.get(cat, 0) + 1
        
        return counts
    
    def update_insight(
        self,
        insight_id: str,
        user_id: str,
        content: Optional[str] = None,
        category: Optional[str] = None,
    ) -> bool:
        """
        Update an insight's content or category.
        
        Note: Milvus doesn't support direct updates, so we delete and re-insert.
        Embedding is regenerated if content changes.
        
        Args:
            insight_id: Insight ID
            user_id: User ID (for authorization)
            content: New content (optional)
            category: New category (optional)
            
        Returns:
            True if updated, False if not found
        """
        self.connect()
        
        if not utility.has_collection(COLLECTION_NAME, using="insights"):
            return False
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        try:
            self.collection.load()
        except Exception:
            pass
        
        # Get existing insight
        results = self.collection.query(
            expr=f'id == "{insight_id}" && userId == "{user_id}"',
            output_fields=["id", "userId", "content", "conversationId", "analyzedAt", "modelName", "category", "embedding"],
        )
        
        if not results:
            return False
        
        existing = results[0]
        
        # Prepare updated values
        new_content = content if content is not None else existing.get("content", "")
        new_category = category if category is not None else existing.get("category", "other")
        
        # Validate category
        if new_category not in ChatInsight.VALID_CATEGORIES:
            new_category = "other"
        
        # Delete old record
        self.collection.delete(expr=f'id == "{insight_id}"')
        
        # Re-insert with updated values (keep same embedding if content unchanged)
        embedding = existing.get("embedding", [])
        
        data = [
            [existing.get("id")],
            [existing.get("userId")],
            [new_content],
            [embedding],
            [existing.get("conversationId", "")],
            [existing.get("analyzedAt", 0)],
            [existing.get("modelName", "")],
            [new_category],
        ]
        
        self.collection.insert(data)
        self.collection.flush()
        
        logger.info(f"Updated insight: id={insight_id}, user_id={user_id}")
        return True
    
    def delete_insight(self, insight_id: str, user_id: str) -> bool:
        """
        Delete a single insight by ID.
        
        Args:
            insight_id: Insight ID
            user_id: User ID (for authorization)
            
        Returns:
            True if deleted, False if not found
        """
        self.connect()
        
        if not utility.has_collection(COLLECTION_NAME, using="insights"):
            return False
        
        if not self.collection:
            self.collection = Collection(COLLECTION_NAME, using="insights")
        
        try:
            self.collection.load()
        except Exception:
            pass
        
        # Check if insight exists and belongs to user
        results = self.collection.query(
            expr=f'id == "{insight_id}" && userId == "{user_id}"',
            output_fields=["id"],
        )
        
        if not results:
            return False
        
        # Delete
        self.collection.delete(expr=f'id == "{insight_id}"')
        self.collection.flush()
        
        logger.info(f"Deleted insight: id={insight_id}, user_id={user_id}")
        return True
    
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
    
    def initialize_task_insights_collection(self, embedding_dim: Optional[int] = None, force_recreate: bool = False):
        """
        Initialize the task_insights collection in Milvus.
        
        Task insights store execution results/memories for agent tasks,
        enabling tasks to avoid duplicates and maintain context.
        
        If the collection exists but has an incompatible schema (missing fields),
        it will be dropped and recreated.
        
        Args:
            embedding_dim: Optional embedding dimension override (default: from model registry)
            force_recreate: If True, drop and recreate collection even if it exists
        """
        self.connect()
        
        dim = embedding_dim or get_embedding_dimension()
        
        # Expected fields in the current schema
        EXPECTED_FIELDS = {"id", "taskId", "userId", "content", "embedding", "executionId", "createdAt", "modelName"}
        
        # Check if collection exists
        if utility.has_collection(TASK_INSIGHTS_COLLECTION, using="insights"):
            existing_collection = Collection(TASK_INSIGHTS_COLLECTION, using="insights")
            existing_fields = {field.name for field in existing_collection.schema.fields}
            
            # Check if schema is compatible
            missing_fields = EXPECTED_FIELDS - existing_fields
            
            if missing_fields and not force_recreate:
                logger.warning(
                    f"Collection {TASK_INSIGHTS_COLLECTION} exists but is missing fields: {missing_fields}. Recreating..."
                )
                force_recreate = True
            
            if force_recreate:
                logger.info(f"Dropping collection {TASK_INSIGHTS_COLLECTION} for recreation")
                utility.drop_collection(TASK_INSIGHTS_COLLECTION, using="insights")
            else:
                logger.info(f"Collection {TASK_INSIGHTS_COLLECTION} already exists with compatible schema")
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
        
        # Get the model name from config or environment
        model_name = os.environ.get("FASTEMBED_MODEL", "bge-large-en-v1.5")
        
        # Insert into collection (must match schema: id, taskId, userId, content, embedding, executionId, createdAt, modelName)
        data = [
            [insight_id],  # id
            [task_id],  # taskId
            [user_id],  # userId
            [content[:10000]],  # content (truncate if needed)
            [embedding],  # embedding
            [execution_id],  # executionId
            [created_at],  # createdAt
            [model_name],  # modelName
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
            score_threshold: Maximum cosine distance (lower is better, 0=identical)
            
        Returns:
            List of relevant insights with scores
        """
        collection = self._get_task_insights_collection()
        
        # Generate embedding for query
        query_embedding = await self.generate_embedding(query, user_id, authorization)
        
        # Search with task filter
        # Note: Index was created with COSINE metric, search must match
        search_params = {
            "metric_type": "COSINE",
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
                # For COSINE metric, distance is (1 - cosine_similarity)
                score = hit.distance
                
                # Filter by score threshold (lower is better for COSINE distance)
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
