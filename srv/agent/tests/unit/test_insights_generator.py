"""Unit tests for insights generator service."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.insights_generator import (
    ConversationInsight,
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
@patch('app.services.insights_generator.httpx.AsyncClient')
async def test_get_embedding_success(mock_client_class):
    """Test successful embedding generation."""
    # Mock HTTP client as async context manager
    mock_response = MagicMock()
    mock_response.json.return_value = {"embedding": [0.1, 0.2, 0.3]}
    mock_response.raise_for_status = MagicMock()
    
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    
    # Set up async context manager behavior
    mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)
    
    embedding = await get_embedding(
        "test text",
        "http://localhost:8002",
        "Bearer token"
    )
    
    assert embedding == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
@patch('app.services.insights_generator.httpx.AsyncClient')
async def test_get_embedding_failure(mock_client_class):
    """Test embedding generation failure returns zero vector."""
    # Mock HTTP client to raise exception during post
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=Exception("API error"))
    
    # Set up async context manager behavior
    mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)
    
    embedding = await get_embedding(
        "test text",
        "http://localhost:8002",
        None
    )
    
    # Should return zero vector
    assert embedding is not None
    assert len(embedding) == 384
    assert all(x == 0.0 for x in embedding)


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
    # Mock embedding generation
    mock_get_embedding.return_value = [0.1] * 384
    
    # Mock insights service
    mock_insights_service = MagicMock()
    mock_insights_service.insert_insights = MagicMock()
    
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
    
    count = await generate_and_store_insights(
        conversation,
        messages,
        mock_insights_service,
        "http://localhost:8002",
        None
    )
    
    # Should have generated insights
    assert count > 0
    
    # Should have called insert_insights
    mock_insights_service.insert_insights.assert_called_once()


@pytest.mark.asyncio
async def test_generate_and_store_insights_no_insights():
    """Test when no insights are extracted."""
    mock_insights_service = MagicMock()
    
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
    
    count = await generate_and_store_insights(
        conversation,
        messages,
        mock_insights_service,
        "http://localhost:8002",
        None
    )
    
    assert count == 0


def test_should_generate_insights_sufficient_messages():
    """Test insights generation threshold with sufficient messages."""
    conversation = Conversation(
        title="Test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
    
    # 4 messages (2 exchanges)
    assert should_generate_insights(conversation, 4) is True
    
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
    
    # Less than 4 messages
    assert should_generate_insights(conversation, 2) is False
    assert should_generate_insights(conversation, 3) is False


def test_should_generate_insights_too_recent():
    """Test insights generation threshold with recent conversation."""
    from datetime import timedelta
    
    # Conversation less than 5 minutes old
    conversation = Conversation(
        title="Test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
    
    # Even with enough messages, should not generate if too recent
    assert should_generate_insights(conversation, 6) is False


def test_should_generate_insights_old_enough():
    """Test insights generation with conversation old enough."""
    from datetime import timedelta
    
    conversation = Conversation(
        title="Test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
    
    assert should_generate_insights(conversation, 4) is True

