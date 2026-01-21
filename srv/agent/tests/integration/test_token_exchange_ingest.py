"""
Integration tests for Zero Trust token exchange to ingest-api.

Tests that the agent-api can properly exchange tokens using Zero Trust mode
(passing the user's JWT as subject_token) to get ingest-api audience tokens
for embedding generation and content ingestion.
"""

import pytest

from app.auth.tokens import get_service_token


async def _get_user_token(test_user_id: str) -> str:
    """
    Get a valid user JWT for testing.
    
    In Zero Trust mode, we need the user's actual JWT to exchange.
    This gets a token from AuthZ using the bootstrap client.
    """
    import httpx
    import os
    
    authz_url = os.environ.get(
        "AUTH_TOKEN_URL",
        os.environ.get("AUTHZ_TOKEN_URL", "http://authz-api:8010/oauth/token")
    )
    bootstrap_client_id = os.environ.get("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
    bootstrap_client_secret = os.environ.get("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            authz_url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": bootstrap_client_id,
                "client_secret": bootstrap_client_secret,
                "requested_subject": test_user_id,
                "audience": "agent-api",
                "scope": "read write",
            },
        )
        
        if response.status_code != 200:
            raise ValueError(f"Failed to get user token: {response.status_code} - {response.text}")
        
        return response.json()["access_token"]


@pytest.mark.asyncio
async def test_token_exchange_to_ingest_api(test_user_id: str):
    """
    Test that we can exchange tokens to get an ingest-api token using Zero Trust.
    
    This is critical for:
    - Saving task insights (requires embedding generation)
    - Saving task outputs to document library (requires content ingestion)
    """
    # First get a user JWT (agent-api audience)
    user_token = await _get_user_token(test_user_id)
    
    # Exchange it for an ingest-api token using Zero Trust
    ingest_token = await get_service_token(
        user_token=user_token,
        user_id=test_user_id,
        target_audience="ingest-api",
    )
    
    assert ingest_token is not None
    assert len(ingest_token) > 0
    # JWT format check (header.payload.signature)
    assert ingest_token.count(".") == 2, "Token should be in JWT format"


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
async def test_ingest_api_embedding_endpoint_with_exchanged_token(test_user_id: str):
    """
    Test that the exchanged ingest-api token can call the embeddings endpoint.
    
    This validates the full Zero Trust flow:
    1. Get user token for agent-api audience
    2. Exchange to ingest-api audience using subject_token (no client creds)
    3. Call ingest-api embeddings endpoint with that token
    4. Get embeddings back
    """
    import httpx
    from app.config.settings import get_settings
    
    settings = get_settings()
    
    # Get a user JWT first
    user_token = await _get_user_token(test_user_id)
    
    # Exchange for ingest-api token
    ingest_token = await get_service_token(
        user_token=user_token,
        user_id=test_user_id,
        target_audience="ingest-api",
    )
    
    # Call the embeddings endpoint
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Remove trailing slash to avoid double slashes
        base_url = str(settings.ingest_api_url).rstrip("/")
        
        response = await client.post(
            f"{base_url}/api/embeddings",
            json={"input": "Test text for embedding generation"},
            headers={"Authorization": f"Bearer {ingest_token}"},
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
async def test_ingest_api_content_endpoint_with_exchanged_token(test_user_id: str):
    """
    Test that the exchanged ingest-api token can call the content ingestion endpoint.
    """
    import httpx
    from app.config.settings import get_settings
    
    settings = get_settings()
    
    # Get a user JWT first
    user_token = await _get_user_token(test_user_id)
    
    # Exchange for ingest-api token
    ingest_token = await get_service_token(
        user_token=user_token,
        user_id=test_user_id,
        target_audience="ingest-api",
    )
    
    # Call the content ingestion endpoint
    async with httpx.AsyncClient(timeout=60.0) as client:
        base_url = str(settings.ingest_api_url).rstrip("/")
        
        response = await client.post(
            f"{base_url}/ingest/content",
            json={
                "content": "Test content for integration test",
                "title": "Integration Test Document",
                "folder": "personal-tasks",
            },
            headers={"Authorization": f"Bearer {ingest_token}"},
        )
        
        # Should not be 401 Unauthorized
        assert response.status_code != 401, f"Token was rejected: {response.text}"
        
        # Note: May fail for other reasons (library not found, etc.)
        # but the token should be accepted
        if response.status_code == 200 or response.status_code == 201:
            data = response.json()
            # Verify we got a document ID back
            assert data.get("document_id") or data.get("id") or data.get("fileId")
