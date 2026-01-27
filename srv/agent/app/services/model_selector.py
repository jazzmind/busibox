"""
Model selection service for intelligent model routing.

Provides logic for selecting the best model based on:
- Message content and complexity
- Attachments (vision requirements)
- Tool requirements
- User preferences
- Performance/cost tradeoffs
"""

import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ModelCapabilities(BaseModel):
    """Model capabilities definition."""
    id: str
    name: str
    description: str
    supports_vision: bool = False
    supports_tools: bool = False
    supports_reasoning: bool = False
    max_tokens: int = 4096
    cost_tier: str = "low"  # low, medium, high
    speed_tier: str = "fast"  # fast, medium, slow


class ModelSelectionResult(BaseModel):
    """Result of model selection."""
    model_id: str
    model_name: str
    reasoning: str
    confidence: float  # 0-1
    capabilities_used: List[str]


# Available models with their capabilities
AVAILABLE_MODELS = {
    "test": ModelCapabilities(
        id="test",
        name="Test Model",
        description="Tiny model for validation tests (Qwen3-0.6B)",
        supports_vision=False,
        supports_tools=False,
        supports_reasoning=True,  # Qwen3 has <think> reasoning
        max_tokens=512,
        cost_tier="low",
        speed_tier="fast"
    ),
    "chat": ModelCapabilities(
        id="chat",
        name="Chat Model",
        description="Fast, efficient model for general conversation",
        supports_vision=False,
        supports_tools=False,
        supports_reasoning=False,
        max_tokens=4096,
        cost_tier="low",
        speed_tier="fast"
    ),
    "research": ModelCapabilities(
        id="research",
        name="Research Model",
        description="Powerful model for research and analysis with tool support",
        supports_vision=False,
        supports_tools=True,
        supports_reasoning=True,
        max_tokens=8192,
        cost_tier="medium",
        speed_tier="medium"
    ),
    "frontier": ModelCapabilities(
        id="frontier",
        name="Frontier Model",
        description="Most capable model with vision and tool support (Claude via AWS)",
        supports_vision=True,
        supports_tools=True,
        supports_reasoning=True,
        max_tokens=16384,
        cost_tier="high",
        speed_tier="slow"
    ),
}


def has_image_attachments(attachments: List[Dict[str, Any]]) -> bool:
    """Check if attachments contain images."""
    if not attachments:
        return False
    
    image_types = ["image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp"]
    return any(att.get("type", "").lower() in image_types for att in attachments)


def detect_web_search_intent(message: str) -> bool:
    """
    Detect if message requires web search.
    
    Looks for:
    - Current events keywords
    - "search", "find", "look up" phrases
    - Questions about recent information
    - URLs or website mentions
    """
    message_lower = message.lower()
    
    # Current events indicators
    current_events = ["latest", "recent", "current", "today", "this week", "news", "update"]
    if any(keyword in message_lower for keyword in current_events):
        return True
    
    # Search intent indicators
    search_phrases = ["search for", "find out", "look up", "what is", "who is", "where is"]
    if any(phrase in message_lower for phrase in search_phrases):
        return True
    
    # URL or website mentions
    if re.search(r'https?://|www\.|\.(com|org|net|edu)', message_lower):
        return True
    
    return False


def detect_doc_search_intent(message: str, history: List[Dict[str, Any]]) -> bool:
    """
    Detect if message requires document search.
    
    Looks for:
    - References to "documents", "files", "my documents"
    - Questions about previously uploaded content
    - Context from conversation history about documents
    - References to reports, data, analysis
    """
    message_lower = message.lower()
    
    # Document-related keywords
    doc_keywords = [
        "document", "file", "pdf", "my documents", "uploaded",
        "in the document", "from the file", "according to",
        "report", "data", "spreadsheet", "presentation"
    ]
    if any(keyword in message_lower for keyword in doc_keywords):
        return True
    
    # Check if recent history mentions documents
    if history:
        recent_messages = history[-5:]  # Last 5 messages
        for msg in recent_messages:
            content = msg.get("content", "").lower()
            if any(keyword in content for keyword in doc_keywords):
                return True
    
    return False


def needs_complex_reasoning(message: str) -> bool:
    """
    Detect if message requires complex reasoning.
    
    Looks for:
    - Multi-step questions
    - Analysis requests
    - Comparison requests
    - Complex problem-solving
    """
    message_lower = message.lower()
    
    reasoning_indicators = [
        "analyze", "compare", "evaluate", "explain why", "how does",
        "what are the implications", "pros and cons", "trade-offs",
        "step by step", "break down", "in detail"
    ]
    
    return any(indicator in message_lower for indicator in reasoning_indicators)


