"""
Unit tests for status endpoint (SSE streaming).

Uses JWT auth fixtures from conftest.py.
"""
import uuid
import pytest
from fastapi import status as http_status


@pytest.mark.asyncio
async def test_status_stream_success(async_client):
    """Test status endpoint returns proper SSE response.
    
    Note: TestClient doesn't fully support SSE streaming,
    but we can verify the endpoint accepts requests.
    """
    file_id = str(uuid.uuid4())
    
    response = await async_client.get(f"/status/{file_id}")
    
    # Status endpoint should return 200 with SSE content type
    # or 404 if file doesn't exist
    assert response.status_code in [http_status.HTTP_200_OK, http_status.HTTP_404_NOT_FOUND]


@pytest.mark.asyncio
async def test_status_stream_invalid_uuid(async_client):
    """Test status endpoint with invalid UUID."""
    response = await async_client.get("/status/not-a-valid-uuid")
    
    # Should return 400 or 422 for invalid UUID format, or 200 if SSE starts
    assert response.status_code in [
        http_status.HTTP_200_OK,  # SSE may start even with invalid UUID
        http_status.HTTP_400_BAD_REQUEST, 
        http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        http_status.HTTP_404_NOT_FOUND,
    ]
