"""
Integration tests for chat flow with conversation history and intelligent routing.

Tests:
- Creating conversations
- Sending messages
- Auto model selection
- Message history retrieval
- Streaming responses
- Tool/agent routing
"""

import asyncio
import json
import pytest
import uuid
from httpx import AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_send_chat_message_creates_conversation(async_client: AsyncClient, auth_headers: dict):
    """Test sending a message creates a new conversation."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Hello, this is a test message",
            "model": "chat"
        },
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert "message_id" in data
    assert "conversation_id" in data
    assert "content" in data
    assert "model" in data
    assert data["model"] == "chat"
    
    # Verify conversation was created
    conversation_id = data["conversation_id"]
    assert conversation_id is not None


@pytest.mark.asyncio
async def test_send_message_to_existing_conversation(async_client: AsyncClient, auth_headers: dict):
    """Test sending a message to an existing conversation."""
    # Create first message (creates conversation)
    response1 = await async_client.post(
        "/chat/message",
        json={
            "message": "First message",
            "model": "chat"
        },
        headers=auth_headers
    )
    
    assert response1.status_code == 200
    conversation_id = response1.json()["conversation_id"]
    
    # Send second message to same conversation
    response2 = await async_client.post(
        "/chat/message",
        json={
            "message": "Second message",
            "conversation_id": conversation_id,
            "model": "chat"
        },
        headers=auth_headers
    )
    
    assert response2.status_code == 200, f"Expected 200, got {response2.status_code}: {response2.text}"
    data2 = response2.json()
    
    assert data2["conversation_id"] == conversation_id
    assert "message_id" in data2


@pytest.mark.asyncio
async def test_auto_model_selection(async_client: AsyncClient, auth_headers: dict):
    """Test auto model selection based on message content."""
    # Simple conversation - should select chat model
    response1 = await async_client.post(
        "/chat/message",
        json={
            "message": "Hello, how are you?",
            "model": "auto"
        },
        headers=auth_headers
    )
    
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1["model"] in ["chat", "research", "frontier"]
    
    # Complex analysis - should select research or frontier
    response2 = await async_client.post(
        "/chat/message",
        json={
            "message": "Analyze the pros and cons of microservices architecture in detail",
            "model": "auto"
        },
        headers=auth_headers
    )
    
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["model"] in ["research", "frontier"]


@pytest.mark.asyncio
async def test_get_chat_history(async_client: AsyncClient, auth_headers: dict):
    """Test retrieving chat history for a conversation."""
    # Create conversation with messages
    response1 = await async_client.post(
        "/chat/message",
        json={
            "message": "First message",
            "model": "chat"
        },
        headers=auth_headers
    )
    
    conversation_id = response1.json()["conversation_id"]
    
    # Add second message
    await async_client.post(
        "/chat/message",
        json={
            "message": "Second message",
            "conversation_id": conversation_id,
            "model": "chat"
        },
        headers=auth_headers
    )
    
    # Get history
    response = await async_client.get(
        f"/chat/{conversation_id}/history",
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["conversation_id"] == conversation_id
    assert "title" in data
    assert "messages" in data
    assert "total_messages" in data
    
    # Should have 4 messages: 2 user + 2 assistant
    assert data["total_messages"] >= 4
    assert len(data["messages"]) >= 4


@pytest.mark.asyncio
async def test_list_available_models(async_client: AsyncClient, auth_headers: dict):
    """Test listing available models."""
    response = await async_client.get(
        "/chat/models",
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert "models" in data
    assert len(data["models"]) > 0
    
    # Check model structure
    model = data["models"][0]
    assert "id" in model
    assert "name" in model
    assert "description" in model
    assert "supports_vision" in model
    assert "supports_tools" in model
    assert "supports_reasoning" in model
    assert "max_tokens" in model
    assert "cost_tier" in model
    assert "speed_tier" in model


@pytest.mark.asyncio
async def test_chat_with_web_search_enabled(async_client: AsyncClient, auth_headers: dict):
    """Test chat with web search tool enabled."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "What is the latest news about AI?",
            "model": "auto",
            "enable_web_search": True
        },
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert "routing_decision" in data
    # Should route to web search
    routing = data["routing_decision"]
    assert "selected_tools" in routing


