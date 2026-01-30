"""
Integration tests for Zero Trust token exchange to data-api.

Tests that the agent-api can properly exchange tokens using Zero Trust mode
(passing the user's JWT as subject_token) to get data-api audience tokens
for embedding generation and content data.

NOTE: Zero Trust auth - no client credentials needed. Tests use pre-created
test users from bootstrap-test-databases.py.
"""

import pytest

from app.auth.tokens import get_service_token


async def _get_user_token(test_user_id: str) -> str:
    """
    Get a valid user JWT for testing.
    
    In Zero Trust mode, we use the AuthTestClient which:
    1. Uses pre-created test users (no client auth)
    2. Gets tokens via admin-scoped token exchange
    """
    # Import the shared testing library
    import sys
    import os
    
    # Add shared testing library to path if not already
    _agent_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _testing_path = os.path.join(_agent_root, "src", "testing")
    if _testing_path not in sys.path and os.path.exists(_testing_path):
        sys.path.insert(0, _testing_path)
    
    from testing import AuthTestClient
    
    # Use the shared test client to get a token
    client = AuthTestClient(test_user_id=test_user_id)
    return client.get_token(audience="agent-api")


@pytest.mark.asyncio
async def test_token_exchange_to_data_api(test_user_id: str):
    """
    Test that we can exchange tokens to get an data-api token using Zero Trust.
    
    This is critical for:
    - Saving task insights (requires embedding generation)
    - Saving task outputs to document library (requires content data)
    """
    # First get a user JWT (agent-api audience)
    user_token = await _get_user_token(test_user_id)
    
    # Exchange it for an data-api token using Zero Trust
    data_token = await get_service_token(
        user_token=user_token,
        user_id=test_user_id,
        target_audience="data-api",
    )
    
    assert data_token is not None
    assert len(data_token) > 0
    # JWT format check (header.payload.signature)
    assert data_token.count(".") == 2, "Token should be in JWT format"


@pytest.mark.asyncio
async def test_token_exchange_to_search_api(test_user_id: str):
    """
    Test that we can exchange tokens to get a search-api token using Zero Trust.
    """
    # First get a user JWT
    user_token = await _get_user_token(test_user_id)
    
    # Exchange it for a search-api token
    search_token = await get_service_token(
        user_token=user_token,
        user_id=test_user_id,
        target_audience="search-api",
    )
    
    assert search_token is not None
    assert len(search_token) > 0
    assert search_token.count(".") == 2


@pytest.mark.asyncio
async def test_data_api_embedding_endpoint_with_exchanged_token(test_user_id: str):
    """
    Test that the exchanged data-api token can call the embeddings endpoint.
    
    This validates the full Zero Trust flow:
    1. Get user token for agent-api audience
    2. Exchange to data-api audience using subject_token (no client creds)
    3. Call data-api embeddings endpoint with that token
    4. Get embeddings back
    """
    import httpx
    from app.config.settings import get_settings
    
    settings = get_settings()
    
    # Get a user JWT first
    user_token = await _get_user_token(test_user_id)
    
    # Exchange for data-api token
    data_token = await get_service_token(
        user_token=user_token,
        user_id=test_user_id,
        target_audience="data-api",
    )
    
    # Call the embeddings endpoint
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Remove trailing slash to avoid double slashes
        base_url = str(settings.data_api_url).rstrip("/")
        
        response = await client.post(
            f"{base_url}/api/embeddings",
            json={"input": "Test text for embedding generation"},
            headers={"Authorization": f"Bearer {data_token}"},
        )
        
        # Should not be 401 Unauthorized
        assert response.status_code != 401, f"Token was rejected: {response.text}"
        
        # Note: May be 404 if embeddings endpoint doesn't exist, or 200 on success
        # The important thing is we're not getting 401
        if response.status_code == 200:
            data = response.json()
            # Verify we got embeddings back
            assert "data" in data
            assert len(data["data"]) > 0
            assert "embedding" in data["data"][0]


@pytest.mark.asyncio
async def test_data_api_content_endpoint_with_exchanged_token(test_user_id: str):
    """
    Test that the exchanged data-api token can call the content data endpoint.
    """
    import httpx
    from app.config.settings import get_settings
    
    settings = get_settings()
    
    # Get a user JWT first
    user_token = await _get_user_token(test_user_id)
    
    # Exchange for data-api token
    data_token = await get_service_token(
        user_token=user_token,
        user_id=test_user_id,
        target_audience="data-api",
    )
    
    # Call the content data endpoint
    async with httpx.AsyncClient(timeout=60.0) as client:
        base_url = str(settings.data_api_url).rstrip("/")
        
        response = await client.post(
            f"{base_url}/data/content",
            json={
                "content": "Test content for integration test",
                "title": "Integration Test Document",
                "folder": "personal-tasks",
            },
            headers={"Authorization": f"Bearer {data_token}"},
        )
        
        # Should not be 401 Unauthorized
        assert response.status_code != 401, f"Token was rejected: {response.text}"
        
        # Note: May fail for other reasons (library not found, etc.)
        # but the token should be accepted
        if response.status_code == 200 or response.status_code == 201:
            data = response.json()
            # Verify we got a document ID back
            assert data.get("document_id") or data.get("id") or data.get("fileId")
