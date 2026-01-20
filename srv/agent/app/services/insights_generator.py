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

LLM Usage:
- Uses busibox_common.llm.LiteLLMClient for all LLM calls
- Same client used by agents for DRY code
"""

import asyncio
import json
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
    
    # Valid categories
    CATEGORIES = {"preference", "fact", "goal", "context", "other"}
    
    def __init__(
        self,
        content: str,
        conversation_id: str,
        user_id: str,
        importance: float = 0.5,
        category: str = "other"
    ):
        self.content = content
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.importance = importance
        self.category = category if category in self.CATEGORIES else "other"


def classify_insight_category(content: str) -> str:
    """
    Classify an insight into a category based on content.
    
    Categories:
    - preference: User likes, dislikes, preferences
    - fact: Factual information, definitions, data
    - goal: User goals, objectives, things they want to achieve
    - context: Background information, context about user or situation
    - other: Everything else
    
    Args:
        content: The insight text
        
    Returns:
        Category string
    """
    content_lower = content.lower()
    
    # Preference indicators
    preference_keywords = [
        "prefer", "like", "dislike", "love", "hate", "enjoy", "favorite",
        "rather", "better", "worse", "always use", "never use", "usually",
        "my choice", "i choose", "i pick"
    ]
    if any(kw in content_lower for kw in preference_keywords):
        return "preference"
    
    # Goal indicators
    goal_keywords = [
        "want to", "need to", "goal", "objective", "aim to", "trying to",
        "plan to", "intend to", "hope to", "looking to", "working on",
        "i'm building", "i'm creating", "i'm developing", "my project"
    ]
    if any(kw in content_lower for kw in goal_keywords):
        return "goal"
    
    # Fact indicators (usually from assistant responses)
    fact_keywords = [
        "is defined as", "means", "refers to", "indicates", "represents",
        "the answer is", "the result is", "according to", "based on",
        "technically", "in fact", "actually", "the key is"
    ]
    if any(kw in content_lower for kw in fact_keywords):
        return "fact"
    
    # Context indicators
    context_keywords = [
        "background", "context", "situation", "my company", "my team",
        "we use", "our system", "our project", "currently", "right now",
        "environment", "setup", "configuration", "stack"
    ]
    if any(kw in content_lower for kw in context_keywords):
        return "context"
    
    # Default to "other"
    return "other"


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


def is_similar_to_existing(
    content: str,
    existing_insights: List[Dict[str, Any]],
    similarity_threshold: float = 0.8
) -> bool:
    """
    Check if content is similar to any existing insight.
    
    Uses simple text similarity (Jaccard similarity on words) to detect duplicates.
    For more robust detection, could use embedding similarity.
    
    Args:
        content: New insight content to check
        existing_insights: List of existing insight dicts with 'content' field
        similarity_threshold: Threshold above which content is considered duplicate
        
    Returns:
        True if content is similar to an existing insight
    """
    if not existing_insights:
        return False
    
    # Normalize and tokenize new content
    new_words = set(content.lower().split())
    
    for existing in existing_insights:
        existing_content = existing.get("content", "")
        existing_words = set(existing_content.lower().split())
        
        # Jaccard similarity
        if not new_words or not existing_words:
            continue
        
        intersection = len(new_words & existing_words)
        union = len(new_words | existing_words)
        similarity = intersection / union if union > 0 else 0
        
        if similarity >= similarity_threshold:
            return True
    
    return False


INSIGHT_EXTRACTION_PROMPT = """Analyze this conversation and extract meaningful insights about the user.

IMPORTANT: Only extract TRUE INSIGHTS - not conversation snippets. An insight should be a conclusion or inference about the user, NOT a copy of what they said.

Good insight examples:
- "User is interested in current events and restaurants in Boston - may live there or be planning a visit"
- "User prefers Python for data analysis and has experience with pandas"
- "User is working on a project involving machine learning for customer churn prediction"
- "User values code readability and maintainability over raw performance"

Bad insight examples (these are just conversation snippets, NOT insights):
- "User asked about new restaurants in Boston"
- "What are the best new restaurants in Boston?"
- "I need help with Python"

For each insight, provide:
1. content: A concise insight about the user (1-2 sentences max). Should be a CONCLUSION or INFERENCE, not a quote.
2. category: One of: preference, fact, goal, context, other
   - preference: User likes/dislikes, preferences, habits
   - fact: Factual information about user (job, location, expertise)
   - goal: What user is trying to achieve
   - context: Background info about user's situation/project
   - other: Anything else meaningful