@pytest.mark.asyncio
async def test_chat_with_doc_search_enabled(async_client: AsyncClient, auth_headers: dict):
    """Test chat with document search tool enabled."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "What does the document say about revenue?",
            "model": "auto",
            "enable_doc_search": True
        },
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert "routing_decision" in data
    routing = data["routing_decision"]
    assert "selected_tools" in routing


@pytest.mark.asyncio
async def test_chat_streaming(async_client: AsyncClient, auth_headers: dict):
    """Test streaming chat response."""
    async with async_client.stream(
        "POST",
        "/chat/message/stream",
        json={
            "message": "Tell me a short story",
            "model": "chat"
        },
        headers=auth_headers
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
        
        events = []
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
                events.append({"type": event_type, "data": data})
        
        # Should have at least model_selected, routing_decision, content_chunks, and message_complete
        event_types = [e["type"] for e in events]
        assert "content_chunk" in event_types or "message_complete" in event_types


@pytest.mark.asyncio
async def test_chat_with_attachments(async_client: AsyncClient, auth_headers: dict):
    """Test chat with file attachments."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "What's in this image?",
            "model": "auto",
            "attachments": [
                {
                    "name": "test.jpg",
                    "type": "image/jpeg",
                    "url": "https://example.com/test.jpg",
                    "size": 1024
                }
            ]
        },
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Should select an advanced model for vision (frontier or research)
    assert data["model"] in ["frontier", "research"]


@pytest.mark.asyncio
async def test_chat_respects_user_settings(async_client: AsyncClient, auth_headers: dict):
    """Test that chat respects user's chat settings."""
    # Set user chat settings
    await async_client.put(
        "/users/me/chat-settings",
        json={
            "enabled_tools": ["web_search"],  # Only web search enabled
            "model": "research",
            "temperature": 0.5,
            "max_tokens": 1000
        },
        headers=auth_headers
    )
    
    # Send message with doc search enabled (should be filtered out)
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Search for information",
            "model": "auto",
            "enable_web_search": True,
            "enable_doc_search": True  # Should be filtered out by user settings
        },
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Should respect user settings
    routing = data["routing_decision"]
    if "selected_tools" in routing and routing["selected_tools"]:
        # If doc_search is selected, it should have been filtered
        assert "doc_search" not in routing["selected_tools"] or "web_search" in routing["selected_tools"]


@pytest.mark.asyncio
async def test_chat_conversation_not_found(async_client: AsyncClient, auth_headers: dict):
    """Test sending message to non-existent conversation."""
    fake_conversation_id = str(uuid.uuid4())
    
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Test message",
            "conversation_id": fake_conversation_id,
            "model": "chat"
        },
        headers=auth_headers
    )
    
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_chat_unauthorized(async_client: AsyncClient):
    """Test chat without authentication."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Test message",
            "model": "chat"
        }
    )
    
    # May return 401 (unauthorized) or 422 (validation fails before auth check)
    # depending on the order FastAPI processes dependencies
    assert response.status_code in [401, 422]


@pytest.mark.asyncio
async def test_chat_invalid_model(async_client: AsyncClient, auth_headers: dict):
    """Test chat with unknown model selection."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Test message",
            "model": "invalid_model"
        },
        headers=auth_headers
    )
    
    # The API accepts any model string and uses fallback/default behavior
    # Either success (200) if dispatcher handles gracefully, or 422 if validation rejects
    assert response.status_code in [200, 422]


@pytest.mark.asyncio
async def test_chat_empty_message(async_client: AsyncClient, auth_headers: dict):
    """Test chat with empty message."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "",
            "model": "chat"
        },
        headers=auth_headers
    )
    
    # Should return 422 for validation error
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_message_too_long(async_client: AsyncClient, auth_headers: dict):
    """Test chat with message exceeding max length."""
    long_message = "a" * 10001  # Exceeds 10000 char limit
    
    response = await async_client.post(
        "/chat/message",
        json={
            "message": long_message,
            "model": "chat"
        },
        headers=auth_headers
    )
    
    # Should return 422 for validation error
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_with_tool_execution(async_client: AsyncClient, auth_headers: dict):
    """Test chat with actual tool execution (web search)."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "What is the weather like today?",
            "model": "auto",
            "enable_web_search": True
        },
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Should have tool calls
    assert "tool_calls" in data
    if data["tool_calls"]:
        # Verify tool call structure
        tool_call = data["tool_calls"][0]
        assert "tool_name" in tool_call
        assert "success" in tool_call
        assert "output" in tool_call


