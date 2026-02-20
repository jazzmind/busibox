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
import re
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

# Profile fields that define minimum useful user context for proactive assistance.
PROFILE_FIELDS: Dict[str, Dict[str, Any]] = {
    "location": {
        "description": "Where the user lives or is usually based (city/region/country).",
        "required": True,
    },
    "occupation": {
        "description": "The user's role, work, or primary domain.",
        "required": True,
    },
    "communication_tone": {
        "description": "Preferred assistant tone/style (brief, formal, casual, etc.).",
        "required": True,
    },
    "primary_language": {
        "description": "Primary language for communication.",
        "required": False,
    },
    "timezone": {
        "description": "Timezone or locale to ground dates/times.",
        "required": False,
    },
    "key_interests": {
        "description": "Recurring topics/interests the user cares about.",
        "required": False,
    },
}


class ConversationInsight:
    """Insight extracted from conversation."""
    
    # Valid categories
    CATEGORIES = {"preference", "fact", "goal", "context", "pending_question", "other"}
    
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


def build_session_summary_insight(
    messages: List[Message],
    conversation_id: str,
    user_id: str,
) -> Optional[ConversationInsight]:
    """
    Build a compact session summary insight from recent conversation turns.

    This gives the memory system a durable conversation-level anchor even when
    extracted atomic insights are sparse.
    """
    if not messages:
        return None

    # Use recent user turns only to keep summaries concise and avoid storing
    # assistant verbosity as memory.
    recent = messages[-8:]
    user_points: List[str] = []
    for msg in recent:
        text = (msg.content or "").strip()
        if not text:
            continue
        compact = text[:180]
        if msg.role == "user":
            user_points.append(compact)

    if not user_points:
        return None

    parts = ["Session summary:"]
    parts.append("User topics: " + " | ".join(user_points[:3]))

    return ConversationInsight(
        content="\n".join(parts),
        conversation_id=conversation_id,
        user_id=user_id,
        importance=0.6,
        category="context",
    )


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


def _content_matches_patterns(content_lower: str, patterns: List[str]) -> bool:
    """Return True if any regex pattern matches the lowercased content."""
    for pattern in patterns:
        if re.search(pattern, content_lower):
            return True
    return False


def extract_profile_insights_from_messages(
    messages: List[Message],
    conversation_id: str,
    user_id: str,
    existing_insights: Optional[List[Dict[str, Any]]] = None,
) -> List[ConversationInsight]:
    """
    Deterministically extract profile facts/preferences from user messages.

    This complements LLM extraction so critical profile facts (like location)
    are still captured even when the extraction model is unavailable.
    """
    existing = existing_insights or []
    extracted: List[ConversationInsight] = []
    seen_contents: List[Dict[str, Any]] = list(existing)

    def add_insight(content: str, category: str = "fact", importance: float = 0.85) -> None:
        normalized = content.strip()
        if len(normalized) < 8:
            return
        if is_similar_to_existing(normalized, seen_contents, similarity_threshold=0.74):
            return
        extracted.append(
            ConversationInsight(
                content=normalized,
                conversation_id=conversation_id,
                user_id=user_id,
                importance=importance,
                category=category,
            )
        )
        seen_contents.append({"content": normalized, "category": category})

    for msg in messages[-20:]:
        if msg.role != "user":
            continue
        text = str(msg.content or "").strip()
        if not text:
            continue
        lower = text.lower()

        # Location signals
        location_match = re.search(
            r"\b(?:i am|i'm|im|i live|i(?:'m| am)? based)\s+in\s+([A-Za-z][A-Za-z .,'-]{1,80})",
            text,
            flags=re.IGNORECASE,
        )
        if location_match:
            location = location_match.group(1).strip().rstrip(".!?")
            add_insight(f"User is based in {location}.", category="fact", importance=0.92)

        # Occupation signals
        occupation_match = re.search(
            r"\b(?:i work as|my job is|i am|i'm)\s+(?:an?\s+)?([A-Za-z][A-Za-z \-]{2,60})",
            text,
            flags=re.IGNORECASE,
        )
        if occupation_match and " in " not in occupation_match.group(1).lower():
            role = occupation_match.group(1).strip().rstrip(".!?")
            add_insight(f"User works as {role}.", category="fact", importance=0.9)

        # Tone/style preferences
        if re.search(r"\b(keep it|be|reply|responses?)\s+(brief|concise|short|direct)\b", lower):
            add_insight("User prefers concise and direct assistant responses.", category="preference", importance=0.9)
        if re.search(r"\b(detailed|thorough|step[- ]by[- ]step)\b", lower):
            add_insight("User prefers detailed, step-by-step responses when possible.", category="preference", importance=0.85)

        # Language preference
        language_match = re.search(
            r"\b(?:i speak|prefer)\s+(english|spanish|french|german|italian|portuguese|hindi)\b",
            lower,
        )
        if language_match:
            language = language_match.group(1).capitalize()
            add_insight(f"User's preferred language is {language}.", category="fact", importance=0.85)

        # Timezone/local time preference
        timezone_match = re.search(r"\b(?:timezone|time zone)\s*(?:is|=)?\s*([A-Za-z0-9_+\-/:]{2,20})", text, flags=re.IGNORECASE)
        if timezone_match:
            tz = timezone_match.group(1).strip().rstrip(".!?")
            add_insight(f"User's timezone is {tz}.", category="fact", importance=0.85)

    return extracted