def select_model_and_tools(
    message: str,
    attachments: List[Dict[str, Any]],
    history: List[Dict[str, Any]],
    user_model_preference: Optional[str] = None,
    enabled_tools: Optional[List[str]] = None,
) -> ModelSelectionResult:
    """
    Select the best model and tools for a given message.
    
    Args:
        message: User message text
        attachments: List of attachment metadata
        history: Conversation history
        user_model_preference: User's preferred model (if any)
        enabled_tools: List of enabled tool names
        
    Returns:
        ModelSelectionResult with selected model and reasoning
    """
    # If user has explicit preference (not "auto"), use it
    if user_model_preference and user_model_preference != "auto":
        if user_model_preference in AVAILABLE_MODELS:
            model = AVAILABLE_MODELS[user_model_preference]
            return ModelSelectionResult(
                model_id=model.id,
                model_name=model.name,
                reasoning=f"Using user-selected model: {model.name}",
                confidence=1.0,
                capabilities_used=[]
            )
    
    # Analyze requirements
    needs_vision = has_image_attachments(attachments)
    needs_web_search = detect_web_search_intent(message)
    needs_doc_search = detect_doc_search_intent(message, history)
    needs_reasoning = needs_complex_reasoning(message)
    
    # Check if any tools are needed and enabled
    needs_tools = False
    if enabled_tools:
        if needs_web_search and "web_search" in enabled_tools:
            needs_tools = True
        if needs_doc_search and "doc_search" in enabled_tools:
            needs_tools = True
    
    capabilities_used = []
    reasoning_parts = []
    
    # Select model based on requirements
    selected_model = None
    confidence = 0.8  # Default confidence
    
    if needs_vision:
        # Vision requires frontier model
        selected_model = AVAILABLE_MODELS["frontier"]
        capabilities_used.append("vision")
        reasoning_parts.append("image attachments detected")
        confidence = 0.95
    elif needs_tools and needs_reasoning:
        # Complex task with tools - use research or frontier
        if needs_web_search or needs_doc_search:
            selected_model = AVAILABLE_MODELS["research"]
            capabilities_used.extend(["tools", "reasoning"])
            reasoning_parts.append("complex analysis with tool support needed")
            confidence = 0.9
        else:
            selected_model = AVAILABLE_MODELS["research"]
            capabilities_used.append("reasoning")
            reasoning_parts.append("complex reasoning required")
            confidence = 0.85
    elif needs_tools:
        # Tools needed but not complex reasoning
        selected_model = AVAILABLE_MODELS["research"]
        capabilities_used.append("tools")
        if needs_web_search:
            reasoning_parts.append("web search needed")
        if needs_doc_search:
            reasoning_parts.append("document search needed")
        confidence = 0.85
    elif needs_reasoning:
        # Complex reasoning without tools
        selected_model = AVAILABLE_MODELS["research"]
        capabilities_used.append("reasoning")
        reasoning_parts.append("complex analysis required")
        confidence = 0.8
    else:
        # Simple conversation
        selected_model = AVAILABLE_MODELS["chat"]
        reasoning_parts.append("general conversation")
        confidence = 0.9
    
    # Build reasoning string
    if reasoning_parts:
        reasoning = f"Selected {selected_model.name} because: {', '.join(reasoning_parts)}"
    else:
        reasoning = f"Selected {selected_model.name} for general conversation"
    
    logger.info(
        f"Model selection: {selected_model.id}",
        extra={
            "model_id": selected_model.id,
            "confidence": confidence,
            "needs_vision": needs_vision,
            "needs_tools": needs_tools,
            "needs_reasoning": needs_reasoning,
            "capabilities_used": capabilities_used
        }
    )
    
    return ModelSelectionResult(
        model_id=selected_model.id,
        model_name=selected_model.name,
        reasoning=reasoning,
        confidence=confidence,
        capabilities_used=capabilities_used
    )


def get_model_capabilities(model_id: str) -> Optional[ModelCapabilities]:
    """Get capabilities for a specific model."""
    return AVAILABLE_MODELS.get(model_id)


def list_available_models() -> List[ModelCapabilities]:
    """List all available models with their capabilities."""
    return list(AVAILABLE_MODELS.values())

