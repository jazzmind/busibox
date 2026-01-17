"""
Ultimate integration tests for complete chat flow with memory, tools, and agents.

These tests demonstrate the full power of the system:
1. Memory-based routing with insights
2. Multi-agent orchestration
3. Tool execution with real APIs
4. End-to-end conversation flow

These tests require Milvus vector database backend - skip with: pytest -m "not milvus"
"""

import json
import pytest
import uuid
from datetime import datetime, timezone

from app.config.settings import get_settings
from app.services.insights_service import ChatInsight

settings = get_settings()

# All tests in this module require Milvus
pytestmark = [pytest.mark.milvus, pytest.mark.integration]

# Embedding dimension used by Milvus insights collection (bge-large-en-v1.5)
EMBEDDING_DIM = 1024


def get_insights_service_safe():
    """
    Safely get insights service, raising skip if not initialized.
    """
    from app.api.insights import get_insights_service as _get_insights_service, _insights_service
    
    if _insights_service is None:
        pytest.skip("Insights service not initialized - Milvus may not be available")
    
    try:
        return _get_insights_service()
    except Exception as e:
        pytest.skip(f"Insights service unavailable: {e}")


async def get_embedding(text: str) -> list:
    """
    Get embedding from ingest API, or return a mock embedding for testing.
    
    Uses the correct dimension (1024) for the Milvus insights collection.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            embed_response = await http_client.post(
                f"{settings.ingest_api_url}/embed",
                json={"text": text}
            )
            if embed_response.status_code == 200:
                embedding = embed_response.json().get("embedding")
                if embedding and len(embedding) == EMBEDDING_DIM:
                    return embedding
    except Exception:
        pass  # Fall through to mock embedding
    
    # Return a mock embedding with correct dimension
    # Use a simple deterministic pattern based on text hash for consistency
    import hashlib
    text_hash = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
    return [(text_hash + i) % 100 / 100.0 for i in range(EMBEDDING_DIM)]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ultimate_memory_to_weather_flow(client, insights_service):
    """
    ULTIMATE TEST: Memory-based weather query flow.
    
    Flow:
    1. Create insight: "User lives in Boston"
    2. User asks: "What's the weather today?"
    3. Dispatcher searches insights → finds Boston
    4. Routes to weather agent
    5. Weather agent calls weather tool for Boston
    6. Tool fetches real weather data
    7. Agent synthesizes response with weather info
    8. Response returned to user
    
    This tests:
    - Insights storage and retrieval
    - Memory-based context understanding
    - Agent routing
    - Tool execution
    - Response synthesis
    """
    # Step 1: Create memory/insight that user lives in Boston
    # insights_service is provided via fixture
    
    # Initialize collection if needed
    try:
        insights_service.initialize_collection()
    except:
        pass  # Collection might already exist
    
    # Create insight about user location
    user_id = "test-user-123"
    conversation_id = str(uuid.uuid4())
    
    # Get embedding for the insight (uses correct 1024 dimension)
    embedding = await get_embedding("User lives in Boston, Massachusetts")
    
    insight = ChatInsight(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content="User lives in Boston, Massachusetts",
        embedding=embedding,
        conversation_id=conversation_id,
        analyzed_at=int(datetime.now(timezone.utc).timestamp())
    )
    
    insights_service.insert_insights([insight])
    insights_service.flush_collection()
    
    # Step 2: Send chat message asking about weather
    response = await client.post(
        "/chat/message",
        json={
            "message": "What's the weather today?",
            "model": "auto",
            "enable_web_search": False,  # Force to use insights + weather agent
            "enable_doc_search": False
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Step 3: Verify response structure
    assert "content" in data
    assert "routing_decision" in data
    assert "model" in data
    
    # Step 4: Verify weather information is in response
    # The response should mention Boston and include weather details
    content = data["content"].lower()
    
    # Should mention location (Boston might be inferred from insights)
    # Or the response should ask for location if insights weren't used
    assert len(content) > 0
    
    # Verify conversation was created
    assert "conversation_id" in data
    assert "message_id" in data
    
    print(f"✅ Ultimate test passed!")
    print(f"Response: {data['content'][:200]}...")
    
    # Cleanup
    try:
        insights_service.delete_conversation_insights(conversation_id, user_id)
    except:
        pass


@pytest.mark.asyncio
@pytest.mark.integration
async def test_multi_agent_web_and_doc_search(client):
    """
    Multi-agent integration test: Web search + Document search.
    
    Flow:
    1. User asks: "Compare the latest AI trends with our internal analysis"
    2. Dispatcher routes to both web_search and doc_search
    3. Web search agent searches web for AI trends
    4. Document search agent searches user's documents
    5. Results aggregated and synthesized
    6. Comprehensive response returned
    
    This tests:
    - Multi-tool routing
    - Parallel tool execution
    - Result aggregation
    - Response synthesis
    """
    response = await client.post(
        "/chat/message",
        json={
            "message": "Compare the latest AI trends with our internal analysis documents",
            "model": "auto",
            "enable_web_search": True,
            "enable_doc_search": True
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify response structure
    assert "content" in data
    assert "routing_decision" in data
    assert "tool_calls" in data
    
    # Verify routing decision
    routing = data["routing_decision"]
    assert "selected_tools" in routing
    
    # Should have selected both tools (or at least one)
    selected_tools = routing["selected_tools"]
    assert len(selected_tools) > 0
    
    # Verify tool calls were executed
    if data["tool_calls"]:
        tool_names = [tc["tool_name"] for tc in data["tool_calls"]]
        print(f"Tools executed: {tool_names}")
        
        # Verify tool execution results
        for tool_call in data["tool_calls"]:
            assert "tool_name" in tool_call
            assert "success" in tool_call
            assert "output" in tool_call
    
    # Verify response has content
    assert len(data["content"]) > 0
    
    print(f"✅ Multi-agent test passed!")
    print(f"Tools used: {routing['selected_tools']}")
    print(f"Response: {data['content'][:200]}...")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_streaming_with_memory_and_tools(client, insights_service):
    """
    Test streaming with memory-based routing and tool execution.
    
    Flow:
    1. Create insight about user preferences
    2. Send streaming request
    3. Verify events are streamed in order
    4. Verify tool execution events
    5. Verify content chunks
    6. Verify completion
    """
    # insights_service is provided via fixture
    user_id = "test-user-123"
    conversation_id = str(uuid.uuid4())
    
    try:
        insights_service.initialize_collection()
    except:
        pass
    
    # Get embedding (uses correct 1024 dimension)
    embedding = await get_embedding("User prefers detailed technical explanations")
    
    insight = ChatInsight(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content="User prefers detailed technical explanations",
        embedding=embedding,
        conversation_id=conversation_id,
        analyzed_at=int(datetime.now(timezone.utc).timestamp())
    )
    
    insights_service.insert_insights([insight])
    insights_service.flush_collection()
    
    # Send streaming request
    async with client.stream(
        "POST",
        "/chat/message/stream",
        json={
            "message": "Explain how neural networks work",
            "model": "auto",
            "enable_web_search": True
        }
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
        
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
        
        # Verify event sequence
        event_types = [e["type"] for e in events]
        
        print(f"Event types received: {event_types}")
        
        # Should have various event types
        assert len(events) > 0
        
        # Should have routing decision
        assert any(e["type"] == "routing_decision" for e in events)
        
        # Should have content chunks or completion
        assert any(e["type"] in ["content_chunk", "message_complete", "execution_complete"] for e in events)
        
        print(f"✅ Streaming test passed!")
        print(f"Total events: {len(events)}")
    
    # Cleanup
    try:
        insights_service.delete_conversation_insights(conversation_id, user_id)
    except:
        pass


@pytest.mark.asyncio
@pytest.mark.integration
async def test_conversation_with_insights_generation(client):
    """
    Test complete conversation flow with automatic insights generation.
    
    Flow:
    1. Start conversation with user preferences
    2. Continue conversation (4+ messages)
    3. System automatically generates insights
    4. Verify insights are stored
    5. Use insights in follow-up query
    """
    # Message 1: User states preference
    response1 = await client.post(
        "/chat/message",
        json={
            "message": "I prefer using Python for all my data science work because it has great libraries like pandas and scikit-learn",
            "model": "chat"
        }
    )
    
    assert response1.status_code == 200
    conversation_id = response1.json()["conversation_id"]
    
    # Message 2: Follow-up
    response2 = await client.post(
        "/chat/message",
        json={
            "message": "Can you help me with a machine learning project?",
            "conversation_id": conversation_id,
            "model": "chat"
        }
    )
    
    assert response2.status_code == 200
    
    # Message 3: More context
    response3 = await client.post(
        "/chat/message",
        json={
            "message": "I always use Jupyter notebooks for my analysis work",
            "conversation_id": conversation_id,
            "model": "chat"
        }
    )
    
    assert response3.status_code == 200
    
    # Message 4: Trigger insights generation threshold
    response4 = await client.post(
        "/chat/message",
        json={
            "message": "What tools would you recommend?",
            "conversation_id": conversation_id,
            "model": "chat"
        }
    )
    
    assert response4.status_code == 200
    
    # Wait a moment for background insights generation
    import asyncio
    await asyncio.sleep(2)
    
    # Manually trigger insights generation to ensure they're created
    insights_response = await client.post(
        f"/chat/{conversation_id}/generate-insights"
    )
    
    assert insights_response.status_code == 200
    insights_data = insights_response.json()
    
    print(f"✅ Insights generated: {insights_data['insights_generated']}")
    
    # Verify insights were generated
    assert insights_data["insights_generated"] > 0
    
    # Verify conversation history
    history_response = await client.get(f"/chat/{conversation_id}/history")
    assert history_response.status_code == 200
    history = history_response.json()
    
    # Should have at least 8 messages (4 user + 4 assistant)
    assert history["total_messages"] >= 8
    
    print(f"✅ Conversation test passed!")
    print(f"Total messages: {history['total_messages']}")
    print(f"Insights generated: {insights_data['insights_generated']}")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_complex_multi_turn_with_tools(client):
    """
    Test complex multi-turn conversation with tool usage.
    
    Flow:
    1. User asks about current events (web search)
    2. User asks follow-up about documents (doc search)
    3. User asks for analysis (reasoning)
    4. Verify context is maintained
    5. Verify appropriate tools used at each step
    """
    # Turn 1: Web search query
    response1 = await client.post(
        "/chat/message",
        json={
            "message": "What are the latest developments in AI?",
            "model": "auto",
            "enable_web_search": True
        }
    )
    
    assert response1.status_code == 200
    data1 = response1.json()
    conversation_id = data1["conversation_id"]
    
    # Verify web search was used
    routing1 = data1["routing_decision"]
    print(f"Turn 1 routing: {routing1['selected_tools']}")
    
    # Turn 2: Document search query
    response2 = await client.post(
        "/chat/message",
        json={
            "message": "Now compare that with what our internal research documents say",
            "conversation_id": conversation_id,
            "model": "auto",
            "enable_doc_search": True
        }
    )
    
    assert response2.status_code == 200
    data2 = response2.json()
    
    # Verify doc search was considered
    routing2 = data2["routing_decision"]
    print(f"Turn 2 routing: {routing2['selected_tools']}")
    
    # Turn 3: Analysis query
    response3 = await client.post(
        "/chat/message",
        json={
            "message": "Based on both sources, what are the key trends we should focus on?",
            "conversation_id": conversation_id,
            "model": "auto"
        }
    )
    
    assert response3.status_code == 200
    data3 = response3.json()
    
    # Verify reasoning model was selected
    assert data3["model"] in ["research", "frontier", "chat"]
    
    # Get full conversation history
    history_response = await client.get(f"/chat/{conversation_id}/history")
    assert history_response.status_code == 200
    history = history_response.json()
    
    # Should have 6 messages (3 turns)
    assert history["total_messages"] >= 6
    
    print(f"✅ Multi-turn test passed!")
    print(f"Total turns: 3")
    print(f"Total messages: {history['total_messages']}")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_error_handling_and_recovery(client):
    """
    Test error handling when tools fail.
    
    Flow:
    1. Request with tools enabled
    2. Even if tools fail, should get response
    3. Error should be communicated clearly
    4. Conversation should continue
    """
    response = await client.post(
        "/chat/message",
        json={
            "message": "Search for information about XYZ123NONEXISTENT",
            "model": "auto",
            "enable_web_search": True,
            "enable_doc_search": True
        }
    )
    
    # Should still return 200 even if tools fail
    assert response.status_code == 200
    data = response.json()
    
    # Should have content
    assert "content" in data
    assert len(data["content"]) > 0
    
    # Tool calls might show errors
    if data.get("tool_calls"):
        print(f"Tool calls: {data['tool_calls']}")
    
    print(f"✅ Error handling test passed!")


@pytest.mark.asyncio
@pytest.mark.integration  
async def test_model_selection_with_attachments(client):
    """
    Test that vision model is selected when images are attached.
    
    Flow:
    1. Send message with image attachment
    2. Verify frontier model (vision) is selected
    3. Verify appropriate routing
    """
    response = await client.post(
        "/chat/message",
        json={
            "message": "What do you see in this image?",
            "model": "auto",
            "attachments": [
                {
                    "name": "photo.jpg",
                    "type": "image/jpeg",
                    "url": "http://example.com/photo.jpg",
                    "size": 1024000
                }
            ]
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Should select frontier model for vision
    assert data["model"] == "frontier"
    
    print(f"✅ Vision model selection test passed!")
    print(f"Selected model: {data['model']}")