Extract 1-3 QUALITY insights. Quality over quantity. If there's nothing meaningful to extract, return an empty list.

Existing insights (avoid duplicates):
{existing_insights}

Conversation:
{conversation}

Respond with a JSON array of objects with 'content' and 'category' fields. Example:
[{{"content": "User is interested in Italian cuisine and lives in the Boston area", "category": "context"}}]

If no meaningful insights can be extracted, respond with: []"""


async def extract_insights_with_llm(
    conversation_text: str,
    existing_insights: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """
    Use LLM to extract meaningful insights from conversation.
    
    Uses busibox_common.llm.LiteLLMClient for consistent LLM access
    across all services (same client used by agents).
    
    Args:
        conversation_text: Formatted conversation text
        existing_insights: List of existing insights to avoid duplicates
        
    Returns:
        List of dicts with 'content' and 'category' keys
    """
    from busibox_common.llm import get_client
    
    # Format existing insights for the prompt
    existing_str = "\n".join([
        f"- {i.get('content', '')}" 
        for i in existing_insights[:10]  # Limit to avoid huge prompts
    ]) if existing_insights else "None"
    
    prompt = INSIGHT_EXTRACTION_PROMPT.format(
        existing_insights=existing_str,
        conversation=conversation_text[:8000]  # Limit conversation length
    )
    
    content = ""  # Initialize for error handling
    
    try:
        # Use shared LiteLLM client (same as agents use)
        client = get_client()
        
        logger.debug(f"Calling LLM via shared client for insight extraction (base_url={client.base_url})")
        
        # Make the chat completion call (no max_tokens - let the model decide)
        response = await client.chat_completion(
            model="fast",  # Use fast model for efficiency
            messages=[
                {"role": "system", "content": "You are an assistant that extracts meaningful user insights from conversations. Respond only with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,  # Lower temperature for more consistent extraction
        )
        
        # Parse LLM response
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        if not content:
            logger.warning(f"LLM returned empty content. Full response: {response}")
            return []
        
        logger.debug(f"LLM raw response: {content[:200]}...")
        
        # Clean up response - sometimes LLM wraps in markdown
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        # Handle empty array case
        if not content or content == "[]":
            logger.info("LLM returned no insights (empty array)")
            return []
        
        insights = json.loads(content)
        
        # Validate structure
        valid_insights = []
        for insight in insights:
            if isinstance(insight, dict) and "content" in insight:
                valid_insights.append({
                    "content": str(insight.get("content", ""))[:500],  # Limit length
                    "category": str(insight.get("category", "other"))
                })
        
        logger.info(f"LLM extracted {len(valid_insights)} insights")
        return valid_insights[:5]  # Max 5 insights
        
    except json.JSONDecodeError as e:
        logger.warning(f"LLM insight extraction failed to parse JSON: {e}. Content was: {content[:200] if content else 'N/A'}")
        return []
    except httpx.HTTPStatusError as e:
        logger.warning(f"LLM HTTP error: {e.response.status_code} - {e.response.text[:200]}")
        return []
    except Exception as e:
        logger.warning(f"LLM insight extraction failed: {type(e).__name__}: {e}", exc_info=True)
        return []


async def analyze_conversation_for_insights(
    messages: List[Message],
    conversation_id: str,
    user_id: str,
    existing_insights: Optional[List[Dict[str, Any]]] = None,
) -> List[ConversationInsight]:
    """
    Analyze conversation messages to extract insights using LLM.
    
    Uses LLM (via busibox_common.llm.LiteLLMClient) to intelligently extract 
    meaningful insights about the user, not just conversation snippets.
    
    Args:
        messages: List of messages in conversation
        conversation_id: Conversation ID
        user_id: User ID
        existing_insights: Optional list of existing insights to avoid duplicates
        
    Returns:
        List of ConversationInsight
    """
    existing = existing_insights or []
    
    # Skip if conversation is too short
    if len(messages) < 2:
        logger.info(f"Conversation {conversation_id} too short for insight extraction")
        return []
    
    # Format conversation for LLM
    conversation_text = "\n".join([
        f"{msg.role.upper()}: {msg.content[:1000]}"  # Limit each message
        for msg in messages[-20:]  # Last 20 messages max
    ])
    
    # Use LLM via shared client
    llm_insights = await extract_insights_with_llm(
        conversation_text,
        existing,
    )
    
    insights = []
    
    # Process LLM insights
    for llm_insight in llm_insights:
        content = llm_insight.get("content", "").strip()
        category = llm_insight.get("category", "other")
        
        # Skip empty or too short
        if len(content) < 10:
            continue
        
        # Skip if similar to existing
        if is_similar_to_existing(content, existing):
            logger.debug(f"Skipping duplicate LLM insight: {content[:50]}...")
            continue
        
        # Validate category
        if category not in ConversationInsight.CATEGORIES:
            category = classify_insight_category(content)
        
        insight = ConversationInsight(
            content=content,
            conversation_id=conversation_id,
            user_id=user_id,
            importance=0.8,  # LLM insights are generally important
            category=category
        )
        insights.append(insight)
        existing.append({"content": content})
    
    logger.info(
        f"Extracted {len(insights)} new insights from conversation {conversation_id} via LLM",
        extra={
            "conversation_id": conversation_id, 
            "user_id": user_id, 
            "insight_count": len(insights), 
            "existing_count": len(existing_insights or [])
        }
    )
    
    return insights


async def generate_and_store_insights(
    conversation: Conversation,
    messages: List[Message],
    insights_service: InsightsService,
    embedding_service_url: str,
    authorization: Optional[str] = None
) -> Tuple[int, int]:
    """
    Generate insights from conversation and store in Milvus.
    
    Fetches existing insights for the conversation first to avoid duplicates.
    
    Args:
        conversation: Conversation object
        messages: List of messages in conversation
        insights_service: Insights service instance
        embedding_service_url: URL of embedding service
        authorization: Optional authorization header
        
    Returns:
        Tuple of (number of new insights stored, number of existing insights)
    """
    try:
        # Get existing insights for this conversation to avoid duplicates
        existing_insights = insights_service.get_conversation_insights(
            str(conversation.id),
            conversation.user_id
        )
        existing_count = len(existing_insights)
        
        logger.info(
            f"Found {existing_count} existing insights for conversation {conversation.id}",
            extra={"conversation_id": str(conversation.id), "existing_count": existing_count}
        )
        
        # Analyze conversation, passing existing insights to avoid duplicates
        insights = await analyze_conversation_for_insights(
            messages,
            str(conversation.id),
            conversation.user_id,
            existing_insights=existing_insights
        )
        
        if not insights:
            logger.info(
                f"No new insights extracted from conversation {conversation.id} (had {existing_count} existing)",
                extra={"conversation_id": str(conversation.id), "existing_count": existing_count}
            )
            return 0, existing_count
        
        # Get embeddings for insights
        chat_insights = []
        embedding_model = None
        
        logger.info(f"Getting embeddings for {len(insights)} insights")
        
        for i, insight in enumerate(insights):
            logger.debug(f"Processing insight {i+1}/{len(insights)}: {insight.content[:50]}...")
            
            # Get embedding (returns tuple of embedding, model_name)
            embedding, model_name = await get_embedding(
                insight.content,
                embedding_service_url,
                authorization
            )
            logger.debug(f"Got embedding with dim={len(embedding)}, model={model_name}")
            
            # Track the model used
            if embedding_model is None:
                embedding_model = model_name
            
            if not embedding or len(embedding) == 0:
                logger.warning(
                    f"Failed to get embedding for insight, skipping",
                    extra={"conversation_id": str(conversation.id)}
                )
                continue
            
            # Create ChatInsight with model info and category
            chat_insight = ChatInsight(
                id=str(uuid.uuid4()),
                user_id=insight.user_id,
                content=insight.content,
                embedding=embedding,
                conversation_id=insight.conversation_id,
                analyzed_at=int(datetime.now(timezone.utc).timestamp()),
                model_name=model_name,  # Track which model generated this embedding
                category=insight.category  # Category from extraction
            )
            chat_insights.append(chat_insight)
        
        # Store in Milvus
        if chat_insights:
            insights_service.insert_insights(chat_insights)
            
            logger.info(
                f"Stored {len(chat_insights)} new insights for conversation {conversation.id} (had {existing_count} existing)",
                extra={
                    "conversation_id": str(conversation.id),
                    "user_id": conversation.user_id,
                    "new_insight_count": len(chat_insights),
                    "existing_count": existing_count
                }
            )
        
        return len(chat_insights), existing_count
    
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(
            f"Failed to generate insights for conversation {conversation.id}: {e}\nTraceback:\n{tb}",
            extra={"conversation_id": str(conversation.id)},
        )
        return 0, 0


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

