"""
Integration tests for conversation and message management API endpoints.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import ChatSettings, Conversation, Message
from app.schemas.auth import Principal


# ========== Conversation Tests ==========

@pytest.mark.asyncio
async def test_list_conversations_returns_list(client: AsyncClient):
    """Test GET /conversations returns a valid response with conversations list."""
    response = await client.get("/conversations")
    
    assert response.status_code == 200
    data = response.json()
    # Verify response structure (may have existing data from other tests)
    assert "conversations" in data
    assert isinstance(data["conversations"], list)
    assert "total" in data
    assert data["limit"] == 50
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_list_conversations_with_data(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test GET /conversations returns user's conversations with message counts."""
    # Get initial count
    initial_response = await client.get("/conversations")
    initial_count = initial_response.json()["total"]
    
    # Create unique test conversations via API (not direct DB) to ensure consistency
    unique_id = uuid.uuid4().hex[:8]
    conv1_response = await client.post("/conversations", json={"title": f"Test Conversation 1 {unique_id}"})
    assert conv1_response.status_code == 201
    conv1_id = conv1_response.json()["id"]
    
    conv2_response = await client.post("/conversations", json={"title": f"Test Conversation 2 {unique_id}"})
    assert conv2_response.status_code == 201
    
    # Add messages to conv1
    msg1_response = await client.post(f"/conversations/{conv1_id}/messages", json={
        "role": "user",
        "content": "Hello"
    })
    assert msg1_response.status_code == 201
    
    msg2_response = await client.post(f"/conversations/{conv1_id}/messages", json={
        "role": "assistant",
        "content": "Hi there!"
    })
    assert msg2_response.status_code == 201
    
    response = await client.get("/conversations")
    
    assert response.status_code == 200
    data = response.json()
    # Verify we added 2 conversations
    assert data["total"] >= initial_count + 2
    
    # Verify conversation with messages has correct count
    conv_with_messages = next((c for c in data["conversations"] if c["id"] == conv1_id), None)
    assert conv_with_messages is not None
    assert conv_with_messages["message_count"] == 2
    assert conv_with_messages["last_message"]["role"] == "assistant"
    assert conv_with_messages["last_message"]["content"] == "Hi there!"


