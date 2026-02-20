"""Unit tests for insights generator service."""
import pytest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.insights_generator import (
    ConversationInsight,
    PROFILE_FIELDS,
    _should_promote_context_globally,
    extract_profile_insights_from_messages,
    get_profile_completeness,
    identify_knowledge_gaps,
    get_embedding,
    analyze_conversation_for_insights,
    generate_and_store_insights,
    should_generate_insights,
)
from app.models.domain import Conversation, Message


@pytest.mark.asyncio
async def test_conversation_insight_creation():
    """Test ConversationInsight creation."""
    insight = ConversationInsight(
        content="User prefers Python for data analysis",
        conversation_id="conv-123",
        user_id="user-123",
        importance=0.8
    )
    
    assert insight.content == "User prefers Python for data analysis"
    assert insight.conversation_id == "conv-123"
    assert insight.user_id == "user-123"
    assert insight.importance == 0.8


@pytest.mark.asyncio
async def test_get_embedding_success():
    """Test successful embedding generation."""
    # Mock HTTP client - patch where the module is used
    with patch('app.services.insights_generator.httpx.AsyncClient') as mock_client_class:
        mock_response = MagicMock()
        # Use OpenAI-compatible response format
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}],
            "model": "bge-large-en-v1.5"
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        
        # Make the context manager work
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)
        
        embedding, model_name = await get_embedding(
            "test text",
            "http://localhost:8002",
            "Bearer token"
        )
        
        assert embedding == [0.1, 0.2, 0.3]
        assert model_name == "bge-large-en-v1.5"


@pytest.mark.asyncio
async def test_get_embedding_failure():
    """Test embedding generation failure returns zero vector."""
    # Mock HTTP client to raise exception - patch where the module is used
    with patch('app.services.insights_generator.httpx.AsyncClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("API error"))
        
        # Make the context manager work
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)
        
        embedding, model_name = await get_embedding(
            "test text",
            "http://localhost:8002",
            None
        )
        
        # Should return zero vector and default model name
        assert embedding is not None
        assert len(embedding) == 1024  # Default dimension from EMBEDDING_DIMENSION
        assert all(x == 0.0 for x in embedding)
        assert model_name is not None


