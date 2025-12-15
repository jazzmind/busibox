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
async def test_list_conversations_empty(client: AsyncClient):
    """Test GET /conversations returns empty list when no conversations exist."""
    response = await client.get("/conversations")
    
    assert response.status_code == 200
    data = response.json()
    assert data["conversations"] == []
    assert data["total"] == 0
    assert data["limit"] == 50
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_list_conversations_with_data(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_principal: Principal
):
    """Test GET /conversations returns user's conversations with message counts."""
    # Create test conversations
    conv1 = Conversation(
        title="Test Conversation 1",
        user_id=mock_principal.sub
    )
    conv2 = Conversation(
        title="Test Conversation 2",
        user_id=mock_principal.sub
    )
    # Conversation from another user should not appear
    conv3 = Conversation(
        title="Other User Conversation",
        user_id="other-user-456"
    )
    
    test_session.add_all([conv1, conv2, conv3])
    await test_session.commit()
    await test_session.refresh(conv1)
    await test_session.refresh(conv2)
    
    # Add messages to conv1
    msg1 = Message(
        conversation_id=conv1.id,
        role="user",
        content="Hello"
    )
    msg2 = Message(
        conversation_id=conv1.id,
        role="assistant",
        content="Hi there!"
    )
    test_session.add_all([msg1, msg2])
    await test_session.commit()
    
    response = await client.get("/conversations")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data["conversations"]) == 2
    assert data["total"] == 2
    
    # Verify conversation with messages has correct count
    conv_with_messages = next(c for c in data["conversations"] if c["id"] == str(conv1.id))
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
    # Create 10 conversations
    conversations = [
        Conversation(title=f"Conv {i}", user_id=mock_principal.sub)
        for i in range(10)
    ]
    test_session.add_all(conversations)
    await test_session.commit()
    
    # Test limit
    response = await client.get("/conversations?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data["conversations"]) == 5
    assert data["total"] == 10
    
    # Test offset
    response = await client.get("/conversations?limit=5&offset=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data["conversations"]) == 5
    assert data["offset"] == 5


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
async def test_get_chat_settings_creates_default(
    client: AsyncClient,
    mock_principal: Principal
):
    """Test GET /users/me/chat-settings creates default settings if not found."""
    response = await client.get("/users/me/chat-settings")
    
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == mock_principal.sub
    assert data["temperature"] == 0.7
    assert data["max_tokens"] == 2000
    assert data["enabled_tools"] == []
    assert data["enabled_agents"] == []


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
    """Test PUT /users/me/chat-settings creates settings if not found (upsert)."""
    # Create initial settings
    settings = ChatSettings(
        user_id=mock_principal.sub,
        temperature=0.8,
        max_tokens=1000
    )
    test_session.add(settings)
    await test_session.commit()
    
    # Update settings
    payload = {
        "temperature": 0.9,
        "max_tokens": 3000
    }
    
    response = await client.put("/users/me/chat-settings", json=payload)
    
    assert response.status_code == 200
    data = response.json()
    assert data["temperature"] == 0.9
    assert data["max_tokens"] == 3000


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






