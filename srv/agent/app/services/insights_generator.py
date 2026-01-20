"""
Insights generation service for extracting learnings from conversations.

Analyzes conversations to extract:
- Key facts and information
- User preferences
- Important decisions
- Context for future interactions

Embedding Configuration:
- Embedding model and dimension come from model registry
- Supports multiple models via partitioned Milvus collections
- Future: Matryoshka embeddings for dimension flexibility
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.models.domain import Conversation, Message
from app.services.insights_service import InsightsService, ChatInsight

logger = logging.getLogger(__name__)


def get_embedding_config() -> Tuple[str, int]:
    """
    Get embedding model name and dimension from model registry or environment.
    
    Returns:
        Tuple of (model_name, dimension)
    """
    # Try to load from model registry
    try:
        from busibox_common.llm import get_registry
        registry = get_registry()
        config = registry.get_embedding_config("embedding")
        model_name = config.get("model_name", config.get("model", "bge-large-en-v1.5"))
        dimension = config.get("dimension", 1024)
        return model_name, dimension
    except Exception as e:
        logger.warning(f"Could not load embedding config from registry: {e}")
    
    # Fallback to environment variables
    model_name = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-large-en-v1.5")
    
    # Determine dimension from model name
    if "large" in model_name.lower():
        dimension = 1024
    elif "base" in model_name.lower():
        dimension = 768
    elif "small" in model_name.lower():
        dimension = 384
    else:
        dimension = 1024  # Safe default
    
    return model_name, dimension


# Get embedding config at module load
EMBEDDING_MODEL, EMBEDDING_DIMENSION = get_embedding_config()


class ConversationInsight:
    """Insight extracted from conversation."""
    
    def __init__(
        self,
        content: str,
        conversation_id: str,
        user_id: str,
        importance: float = 0.5
    ):
        self.content = content
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.importance = importance


async def get_embedding(
    text: str, 
    embedding_service_url: str, 
    authorization: Optional[str] = None,
    expected_dim: Optional[int] = None
) -> Tuple[List[float], str]:
    """
    Get embedding for text from ingest API.
    
    Uses the OpenAI-compatible /api/embeddings endpoint.
    
    Args:
        text: Text to embed
        embedding_service_url: URL of embedding service (e.g., http://ingest-api:8002)
        authorization: Optional authorization header
        expected_dim: Expected embedding dimension (defaults to EMBEDDING_DIMENSION)
        
    Returns:
        Tuple of (embedding vector, model_name)
    """
    dim = expected_dim or EMBEDDING_DIMENSION
    
    try:
        headers = {}
        if authorization:
            headers["Authorization"] = authorization
        
        # Remove trailing slash to avoid double slashes
        base_url = embedding_service_url.rstrip('/')
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Use the OpenAI-compatible /api/embeddings endpoint
            response = await client.post(
                f"{base_url}/api/embeddings",
                json={"input": text},  # OpenAI-compatible format
                headers=headers
            )
            response.raise_for_status()
            data = response.json()
            
            # Parse OpenAI-compatible response format
            # Response: {"data": [{"embedding": [...], "index": 0}], "model": "bge-large-en-v1.5", ...}
            model_name = data.get("model", EMBEDDING_MODEL)
            embeddings_data = data.get("data", [])
            
            if embeddings_data and len(embeddings_data) > 0:
                embedding = embeddings_data[0].get("embedding", [])
                actual_dim = len(embedding)
                
                # Log if dimension doesn't match expected (but don't fail - use actual)
                if actual_dim != dim:
                    logger.info(
                        f"Embedding dimension {actual_dim} differs from expected {dim}. "
                        f"Model: {model_name}. Using actual dimension."
                    )
                
                return embedding, model_name
            
            logger.warning("No embeddings in response, using zero vector fallback")
            return [0.0] * dim, EMBEDDING_MODEL
    
    except Exception as e:
        logger.error(f"Failed to get embedding: {e}", exc_info=True)
        # Return zero vector as fallback
        return [0.0] * dim, EMBEDDING_MODEL


async def analyze_conversation_for_insights(
    messages: List[Message],
    conversation_id: str,
    user_id: str
) -> List[ConversationInsight]:
    """
    Analyze conversation messages to extract insights.
    
    This is a simple heuristic-based approach. In production, you might use:
    - LLM to extract key learnings
    - Named entity recognition
    - Sentiment analysis
    - Topic modeling
    
    Args:
        messages: List of messages in conversation
        conversation_id: Conversation ID
        user_id: User ID
        
    Returns:
        List of ConversationInsight
    """
    insights = []
    
    # Extract insights from user messages (preferences, questions, context)
    user_messages = [msg for msg in messages if msg.role == "user"]
    
    for msg in user_messages:
        content = msg.content.strip()
        
        # Skip very short messages
        if len(content) < 20:
            continue
        
        # Heuristics for important insights
        importance = 0.5
        
        # Questions indicate learning opportunities
        if "?" in content:
            importance += 0.1
        
        # Longer messages often contain more context
        if len(content) > 100:
            importance += 0.1
        
        # Keywords indicating preferences or important info
        preference_keywords = ["prefer", "like", "want", "need", "always", "never", "usually"]
        if any(keyword in content.lower() for keyword in preference_keywords):
            importance += 0.2
        
        # Create insight if important enough
        if importance >= 0.6:
            insights.append(
                ConversationInsight(
                    content=content,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    importance=min(importance, 1.0)
                )
            )
    
    # Extract insights from assistant messages (facts, answers, solutions)
    assistant_messages = [msg for msg in messages if msg.role == "assistant"]
    
    for msg in assistant_messages:
        content = msg.content.strip()
        
        # Skip very short messages
        if len(content) < 50:
            continue
        
        # Extract key facts (simplified - in production use NER or LLM)
        # For now, extract sentences with certain patterns
        sentences = content.split(". ")
        
        for sentence in sentences:
            sentence = sentence.strip()
            
            # Skip short sentences
            if len(sentence) < 30:
                continue
            
            # Look for factual statements
            fact_indicators = ["is", "are", "was", "were", "means", "refers to", "indicates"]
            if any(indicator in sentence.lower() for indicator in fact_indicators):
                insights.append(
                    ConversationInsight(
                        content=sentence,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        importance=0.7
                    )
                )
                
                # Limit insights per message
                if len([i for i in insights if i.content in content]) >= 2:
                    break
    
    # Limit total insights
    insights = sorted(insights, key=lambda x: x.importance, reverse=True)[:10]
    
    logger.info(
        f"Extracted {len(insights)} insights from conversation {conversation_id}",
        extra={"conversation_id": conversation_id, "user_id": user_id, "insight_count": len(insights)}
    )
    
    return insights


async def generate_and_store_insights(
    conversation: Conversation,
    messages: List[Message],
    insights_service: InsightsService,
    embedding_service_url: str,
    authorization: Optional[str] = None
) -> int:
    """
    Generate insights from conversation and store in Milvus.
    
    Args:
        conversation: Conversation object
        messages: List of messages in conversation
        insights_service: Insights service instance
        embedding_service_url: URL of embedding service
        authorization: Optional authorization header
        
    Returns:
        Number of insights generated and stored
    """
    try:
        # Analyze conversation
        insights = await analyze_conversation_for_insights(
            messages,
            str(conversation.id),
            conversation.user_id
        )
        
        if not insights:
            logger.info(
                f"No insights extracted from conversation {conversation.id}",
                extra={"conversation_id": str(conversation.id)}
            )
            return 0
        
        # Get embeddings for insights
        chat_insights = []
        embedding_model = None
        
        for insight in insights:
            # Get embedding (returns tuple of embedding, model_name)
            embedding, model_name = await get_embedding(
                insight.content,
                embedding_service_url,
                authorization
            )
            
            # Track the model used
            if embedding_model is None:
                embedding_model = model_name
            
            if not embedding or len(embedding) == 0:
                logger.warning(
                    f"Failed to get embedding for insight, skipping",
                    extra={"conversation_id": str(conversation.id)}
                )
                continue
            
            # Create ChatInsight with model info
            chat_insight = ChatInsight(
                id=str(uuid.uuid4()),
                user_id=insight.user_id,
                content=insight.content,
                embedding=embedding,
                conversation_id=insight.conversation_id,
                analyzed_at=int(datetime.now(timezone.utc).timestamp()),
                model_name=model_name  # Track which model generated this embedding
            )
            chat_insights.append(chat_insight)
        
        # Store in Milvus
        if chat_insights:
            insights_service.insert_insights(chat_insights)
            
            logger.info(
                f"Stored {len(chat_insights)} insights for conversation {conversation.id}",
                extra={
                    "conversation_id": str(conversation.id),
                    "user_id": conversation.user_id,
                    "insight_count": len(chat_insights)
                }
            )
        
        return len(chat_insights)
    
    except Exception as e:
        logger.error(
            f"Failed to generate insights for conversation {conversation.id}: {e}",
            extra={"conversation_id": str(conversation.id)},
            exc_info=True
        )
        return 0


async def generate_insights_for_conversation(
    conversation_id: str,
    user_id: str,
    insights_service: InsightsService,
    embedding_service_url: str,
    authorization: Optional[str] = None
) -> int:
    """
    Generate insights for a specific conversation (async task).
    
    This can be called after a conversation is complete or periodically.
    
    Args:
        conversation_id: Conversation ID
        user_id: User ID
        insights_service: Insights service instance
        embedding_service_url: URL of embedding service
        authorization: Optional authorization header
        
    Returns:
        Number of insights generated
    """
    # This would typically fetch the conversation and messages from the database
    # For now, this is a placeholder that would be called from a background task
    
    logger.info(
        f"Generating insights for conversation {conversation_id}",
        extra={"conversation_id": conversation_id, "user_id": user_id}
    )
    
    # TODO: Implement background task to fetch conversation and generate insights
    return 0


def should_generate_insights(conversation: Conversation, message_count: int) -> bool:
    """
    Determine if insights should be generated for a conversation.
    
    Args:
        conversation: Conversation object
        message_count: Number of messages in conversation
        
    Returns:
        True if insights should be generated
    """
    # Generate insights if:
    # 1. Conversation has at least 4 messages (2 exchanges)
    # 2. Conversation is at least 5 minutes old (not too fresh)
    # 3. Not generated too recently (TODO: track last generation time)
    
    if message_count < 4:
        return False
    
    # Check conversation age
    age_minutes = (datetime.now(timezone.utc).replace(tzinfo=None) - conversation.created_at).total_seconds() / 60
    if age_minutes < 5:
        return False
    
    return True