@pytest.mark.asyncio
async def test_list_conversations_pagination(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test GET /conversations pagination."""
    # Get initial count
    initial_response = await client.get("/conversations")
    initial_count = initial_response.json()["total"]
    
    # Create 10 unique conversations via API
    unique_id = uuid.uuid4().hex[:8]
    for i in range(10):
        response = await client.post("/conversations", json={"title": f"PaginationTest Conv {i} {unique_id}"})
        assert response.status_code == 201
    
    # Test limit
    response = await client.get("/conversations?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data["conversations"]) == 5
    # Total should include all existing + 10 new
    assert data["total"] >= initial_count + 10
    
    # Test offset - should work regardless of total
    response = await client.get("/conversations?limit=5&offset=5")
    assert response.status_code == 200
    data = response.json()
    # Offset should be reflected
    assert data["offset"] == 5
    # Should still have 5 items (if total > 10)
    if data["total"] > 10:
        assert len(data["conversations"]) == 5


@pytest.mark.asyncio
async def test_create_conversation_with_title(client: AsyncClient):
    """Test POST /conversations creates conversation with custom title."""
    payload = {"title": "My Custom Conversation"}
    
    response = await client.post("/conversations", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["title"] == "My Custom Conversation"
    assert data["message_count"] == 0
    assert data["last_message"] is None


@pytest.mark.asyncio
async def test_create_conversation_default_title(client: AsyncClient):
    """Test POST /conversations uses default title when none provided."""
    payload = {}
    
    response = await client.post("/conversations", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "New Conversation"


@pytest.mark.asyncio
async def test_get_conversation_with_messages(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test GET /conversations/{id} returns conversation with messages."""
    # Create conversation with messages
    conv = Conversation(title="Test Chat", user_id=mock_principal.sub)
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    messages = [
        Message(conversation_id=conv.id, role="user", content="Question 1"),
        Message(conversation_id=conv.id, role="assistant", content="Answer 1"),
        Message(conversation_id=conv.id, role="user", content="Question 2"),
    ]
    test_session.add_all(messages)
    await test_session.commit()
    
    response = await client.get(f"/conversations/{conv.id}")
    
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(conv.id)
    assert data["title"] == "Test Chat"
    assert len(data["messages"]) == 3
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_get_conversation_not_found(client: AsyncClient):
    """Test GET /conversations/{id} returns 404 for non-existent conversation."""
    fake_id = uuid.uuid4()
    response = await client.get(f"/conversations/{fake_id}")
    
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_conversation_forbidden(
    client: AsyncClient,
    test_session: AsyncSession
):
    """Test GET /conversations/{id} returns 403 for other user's conversation."""
    # Create conversation for another user
    conv = Conversation(title="Other's Chat", user_id="other-user-456")
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    response = await client.get(f"/conversations/{conv.id}")
    
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_conversation_title(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test PATCH /conversations/{id} updates conversation title."""
    conv = Conversation(title="Old Title", user_id=mock_principal.sub)
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    payload = {"title": "New Title"}
    response = await client.patch(f"/conversations/{conv.id}", json=payload)
    
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "New Title"
    
    # Verify in database
    await test_session.refresh(conv)
    assert conv.title == "New Title"


@pytest.mark.asyncio
async def test_delete_conversation_cascade(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test DELETE /conversations/{id} cascade deletes messages."""
    # Create conversation with messages
    conv = Conversation(title="To Delete", user_id=mock_principal.sub)
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    messages = [
        Message(conversation_id=conv.id, role="user", content="Message 1"),
        Message(conversation_id=conv.id, role="assistant", content="Message 2"),
    ]
    test_session.add_all(messages)
    await test_session.commit()
    
    response = await client.delete(f"/conversations/{conv.id}")
    
    assert response.status_code == 204
    
    # Verify conversation and messages are deleted
    from sqlalchemy import select
    
    conv_result = await test_session.execute(
        select(Conversation).where(Conversation.id == conv.id)
    )
    assert conv_result.scalar_one_or_none() is None
    
    msg_result = await test_session.execute(
        select(Message).where(Message.conversation_id == conv.id)
    )
    assert msg_result.scalars().all() == []


# ========== Message Tests ==========

@pytest.mark.asyncio
async def test_list_messages(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test GET /conversations/{id}/messages returns messages with pagination."""
    conv = Conversation(title="Chat", user_id=mock_principal.sub)
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    # Create 5 messages
    messages = [
        Message(conversation_id=conv.id, role="user", content=f"Message {i}")
        for i in range(5)
    ]
    test_session.add_all(messages)
    await test_session.commit()
    
    response = await client.get(f"/conversations/{conv.id}/messages")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data["messages"]) == 5
    assert data["total"] == 5
    assert data["messages"][0]["content"] == "Message 0"


@pytest.mark.asyncio
async def test_list_messages_pagination(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test message pagination."""
    conv = Conversation(title="Chat", user_id=mock_principal.sub)
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    messages = [
        Message(conversation_id=conv.id, role="user", content=f"Message {i}")
        for i in range(10)
    ]
    test_session.add_all(messages)
    await test_session.commit()
    
    response = await client.get(f"/conversations/{conv.id}/messages?limit=5&offset=3")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data["messages"]) == 5
    assert data["total"] == 10
    assert data["offset"] == 3


@pytest.mark.asyncio
async def test_create_message(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test POST /conversations/{id}/messages creates message."""
    conv = Conversation(title="Chat", user_id=mock_principal.sub)
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    payload = {
        "role": "user",
        "content": "Hello, assistant!",
        "attachments": None,
        "run_id": None,
        "routing_decision": None,
        "tool_calls": None
    }
    
    response = await client.post(f"/conversations/{conv.id}/messages", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert data["role"] == "user"
    assert data["content"] == "Hello, assistant!"
    assert data["conversation_id"] == str(conv.id)


@pytest.mark.asyncio
async def test_create_message_with_attachments(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test creating message with attachments."""
    conv = Conversation(title="Chat", user_id=mock_principal.sub)
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    payload = {
        "role": "user",
        "content": "Check this file",
        "attachments": [
            {
                "name": "document.pdf",
                "type": "application/pdf",
                "url": "s3://bucket/document.pdf",
                "size": 1024,
                "knowledge_base_id": "kb-123"
            }
        ]
    }
    
    response = await client.post(f"/conversations/{conv.id}/messages", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert len(data["attachments"]) == 1
    assert data["attachments"][0]["name"] == "document.pdf"


@pytest.mark.asyncio
async def test_create_message_invalid_role(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test creating message with invalid role."""
    conv = Conversation(title="Chat", user_id=mock_principal.sub)
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    payload = {
        "role": "invalid_role",
        "content": "This should fail"
    }
    
    response = await client.post(f"/conversations/{conv.id}/messages", json=payload)
    
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_get_message(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test GET /messages/{id} returns message."""
    conv = Conversation(title="Chat", user_id=mock_principal.sub)
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    message = Message(
        conversation_id=conv.id,
        role="assistant",
        content="Test response"
    )
    test_session.add(message)
    await test_session.commit()
    await test_session.refresh(message)
    
    response = await client.get(f"/messages/{message.id}")
    
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(message.id)
    assert data["content"] == "Test response"


@pytest.mark.asyncio
async def test_get_message_forbidden(
    client: AsyncClient,
    test_session: AsyncSession
):
    """Test GET /messages/{id} returns 403 for other user's message."""
    conv = Conversation(title="Other's Chat", user_id="other-user-456")
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    message = Message(conversation_id=conv.id, role="user", content="Test")
    test_session.add(message)
    await test_session.commit()
    await test_session.refresh(message)
    
    response = await client.get(f"/messages/{message.id}")
    
    assert response.status_code == 403


# ========== Chat Settings Tests ==========

@pytest.mark.asyncio
async def test_get_chat_settings_returns_settings(
    client: AsyncClient,
    mock_principal: Principal
):
    """Test GET /users/me/chat-settings returns settings (creates or returns existing)."""
    response = await client.get("/users/me/chat-settings")
    
    assert response.status_code == 200
    data = response.json()
    # Verify user ID matches and structure is valid
    assert data["user_id"] == mock_principal.sub
    # Temperature should be a valid float in range
    assert 0.0 <= data["temperature"] <= 2.0
    assert isinstance(data["max_tokens"], int)
    assert isinstance(data["enabled_tools"], list)
    assert isinstance(data["enabled_agents"], list)


@pytest.mark.asyncio
async def test_update_chat_settings(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test PUT /users/me/chat-settings updates settings."""
    payload = {
        "enabled_tools": ["search", "rag"],
        "temperature": 0.5,
        "max_tokens": 1500,
        "model": "gpt-4"
    }
    
    response = await client.put("/users/me/chat-settings", json=payload)
    
    assert response.status_code == 200
    data = response.json()
    assert data["enabled_tools"] == ["search", "rag"]
    assert data["temperature"] == 0.5
    assert data["max_tokens"] == 1500
    assert data["model"] == "gpt-4"


@pytest.mark.asyncio
async def test_update_chat_settings_upsert(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test PUT /users/me/chat-settings updates settings (upsert behavior)."""
    # Update settings with specific values
    payload = {
        "temperature": 0.9,
        "max_tokens": 3000
    }
    
    response = await client.put("/users/me/chat-settings", json=payload)
    
    assert response.status_code == 200
    data = response.json()
    # Verify the update was applied
    assert data["temperature"] == 0.9
    assert data["max_tokens"] == 3000
    
    # Verify persistence via GET
    get_response = await client.get("/users/me/chat-settings")
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["temperature"] == 0.9
    assert get_data["max_tokens"] == 3000


@pytest.mark.asyncio
async def test_update_chat_settings_validation(
    client: AsyncClient
):
    """Test PUT /users/me/chat-settings validates input."""
    # Invalid temperature (out of range)
    payload = {
        "temperature": 3.0  # Should be 0.0-2.0
    }
    
    response = await client.put("/users/me/chat-settings", json=payload)
    
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_conversation_updated_at_on_message_create(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test conversation updated_at is updated when message is created."""
    import asyncio
    from datetime import datetime
    
    conv = Conversation(title="Chat", user_id=mock_principal.sub)
    test_session.add(conv)
    await test_session.commit()
    await test_session.refresh(conv)
    
    original_updated_at = conv.updated_at
    
    # Wait a moment to ensure timestamp difference
    await asyncio.sleep(0.1)
    
    payload = {
        "role": "user",
        "content": "New message"
    }
    
    response = await client.post(f"/conversations/{conv.id}/messages", json=payload)
    assert response.status_code == 201
    
    # Verify updated_at changed
    await test_session.refresh(conv)
    assert conv.updated_at > original_updated_at









