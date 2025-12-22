"""
Integration tests for insights API endpoints.

Tests the chat insights functionality migrated from search-api to agent-api.
All tests use proper Bearer token authentication via get_principal mocking.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.schemas.auth import Principal
import time


@pytest.fixture
def test_principal():
    """Create a test principal for authentication."""
    return Principal(
        sub="test-user",
        iss="busibox-authz",
        aud="busibox-services",
        exp=int(time.time()) + 3600,
        iat=int(time.time()),
        scope="insights.read insights.write",
    )


@pytest.fixture
def other_principal():
    """Create another test principal for authorization tests."""
    return Principal(
        sub="other-user",
        iss="busibox-authz",
        aud="busibox-services",
        exp=int(time.time()) + 3600,
        iat=int(time.time()),
        scope="insights.read insights.write",
    )


@pytest.fixture
async def authenticated_client(test_principal):
    """HTTP client authenticated as test-user."""
    from app.auth.dependencies import get_principal
    
    async def override_get_principal():
        return test_principal
    
    # Store original overrides
    original_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_principal] = override_get_principal
    
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        # Restore original overrides
        app.dependency_overrides = original_overrides


@pytest.fixture
async def other_authenticated_client(other_principal):
    """HTTP client authenticated as other-user."""
    from app.auth.dependencies import get_principal
    
    async def override_get_principal():
        return other_principal
    
    # Store original overrides
    original_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_principal] = override_get_principal
    
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        # Restore original overrides
        app.dependency_overrides = original_overrides


@pytest.mark.asyncio
async def test_initialize_insights_collection(authenticated_client):
    """Test initializing the insights collection."""
    response = await authenticated_client.post("/insights/init")
    
    assert response.status_code == 200
    data = response.json()
    assert data["collection"] == "chat_insights"
    assert "message" in data


@pytest.mark.asyncio
async def test_insert_insights(authenticated_client):
    """Test inserting insights."""
    # First initialize collection
    await authenticated_client.post("/insights/init")
    
    # Create test insights with embeddings
    insights = [
        {
            "id": f"test-insight-{int(time.time())}-1",
            "userId": "test-user",
            "content": "User prefers Python for backend development",
            "embedding": [0.1] * 1024,  # Mock embedding
            "conversationId": "test-conv-1",
            "analyzedAt": int(time.time())
        },
        {
            "id": f"test-insight-{int(time.time())}-2",
            "userId": "test-user",
            "content": "User is interested in machine learning",
            "embedding": [0.2] * 1024,  # Mock embedding
            "conversationId": "test-conv-1",
            "analyzedAt": int(time.time())
        }
    ]
    
    response = await authenticated_client.post(
        "/insights",
        json={"insights": insights}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["inserted_count"] == 2


@pytest.mark.asyncio
async def test_insert_insights_wrong_user(authenticated_client):
    """Test that users cannot insert insights for other users."""
    await authenticated_client.post("/insights/init")
    
    # Try to insert insights for a different user
    insights = [
        {
            "id": f"test-insight-{int(time.time())}-1",
            "userId": "other-user",  # Different user!
            "content": "Test insight",
            "embedding": [0.1] * 1024,
            "conversationId": "test-conv-1",
            "analyzedAt": int(time.time())
        }
    ]
    
    response = await authenticated_client.post(
        "/insights",
        json={"insights": insights}
    )
    
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_search_insights(authenticated_client):
    """Test searching insights."""
    # Initialize and insert test data
    await authenticated_client.post("/insights/init")
    
    insights = [
        {
            "id": f"test-insight-{int(time.time())}-1",
            "userId": "test-user",
            "content": "User prefers Python",
            "embedding": [0.1] * 1024,
            "conversationId": "test-conv-1",
            "analyzedAt": int(time.time())
        }
    ]
    
    await authenticated_client.post(
        "/insights",
        json={"insights": insights}
    )
    
    # Flush to ensure data is persisted
    await authenticated_client.post("/insights/flush")
    
    # Search for insights
    response = await authenticated_client.post(
        "/insights/search",
        json={
            "query": "What programming languages does the user like?",
            "userId": "test-user",
            "limit": 5,
            "scoreThreshold": 2.0
        }
    )
    
    # API should handle embedding service unavailability gracefully with 503 (service unavailable)
    # Never 500 - that indicates uncaught exception
    assert response.status_code in [200, 503], \
        f"Expected 200 or 503, got {response.status_code}: {response.text}"


@pytest.mark.asyncio
async def test_search_insights_wrong_user(authenticated_client):
    """Test that users cannot search other users' insights."""
    response = await authenticated_client.post(
        "/insights/search",
        json={
            "query": "test query",
            "userId": "other-user",  # Different user!
            "limit": 5
        }
    )
    
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_user_stats(authenticated_client):
    """Test getting user statistics."""
    # Initialize collection
    await authenticated_client.post("/insights/init")
    
    # Get stats
    response = await authenticated_client.get("/insights/stats/test-user")
    
    assert response.status_code == 200
    data = response.json()
    assert "userId" in data
    assert "totalInsights" in data
    assert data["userId"] == "test-user"