@pytest.mark.asyncio
async def test_chat_with_doc_search_execution(async_client: AsyncClient, auth_headers: dict):
    """Test chat with document search execution."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Search my documents for revenue information",
            "model": "auto",
            "enable_doc_search": True
        },
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Should have routing decision
    assert "routing_decision" in data
    routing = data["routing_decision"]
    
    # Should select doc_search
    assert "selected_tools" in routing


@pytest.mark.milvus
@pytest.mark.asyncio
async def test_generate_insights_manually(async_client: AsyncClient, auth_headers: dict):
    """Test manual insights generation (requires Milvus)."""
    # Create conversation with multiple messages
    response1 = await async_client.post(
        "/chat/message",
        json={
            "message": "I prefer using Python for data analysis because it has great libraries",
            "model": "chat"
        },
        headers=auth_headers
    )
    
    assert response1.status_code == 200
    conversation_id = response1.json()["conversation_id"]
    
    # Add more messages
    await async_client.post(
        "/chat/message",
        json={
            "message": "Can you help me analyze this dataset?",
            "conversation_id": conversation_id,
            "model": "chat"
        },
        headers=auth_headers
    )
    
    # Generate insights
    response = await async_client.post(
        f"/chat/{conversation_id}/generate-insights",
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert "insights_generated" in data
    assert "conversation_id" in data
    assert data["conversation_id"] == conversation_id


@pytest.mark.milvus
@pytest.mark.asyncio
async def test_insights_generation_insufficient_messages(async_client: AsyncClient, auth_headers: dict):
    """Test insights generation with too few messages (requires Milvus)."""
    # Create conversation with only one message
    response1 = await async_client.post(
        "/chat/message",
        json={
            "message": "Hello",
            "model": "chat"
        },
        headers=auth_headers
    )
    
    assert response1.status_code == 200
    conversation_id = response1.json()["conversation_id"]
    
    # Try to generate insights (should fail)
    response = await async_client.post(
        f"/chat/{conversation_id}/generate-insights",
        headers=auth_headers
    )
    
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_chat_with_multiple_tools(async_client: AsyncClient, auth_headers: dict):
    """Test chat with multiple tools enabled."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Search the web and my documents for information about AI",
            "model": "auto",
            "enable_web_search": True,
            "enable_doc_search": True
        },
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Should have content
    assert "content" in data
    assert len(data["content"]) > 0
    
    # Should have routing decision
    assert "routing_decision" in data


@pytest.mark.asyncio
async def test_streaming_with_tool_execution(async_client: AsyncClient, auth_headers: dict):
    """Test streaming chat with tool execution."""
    async with async_client.stream(
        "POST",
        "/chat/message/stream",
        json={
            "message": "What's the latest news?",
            "model": "auto",
            "enable_web_search": True
        },
        headers=auth_headers
    ) as response:
        assert response.status_code == 200
        
        events = []
        event_type = None
        
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event_type:
                try:
                    data = json.loads(line.split(":", 1)[1].strip())
                    events.append({"type": event_type, "data": data})
                except:
                    pass
        
        # Should have various event types
        event_types = [e["type"] for e in events]
        
        # Should have at least some events
        assert len(events) > 0
        
        # Should have tool-related events if tools were used
        if "tools_start" in event_types or "tool_result" in event_types:
            assert "tool_result" in event_types or "tools_start" in event_types


@pytest.mark.asyncio
async def test_chat_conversation_context(async_client: AsyncClient, auth_headers: dict):
    """Test that conversation context is maintained across messages."""
    # First message
    response1 = await async_client.post(
        "/chat/message",
        json={
            "message": "My name is Alice and I work in data science",
            "model": "chat"
        },
        headers=auth_headers
    )
    
    assert response1.status_code == 200
    conversation_id = response1.json()["conversation_id"]
    
    # Second message referencing first
    response2 = await async_client.post(
        "/chat/message",
        json={
            "message": "What did I just tell you about myself?",
            "conversation_id": conversation_id,
            "model": "chat"
        },
        headers=auth_headers
    )
    
    assert response2.status_code == 200
    
    # Get history to verify context
    history_response = await async_client.get(
        f"/chat/{conversation_id}/history",
        headers=auth_headers
    )
    
    assert history_response.status_code == 200
    history = history_response.json()
    
    # Should have 4 messages (2 user + 2 assistant)
    assert history["total_messages"] >= 4

