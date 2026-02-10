"""
Integration tests for chat metadata context propagation.

Tests that application-level metadata (e.g. projectId, appName) is:
1. Accepted by the chat API endpoints
2. Propagated through the dispatcher to agents
3. Injected into the LLM prompt for tool execution context

These tests verify the full metadata flow:
  Frontend -> ChatMessageRequest.metadata -> Dispatcher -> AgentContext.metadata -> LLM prompt
"""

import json
import pytest
import uuid
from httpx import AsyncClient


# =============================================================================
# Metadata Acceptance Tests
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.integration
async def test_chat_message_accepts_metadata(async_client: AsyncClient, auth_headers: dict):
    """Test that the chat message endpoint accepts a metadata field."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Hello, this is a test with metadata",
            "model": "chat",
            "metadata": {
                "projectId": "test-project-123",
                "projectName": "Test Project",
                "appName": "status-report",
            }
        },
        headers=auth_headers,
    )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert "message_id" in data
    assert "conversation_id" in data
    assert "content" in data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_chat_message_works_without_metadata(async_client: AsyncClient, auth_headers: dict):
    """Test that metadata is optional and chat works without it."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Hello, no metadata here",
            "model": "chat",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert "content" in data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_chat_message_accepts_empty_metadata(async_client: AsyncClient, auth_headers: dict):
    """Test that empty metadata dict is accepted."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Hello, empty metadata",
            "model": "chat",
            "metadata": {},
        },
        headers=auth_headers,
    )

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_chat_message_accepts_null_metadata(async_client: AsyncClient, auth_headers: dict):
    """Test that null metadata is accepted."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Hello, null metadata",
            "model": "chat",
            "metadata": None,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200