@pytest.mark.asyncio
async def test_get_user_stats_wrong_user(authenticated_client):
    """Test that users cannot view other users' stats."""
    response = await authenticated_client.get("/insights/stats/other-user")
    
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_conversation_insights(authenticated_client):
    """Test deleting insights for a conversation."""
    # Initialize and insert test data
    await authenticated_client.post("/insights/init")
    
    insights = [
        {
            "id": f"test-insight-{int(time.time())}-1",
            "userId": "test-user",
            "content": "Test insight",
            "embedding": [0.1] * 1024,
            "conversationId": "test-conv-delete",
            "analyzedAt": int(time.time())
        }
    ]
    
    await authenticated_client.post(
        "/insights",
        json={"insights": insights}
    )
    
    # Delete conversation insights
    response = await authenticated_client.delete("/insights/conversation/test-conv-delete")
    
    assert response.status_code == 200
    data = response.json()
    assert "deleted_count" in data


@pytest.mark.asyncio
async def test_delete_user_insights(authenticated_client):
    """Test deleting all insights for a user."""
    # Initialize and insert test data
    await authenticated_client.post("/insights/init")
    
    insights = [
        {
            "id": f"test-insight-{int(time.time())}-1",
            "userId": "test-user",
            "content": "Test insight",
            "embedding": [0.1] * 1024,
            "conversationId": "test-conv-1",
            "analyzedAt": int(time.time())
        }
    ]
    
    await authenticated_client.post(
        "/insights",
        json={"insights": insights}
    )
    
    # Delete user insights
    response = await authenticated_client.delete("/insights/user/test-user")
    
    assert response.status_code == 200
    data = response.json()
    assert "deleted_count" in data


@pytest.mark.asyncio
async def test_delete_user_insights_wrong_user(authenticated_client):
    """Test that users cannot delete other users' insights."""
    response = await authenticated_client.delete("/insights/user/other-user")
    
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_flush_collection(authenticated_client):
    """Test flushing the collection."""
    # Initialize collection
    await authenticated_client.post("/insights/init")
    
    # Flush collection
    response = await authenticated_client.post("/insights/flush")
    
    assert response.status_code == 200
    data = response.json()
    assert "message" in data


@pytest.mark.asyncio
async def test_authorization_isolation(authenticated_client, other_authenticated_client):
    """Test that users can only access their own insights."""
    # User 1 inserts insights
    await authenticated_client.post("/insights/init")
    
    insights = [
        {
            "id": f"test-insight-{int(time.time())}-1",
            "userId": "test-user",
            "content": "User 1's insight",
            "embedding": [0.1] * 1024,
            "conversationId": "test-conv-1",
            "analyzedAt": int(time.time())
        }
    ]
    
    await authenticated_client.post(
        "/insights",
        json={"insights": insights}
    )
    
    # User 2 tries to search User 1's insights
    response = await other_authenticated_client.post(
        "/insights/search",
        json={
            "query": "test",
            "userId": "test-user",  # User 1's ID
            "limit": 5
        }
    )
    
    # Should be forbidden
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_request():
    """Test that unauthenticated requests are rejected."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/insights/init")
        # FastAPI returns 422 for missing required header
        assert response.status_code == 422