@pytest.mark.asyncio
async def test_analyze_conversation_user_preferences():
    """Test extracting insights from user preferences."""
    messages = [
        Message(
            role="user",
            content="I prefer using Python for data analysis because it has great libraries",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
        Message(
            role="assistant",
            content="That's a great choice! Python has pandas, numpy, and scikit-learn.",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
        Message(
            role="user",
            content="I always use Jupyter notebooks for my work",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
    ]
    
    with patch("app.services.insights_generator.extract_insights_with_llm", new=AsyncMock(return_value=[
        {"content": "User prefers Python for data analysis workflows.", "category": "preference"}
    ])):
        insights = await analyze_conversation_for_insights(
            messages,
            "conv-123",
            "user-123"
        )
    
    # Should extract insights about preferences
    assert len(insights) > 0
    
    # Check that preference keywords increased importance
    preference_insights = [i for i in insights if "prefer" in i.content.lower() or "always" in i.content.lower()]
    assert len(preference_insights) > 0
    assert all(i.importance >= 0.6 for i in preference_insights)


@pytest.mark.asyncio
async def test_analyze_conversation_questions():
    """Test that questions are identified as important."""
    messages = [
        Message(
            role="user",
            content="How do I implement a neural network in PyTorch?",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
        Message(
            role="assistant",
            content="Here's how to implement a neural network...",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
    ]
    
    with patch("app.services.insights_generator.extract_insights_with_llm", new=AsyncMock(return_value=[
        {"content": "User asked how to implement a neural network in PyTorch?", "category": "goal"}
    ])):
        insights = await analyze_conversation_for_insights(
            messages,
            "conv-123",
            "user-123"
        )
    
    # Questions should be extracted
    question_insights = [i for i in insights if "?" in i.content]
    assert len(question_insights) > 0


@pytest.mark.asyncio
async def test_analyze_conversation_facts():
    """Test extracting factual statements from assistant messages."""
    messages = [
        Message(
            role="user",
            content="What is machine learning?",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
        Message(
            role="assistant",
            content="Machine learning is a subset of artificial intelligence. It refers to algorithms that improve through experience. Neural networks are a type of machine learning model.",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
    ]
    
    with patch("app.services.insights_generator.extract_insights_with_llm", new=AsyncMock(return_value=[
        {"content": "Machine learning is a subset of artificial intelligence.", "category": "fact"}
    ])):
        insights = await analyze_conversation_for_insights(
            messages,
            "conv-123",
            "user-123"
        )
    
    # Should extract factual statements
    assert len(insights) > 0
    
    # Check for factual indicators
    fact_insights = [i for i in insights if any(ind in i.content.lower() for ind in ["is", "are", "refers to"])]
    assert len(fact_insights) > 0


@pytest.mark.asyncio
async def test_analyze_conversation_short_messages_skipped():
    """Test that very short messages are skipped."""
    messages = [
        Message(
            role="user",
            content="Hi",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
        Message(
            role="assistant",
            content="Hello!",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
    ]
    
    insights = await analyze_conversation_for_insights(
        messages,
        "conv-123",
        "user-123"
    )
    
    # Short messages should be skipped
    assert len(insights) == 0


@pytest.mark.asyncio
async def test_analyze_conversation_limits_insights():
    """Test that insights are limited to top 10."""
    # Create many messages
    messages = []
    for i in range(20):
        messages.append(
            Message(
                role="user",
                content=f"I prefer using tool {i} because it's great for my workflow and I always use it",
                conversation_id="conv-123",
                created_at=datetime.now(timezone.utc).replace(tzinfo=None)
            )
        )
    
    insights = await analyze_conversation_for_insights(
        messages,
        "conv-123",
        "user-123"
    )
    
    # Should be limited to 10
    assert len(insights) <= 10


@pytest.mark.asyncio
@patch('app.services.insights_generator.get_embedding')
async def test_generate_and_store_insights_success(mock_get_embedding):
    """Test successful insights generation and storage."""
    # Mock embedding generation - returns tuple of (embedding, model_name)
    mock_get_embedding.return_value = ([0.1] * 384, "bge-large-en-v1.5")
    
    # Mock insights service
    mock_insights_service = MagicMock()
    mock_insights_service.insert_insights = MagicMock()
    mock_insights_service.get_conversation_insights = MagicMock(return_value=[])  # No existing insights
    mock_insights_service.list_user_insights = MagicMock(return_value=([], 0))
    
    # Create conversation and messages
    conversation = Conversation(
        title="Test Conversation",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
    
    messages = [
        Message(
            role="user",
            content="I prefer using Python for all my data analysis work because it's powerful",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
        Message(
            role="assistant",
            content="Python is indeed a great choice for data analysis.",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
    ]
    
    new_count, existing_count = await generate_and_store_insights(
        conversation,
        messages,
        mock_insights_service,
        "http://localhost:8002",
        None
    )
    
    # Should have generated new insights
    assert new_count > 0
    assert existing_count == 0
    
    # Should have called insert_insights
    mock_insights_service.insert_insights.assert_called_once()


@pytest.mark.asyncio
async def test_generate_and_store_insights_no_insights():
    """Test when no insights are extracted."""
    mock_insights_service = MagicMock()
    mock_insights_service.get_conversation_insights = MagicMock(return_value=[])  # No existing insights
    mock_insights_service.insert_insights = MagicMock()
    mock_insights_service.list_user_insights = MagicMock(return_value=([], 0))
    
    conversation = Conversation(
        title="Test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
    
    # Short messages that won't generate insights
    messages = [
        Message(
            role="user",
            content="Hi",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
    ]
    
    new_count, existing_count = await generate_and_store_insights(
        conversation,
        messages,
        mock_insights_service,
        "http://localhost:8002",
        None
    )
    
    # Session summary memory is intentionally created even for sparse chats.
    assert new_count == 1
    assert existing_count == 0
    mock_insights_service.insert_insights.assert_called_once()


@pytest.mark.asyncio
@patch('app.services.insights_generator.get_embedding')
async def test_generate_and_store_insights_with_existing(mock_get_embedding):
    """Test that existing insights are not duplicated."""
    # Mock embedding generation - returns tuple of (embedding, model_name)
    mock_get_embedding.return_value = ([0.1] * 384, "bge-large-en-v1.5")
    
    # Mock insights service with existing insights
    existing_insights = [
        {"id": "1", "content": "I prefer using Python for all my data analysis work because it's powerful", "category": "preference"}
    ]
    mock_insights_service = MagicMock()
    mock_insights_service.insert_insights = MagicMock()
    mock_insights_service.get_conversation_insights = MagicMock(return_value=existing_insights)
    mock_insights_service.list_user_insights = MagicMock(return_value=(existing_insights, len(existing_insights)))
    
    conversation = Conversation(
        title="Test Conversation",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
    
    # Same message as existing insight
    messages = [
        Message(
            role="user",
            content="I prefer using Python for all my data analysis work because it's powerful",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        ),
    ]
    
    new_count, existing_count = await generate_and_store_insights(
        conversation,
        messages,
        mock_insights_service,
        "http://localhost:8002",
        None
    )
    
    # Existing insight should not be duplicated. A session summary may still be added.
    assert new_count <= 1
    assert existing_count >= 1


def test_should_generate_insights_sufficient_messages():
    """Test insights generation threshold with sufficient messages."""
    conversation = Conversation(
        title="Test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
    
    # 2 messages (1 exchange)
    assert should_generate_insights(conversation, 2) is True
    
    # 6 messages (3 exchanges)
    assert should_generate_insights(conversation, 6) is True


def test_should_generate_insights_insufficient_messages():
    """Test insights generation threshold with insufficient messages."""
    conversation = Conversation(
        title="Test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
    
    # Less than 2 messages
    assert should_generate_insights(conversation, 1) is False
    assert should_generate_insights(conversation, 0) is False


def test_should_generate_insights_too_recent():
    """Test insights generation threshold with recent conversation."""
    from datetime import timedelta
    
    # Conversation less than 30 seconds old
    conversation = Conversation(
        title="Test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
    
    # Even with enough messages, should not generate if too recent
    assert should_generate_insights(conversation, 2) is False


def test_should_generate_insights_old_enough():
    """Test insights generation with conversation old enough."""
    from datetime import timedelta
    
    conversation = Conversation(
        title="Test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
    
    assert should_generate_insights(conversation, 2) is True


def test_profile_fields_schema_present():
    """Profile fields schema includes expected baseline fields."""
    expected = {
        "location",
        "occupation",
        "communication_tone",
        "primary_language",
        "timezone",
        "key_interests",
    }
    assert expected.issubset(set(PROFILE_FIELDS.keys()))


def test_get_profile_completeness_empty():
    """Empty insights should return zero completeness and all fields missing."""
    result = get_profile_completeness([])
    assert result["score"] == 0.0
    assert result["required_score"] == 0.0
    assert set(result["missing_fields"]) == set(PROFILE_FIELDS.keys())
    assert "location" in result["required_missing_fields"]


def test_get_profile_completeness_detects_known_fields():
    """Heuristics detect completed profile fields from insight corpus."""
    existing = [
        {"content": "I live in Boston and usually work remotely.", "category": "context"},
        {"content": "I work as a software engineer.", "category": "fact"},
        {"content": "Please keep it brief and direct.", "category": "preference"},
    ]
    result = get_profile_completeness(existing)
    assert "location" in result["completed_fields"]
    assert "occupation" in result["completed_fields"]
    assert "communication_tone" in result["completed_fields"]
    assert result["score"] > 0.0


def test_extract_profile_insights_from_messages_captures_location():
    """Deterministic profile extraction should capture location from user phrasing."""
    messages = [
        Message(
            role="user",
            content="I'm in Boston, MA.",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ),
        Message(
            role="assistant",
            content="Thanks for sharing your location.",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ),
    ]
    extracted = extract_profile_insights_from_messages(
        messages=messages,
        conversation_id="conv-123",
        user_id="user-123",
        existing_insights=[],
    )
    assert any("based in Boston, MA" in insight.content for insight in extracted)


def test_should_promote_context_globally_is_sparse():
    """Only durable user-level context should be globally promoted."""
    assert _should_promote_context_globally("Session summary: User topics: weather") is False
    assert _should_promote_context_globally("User is based in Boston, MA.") is True
    assert _should_promote_context_globally("User works as a product manager.") is True


@pytest.mark.asyncio
@patch("busibox_common.llm.get_client")
async def test_identify_knowledge_gaps_creates_pending_question(mock_get_client):
    """Missing profile fields should produce one pending_question insight."""
    # Force LLM fallback path for determinism.
    mock_get_client.side_effect = RuntimeError("llm unavailable")
    conversation = Conversation(
        id=uuid.uuid4(),
        title="Test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    messages = [
        Message(
            role="user",
            content="Can you help me plan my week?",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ),
        Message(
            role="assistant",
            content="Absolutely. Let me help with that.",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ),
    ]
    pending = await identify_knowledge_gaps(
        conversation=conversation,
        messages=messages,
        user_id="user-123",
        existing_insights=[],
    )
    assert pending is not None
    assert pending.category == "pending_question"
    assert pending.conversation_id.startswith("pending:")
    assert len(pending.content) > 10


@pytest.mark.asyncio
@patch("busibox_common.llm.get_client")
async def test_identify_knowledge_gaps_skips_when_pending_exists(mock_get_client):
    """No new pending question should be created when one already exists."""
    mock_get_client.side_effect = RuntimeError("llm unavailable")
    conversation = Conversation(
        id=uuid.uuid4(),
        title="Test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    messages = [
        Message(
            role="user",
            content="Need help prioritizing work.",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ),
        Message(
            role="assistant",
            content="Sure, let's do that.",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ),
    ]
    existing_insights = [
        {"content": "Quick profile question: what timezone should I assume for dates and scheduling?", "category": "pending_question"}
    ]
    pending = await identify_knowledge_gaps(
        conversation=conversation,
        messages=messages,
        user_id="user-123",
        existing_insights=existing_insights,
    )
    assert pending is None


@pytest.mark.asyncio
@patch("busibox_common.llm.get_client")
async def test_identify_knowledge_gaps_moves_past_location_when_known(mock_get_client):
    """If location is already known, the next pending question should target another field."""
    mock_get_client.side_effect = RuntimeError("llm unavailable")
    conversation = Conversation(
        id=uuid.uuid4(),
        title="Test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    messages = [
        Message(
            role="user",
            content="What's the weather tomorrow?",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ),
        Message(
            role="assistant",
            content="I can help with that.",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ),
    ]
    existing_insights = [
        {"content": "User is based in Boston, MA.", "category": "fact"},
    ]
    pending = await identify_knowledge_gaps(
        conversation=conversation,
        messages=messages,
        user_id="user-123",
        existing_insights=existing_insights,
    )
    assert pending is not None
    assert "city or region" not in pending.content.lower()