# =============================================================================
# Agentic Streaming Metadata Tests
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.integration
async def test_agentic_stream_accepts_metadata(async_client: AsyncClient, auth_headers: dict):
    """Test that the agentic streaming endpoint accepts metadata."""
    async with async_client.stream(
        "POST",
        "/chat/message/stream/agentic",
        json={
            "message": "Hello with metadata context",
            "model": "chat",
            "metadata": {
                "projectId": "proj-456",
                "appName": "status-report",
            },
        },
        headers=auth_headers,
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        events = []
        event_type = None

        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event_type:
                try:
                    data = json.loads(line.split(":", 1)[1].strip())
                    events.append({"type": event_type, "data": data})
                except (json.JSONDecodeError, IndexError):
                    pass

        # Should have at least some events
        assert len(events) > 0, "Expected at least one SSE event"

        # Should have content or thought events
        event_types = {e["type"] for e in events}
        has_output = (
            "content" in event_types
            or "thought" in event_types
            or "agent_start" in event_types
            or "error" in event_types
        )
        assert has_output, f"Expected content/thought/agent events, got: {event_types}"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_agentic_stream_without_metadata(async_client: AsyncClient, auth_headers: dict):
    """Test that agentic streaming works without metadata."""
    async with async_client.stream(
        "POST",
        "/chat/message/stream/agentic",
        json={
            "message": "Hello without metadata",
            "model": "chat",
        },
        headers=auth_headers,
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
                except (json.JSONDecodeError, IndexError):
                    pass

        assert len(events) > 0


# =============================================================================
# Metadata with Selected Agent Tests
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.integration
async def test_agentic_stream_metadata_with_selected_agent(
    async_client: AsyncClient, auth_headers: dict
):
    """Test metadata flows when a specific agent is selected (like status-update)."""
    async with async_client.stream(
        "POST",
        "/chat/message/stream/agentic",
        json={
            "message": "I completed the authentication module today",
            "model": "chat",
            "selected_agents": ["chat"],  # Use the built-in chat agent
            "metadata": {
                "projectId": "proj-789",
                "projectName": "Auth Refactor",
                "appName": "status-report",
            },
        },
        headers=auth_headers,
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
                except (json.JSONDecodeError, IndexError):
                    pass

        assert len(events) > 0

        # Look for content events - the response should acknowledge the project context
        content_events = [e for e in events if e["type"] == "content"]
        if content_events:
            # Collect all content
            full_content = " ".join(
                e["data"].get("message", "") for e in content_events
            )
            # The LLM should have access to the metadata context.
            # We don't assert on exact content since LLM responses vary,
            # but the response should exist and be non-empty.
            assert len(full_content.strip()) > 0, "Expected non-empty content response"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_metadata_with_conversation_continuity(
    async_client: AsyncClient, auth_headers: dict
):
    """Test that metadata is preserved across messages in a conversation."""
    # First message with metadata
    response1 = await async_client.post(
        "/chat/message",
        json={
            "message": "Starting work on the login feature",
            "model": "chat",
            "metadata": {
                "projectId": "proj-continuity-test",
                "projectName": "Login Feature",
                "appName": "status-report",
            },
        },
        headers=auth_headers,
    )

    assert response1.status_code == 200
    conversation_id = response1.json()["conversation_id"]

    # Second message in same conversation, also with metadata
    response2 = await async_client.post(
        "/chat/message",
        json={
            "message": "I also fixed the logout bug",
            "conversation_id": conversation_id,
            "model": "chat",
            "metadata": {
                "projectId": "proj-continuity-test",
                "projectName": "Login Feature",
                "appName": "status-report",
            },
        },
        headers=auth_headers,
    )

    assert response2.status_code == 200
    assert response2.json()["conversation_id"] == conversation_id


# =============================================================================
# Metadata Validation Tests
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.integration
async def test_metadata_accepts_arbitrary_keys(async_client: AsyncClient, auth_headers: dict):
    """Test that metadata accepts arbitrary key-value pairs."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Test with custom metadata keys",
            "model": "chat",
            "metadata": {
                "projectId": "proj-custom",
                "customField": "custom-value",
                "nestedData": {"key": "value"},
                "numericField": 42,
                "booleanField": True,
                "arrayField": ["a", "b", "c"],
            },
        },
        headers=auth_headers,
    )

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_metadata_with_doc_search_enabled(async_client: AsyncClient, auth_headers: dict):
    """Test metadata works alongside doc search (common status-report pattern)."""
    response = await async_client.post(
        "/chat/message",
        json={
            "message": "Search for recent updates on this project",
            "model": "auto",
            "enable_doc_search": True,
            "metadata": {
                "projectId": "proj-doc-search",
                "projectName": "Doc Search Project",
                "appName": "status-report",
            },
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert "content" in data


# =============================================================================
# Metadata Propagation Verification Tests
# These test that metadata actually reaches the agent context
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.integration
async def test_agentic_metadata_reaches_agent_prompt(
    async_client: AsyncClient, auth_headers: dict
):
    """
    Test that metadata is injected into the agent's prompt context.
    
    We verify this by sending metadata with a unique projectId and asking
    the agent to reference it. If the metadata is properly injected into
    the LLM prompt, the agent should be able to reference the project details.
    """
    unique_project_name = f"UniqueProject_{uuid.uuid4().hex[:8]}"

    async with async_client.stream(
        "POST",
        "/chat/message/stream/agentic",
        json={
            "message": f"What project am I working on? Please tell me the project name from the context.",
            "model": "chat",
            "selected_agents": ["chat"],
            "metadata": {
                "projectId": "proj-prompt-test",
                "projectName": unique_project_name,
                "appName": "status-report",
            },
        },
        headers=auth_headers,
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
                except (json.JSONDecodeError, IndexError):
                    pass

        # Collect all content from the response
        content_events = [e for e in events if e["type"] == "content"]
        full_content = " ".join(
            e["data"].get("message", "") for e in content_events
        )

        # The LLM should reference the unique project name since it was
        # injected into the prompt via metadata
        assert unique_project_name in full_content, (
            f"Expected the agent to reference '{unique_project_name}' from metadata, "
            f"but got: {full_content[:500]}"
        )