def _should_promote_context_globally(content: str) -> bool:
    """
    Return True only for durable, broadly reusable context.

    This intentionally keeps promotion sparse.
    """
    lower = content.lower()
    if "session summary:" in lower:
        return False
    promotion_signals = [
        r"\buser is based in\b",
        r"\buser works as\b",
        r"\buser prefers\b",
        r"\buser's preferred language\b",
        r"\buser's timezone\b",
    ]
    return _content_matches_patterns(lower, promotion_signals)


def get_profile_completeness(existing_insights: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute profile completeness against PROFILE_FIELDS from existing insights.

    Args:
        existing_insights: Existing insight dicts (usually from InsightsService)

    Returns:
        Dict containing completed_fields, missing_fields, required_missing_fields,
        score (0..1), and required_score (0..1)
    """
    if not existing_insights:
        required_fields = [name for name, meta in PROFILE_FIELDS.items() if meta.get("required")]
        return {
            "completed_fields": [],
            "missing_fields": list(PROFILE_FIELDS.keys()),
            "required_missing_fields": required_fields,
            "score": 0.0,
            "required_score": 0.0,
        }

    text_blobs: List[str] = []
    for insight in existing_insights:
        content = str(insight.get("content", "")).strip()
        category = str(insight.get("category", "")).strip()
        if not content:
            continue
        # pending_question does not count as profile completion.
        if category == "pending_question":
            continue
        text_blobs.append(content.lower())
    corpus = "\n".join(text_blobs)

    detectors: Dict[str, List[str]] = {
        "location": [
            r"\blive in\b",
            r"\bbased in\b",
            r"\bfrom\b",
            r"\bcurrently in\b",
            r"\bi(?:'m| am|m)\s+in\b",
            r"\b(city|state|country|region)\b",
        ],
        "occupation": [
            r"\bi work as\b",
            r"\bi am a\b",
            r"\bmy job\b",
            r"\bengineer\b",
            r"\bdeveloper\b",
            r"\bmanager\b",
            r"\bfounder\b",
            r"\bstudent\b",
            r"\bconsultant\b",
        ],
        "communication_tone": [
            r"\bkeep (it )?(brief|concise|short)\b",
            r"\bbe direct\b",
            r"\bformal\b",
            r"\bcasual\b",
            r"\btone\b",
            r"\bstyle\b",
        ],
        "primary_language": [
            r"\bi speak\b",
            r"\blanguage\b",
            r"\benglish\b",
            r"\bspanish\b",
            r"\bfrench\b",
            r"\bgerman\b",
        ],
        "timezone": [
            r"\btimezone\b",
            r"\btime zone\b",
            r"\best\b",
            r"\bpst\b",
            r"\bcst\b",
            r"\bgmt\b",
            r"\butc\b",
        ],
        "key_interests": [
            r"\binterested in\b",
            r"\bi care about\b",
            r"\bmy focus\b",
            r"\bhobby\b",
            r"\binterests\b",
            r"\bworking on\b",
        ],
    }

    completed_fields: List[str] = []
    missing_fields: List[str] = []
    required_missing_fields: List[str] = []
    for field_name, field_meta in PROFILE_FIELDS.items():
        if _content_matches_patterns(corpus, detectors.get(field_name, [])):
            completed_fields.append(field_name)
        else:
            missing_fields.append(field_name)
            if field_meta.get("required"):
                required_missing_fields.append(field_name)

    total_fields = max(1, len(PROFILE_FIELDS))
    required_fields = [name for name, meta in PROFILE_FIELDS.items() if meta.get("required")]
    required_total = max(1, len(required_fields))

    return {
        "completed_fields": completed_fields,
        "missing_fields": missing_fields,
        "required_missing_fields": required_missing_fields,
        "score": len(completed_fields) / total_fields,
        "required_score": (len(required_fields) - len(required_missing_fields)) / required_total,
    }


async def identify_knowledge_gaps(
    conversation: Conversation,
    messages: List[Message],
    user_id: str,
    existing_insights: Optional[List[Dict[str, Any]]] = None,
) -> Optional[ConversationInsight]:
    """
    Identify missing profile knowledge and generate one follow-up question insight.

    The returned insight uses category `pending_question` so it can be surfaced
    at the end of this chat and asked again at the next chat start if unanswered.
    """
    existing = existing_insights or []
    completeness = get_profile_completeness(existing)
    missing_fields: List[str] = completeness.get("missing_fields", [])
    if not missing_fields:
        return None

    # Avoid creating multiple unresolved follow-up prompts.
    existing_pending = [
        i for i in existing
        if str(i.get("category", "")).strip().lower() == "pending_question"
    ]
    if existing_pending:
        return None

    fallback_questions = {
        "location": "Quick profile question: what city or region are you usually based in?",
        "occupation": "Quick profile question: what kind of work do you do?",
        "communication_tone": "Quick profile question: do you prefer concise, direct replies or more detailed explanations?",
        "primary_language": "Quick profile question: what language should I default to when replying?",
        "timezone": "Quick profile question: what timezone should I assume for dates and scheduling?",
        "key_interests": "Quick profile question: what topics do you most want me to prioritize for you?",
    }
    target_field = missing_fields[0]
    fallback_question = fallback_questions.get(
        target_field,
        "Quick profile question: what’s one thing I should know to better personalize help for you?",
    )

    # Format a compact conversation view for the gap-identification pass.
    conversation_text = "\n".join(
        f"{msg.role.upper()}: {str(msg.content or '')[:350]}"
        for msg in messages[-10:]
    )
    existing_compact = "\n".join(
        f"- [{i.get('category', 'other')}] {str(i.get('content', ''))[:180]}"
        for i in existing[:15]
    ) or "None"

    prompt = (
        "You are selecting ONE follow-up question to improve a user profile for future assistant quality.\n"
        "Return ONLY JSON: {\"target_field\":\"...\", \"question\":\"...\"}\n"
        f"Missing fields: {missing_fields}\n"
        f"Current profile completeness: {round(float(completeness.get('score', 0.0)), 3)}\n"
        "Question requirements:\n"
        "- Ask exactly one natural, concise question.\n"
        "- Must be optional/non-intrusive in tone.\n"
        "- Prefer highest-impact missing field.\n"
        "- Max 160 characters.\n\n"
        f"Existing insights:\n{existing_compact}\n\n"
        f"Recent conversation:\n{conversation_text}"
    )

    question_text = fallback_question
    try:
        from busibox_common.llm import get_client

        client = get_client()
        response = await client.chat_completion(
            model="fast",
            messages=[
                {"role": "system", "content": "You are a strict JSON generator. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        raw = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if raw.startswith("```json"):
            raw = raw[7:]
        if raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
        if raw and not raw.startswith("{"):
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                raw = raw[start:end + 1]
        parsed = json.loads(raw)
        candidate_question = str(parsed.get("question", "")).strip()
        candidate_field = str(parsed.get("target_field", "")).strip()
        if candidate_field in missing_fields and candidate_question:
            question_text = candidate_question[:160]
    except Exception as exc:
        logger.warning("Knowledge-gap question generation fallback: %s", exc)

    # Guard against near-duplicate pending questions across user's memory.
    if is_similar_to_existing(
        question_text,
        [i for i in existing if str(i.get("category", "")) == "pending_question"],
        similarity_threshold=0.75,
    ):
        return None

    return ConversationInsight(
        content=question_text,
        conversation_id=f"pending:{conversation.id}",
        user_id=user_id,
        importance=0.7,
        category="pending_question",
    )


async def get_embedding(
    text: str, 
    embedding_service_url: str, 
    authorization: Optional[str] = None,
    expected_dim: Optional[int] = None
) -> Tuple[List[float], str]:
    """
    Get embedding for text from dedicated embedding-api service.
    
    Uses embedding-api:8005 /embed endpoint (no auth required for internal services).
    
    Args:
        text: Text to embed
        embedding_service_url: URL of embedding service (e.g., http://embedding-api:8005)
        authorization: Not used (embedding-api is internal service, no auth required)
        expected_dim: Expected embedding dimension (defaults to EMBEDDING_DIMENSION)
        
    Returns:
        Tuple of (embedding vector, model_name)
    """
    dim = expected_dim or EMBEDDING_DIMENSION
    
    try:
        # Remove trailing slash to avoid double slashes
        base_url = embedding_service_url.rstrip('/')
        
        async with httpx.AsyncClient(timeout=120.0) as client:  # 2 minutes for embedding generation
            # embedding-api uses /embed endpoint with OpenAI-compatible format
            # No authentication required for internal service
            response = await client.post(
                f"{base_url}/embed",
                json={"input": text},
            )
            response.raise_for_status()
            data = response.json()
            
            # embedding-api returns OpenAI-compatible format:
            # {"data": [{"embedding": [...], "index": 0}], "model": "...", "dimension": ...}
            model_name = data.get("model", EMBEDDING_MODEL)
            embeddings_data = data.get("data", [])
            
            if embeddings_data and len(embeddings_data) > 0:
                embedding = embeddings_data[0].get("embedding", [])
                actual_dim = len(embedding)
                
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


def find_similar_existing_insight(
    content: str,
    existing_insights: List[Dict[str, Any]],
    similarity_threshold: float = 0.72,
) -> Optional[Dict[str, Any]]:
    """
    Return the most similar existing insight candidate for update/merge.
    """
    if not existing_insights:
        return None

    new_words = set(content.lower().split())
    if not new_words:
        return None

    best_match: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for existing in existing_insights:
        existing_content = str(existing.get("content", ""))
        existing_words = set(existing_content.lower().split())
        if not existing_words:
            continue
        intersection = len(new_words & existing_words)
        union = len(new_words | existing_words)
        similarity = intersection / union if union > 0 else 0.0
        if similarity >= similarity_threshold and similarity > best_score:
            best_score = similarity
            best_match = existing
    return best_match


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
        # Also fetch broader user insights for cross-conversation dedup/updates.
        user_existing_insights, _ = insights_service.list_user_insights(
            user_id=conversation.user_id,
            limit=250,
        )
        
        logger.info(
            f"Found {existing_count} existing insights for conversation {conversation.id}",
            extra={"conversation_id": str(conversation.id), "existing_count": existing_count}
        )
        
        # Analyze conversation, passing existing insights to avoid duplicates
        llm_insights = await analyze_conversation_for_insights(
            messages,
            str(conversation.id),
            conversation.user_id,
            existing_insights=user_existing_insights
        )

        # Deterministic extraction for critical user profile fields.
        profile_insights = extract_profile_insights_from_messages(
            messages=messages,
            conversation_id=str(conversation.id),
            user_id=conversation.user_id,
            existing_insights=user_existing_insights,
        )
        # Keep only non-context LLM insights by default. Thread context is handled
        # as a single updatable summary below.
        insights: List[ConversationInsight] = []
        for insight in llm_insights:
            if insight.category == "context" and not _should_promote_context_globally(insight.content):
                continue
            if insight.category == "context" and _should_promote_context_globally(insight.content):
                # Promote sparse durable context to global fact memory.
                insight.category = "fact"
            insights.append(insight)
        insights.extend(profile_insights)

        # Maintain exactly ONE thread-scoped context summary insight per conversation.
        summary = build_session_summary_insight(
            messages=messages,
            conversation_id=str(conversation.id),
            user_id=conversation.user_id,
        )
        
        if not insights:
            logger.info(
                f"No new insights extracted from conversation {conversation.id} (had {existing_count} existing)",
                extra={"conversation_id": str(conversation.id), "existing_count": existing_count}
            )
            # Still allow summary upsert below.

        # Merge/update existing insights when new insights are similar but stronger.
        updated_count = 0
        insights_to_insert: List[ConversationInsight] = []
        for insight in insights:
            existing_match = find_similar_existing_insight(insight.content, user_existing_insights)
            if not existing_match:
                insights_to_insert.append(insight)
                continue

            existing_id = str(existing_match.get("id", ""))
            if not existing_id:
                insights_to_insert.append(insight)
                continue

            existing_content = str(existing_match.get("content", ""))
            existing_category = str(existing_match.get("category", "other"))

            # Replace only when the new insight is materially richer.
            richer_content = len(insight.content) >= len(existing_content) + 15
            better_category = existing_category == "other" and insight.category != "other"
            if richer_content or better_category:
                try:
                    updated = insights_service.update_insight(
                        insight_id=existing_id,
                        user_id=conversation.user_id,
                        content=insight.content,
                        category=insight.category,
                    )
                    if updated:
                        updated_count += 1
                        continue
                except Exception as exc:
                    logger.warning("Failed to update existing insight %s: %s", existing_id, exc)
            insights_to_insert.append(insight)

        # Upsert single thread summary context insight (per conversation).
        if summary is not None:
            existing_thread_summary = next(
                (
                    i for i in existing_insights
                    if str(i.get("conversationId", "")) == str(conversation.id)
                    and str(i.get("category", "")) == "context"
                    and str(i.get("content", "")).startswith("Session summary:")
                ),
                None,
            )
            if existing_thread_summary:
                summary_id = str(existing_thread_summary.get("id", ""))
                if summary_id:
                    try:
                        updated = insights_service.update_insight(
                            insight_id=summary_id,
                            user_id=conversation.user_id,
                            content=summary.content,
                            category="context",
                        )
                        if updated:
                            updated_count += 1
                        else:
                            insights_to_insert.append(summary)
                    except Exception as exc:
                        logger.warning("Failed to update thread summary insight %s: %s", summary_id, exc)
                        insights_to_insert.append(summary)
            else:
                insights_to_insert.append(summary)
        
        # Get embeddings for insights
        chat_insights = []
        embedding_model = None
        
        logger.info(f"Getting embeddings for {len(insights_to_insert)} insights")
        
        for i, insight in enumerate(insights_to_insert):
            logger.debug(f"Processing insight {i+1}/{len(insights_to_insert)}: {insight.content[:50]}...")
            
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
                f"Stored {len(chat_insights)} new insights and updated {updated_count} for conversation {conversation.id} (had {existing_count} existing)",
                extra={
                    "conversation_id": str(conversation.id),
                    "user_id": conversation.user_id,
                    "new_insight_count": len(chat_insights),
                    "updated_insight_count": updated_count,
                    "existing_count": existing_count
                }
            )

        return len(chat_insights), existing_count + updated_count
    
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
    # 1. Conversation has at least 2 messages (1 substantive exchange)
    # 2. Conversation is at least 30 seconds old (avoid immediate race with writes)
    # 3. Not generated too recently (TODO: track last generation time)

    if message_count < 2:
        return False
    
    # Check conversation age
    age_minutes = (datetime.now(timezone.utc).replace(tzinfo=None) - conversation.created_at).total_seconds() / 60
    if age_minutes < 0.5:
        return False
    
    return True

