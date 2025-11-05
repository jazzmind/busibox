"""
Unit tests for status endpoint (SSE streaming).
"""
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.mark.asyncio
@patch("api.routes.status.load_config")
@patch("api.routes.status.StatusService")
async def test_status_stream_success(
    mock_status_service,
    mock_load_config,
    client,
):
    """Test successful status stream."""
    # Setup mocks
    mock_config = Mock()
    mock_load_config.return_value = mock_config
    
    file_id = "123e4567-e89b-12d3-a456-426614174000"
    user_id = "123e4567-e89b-12d3-a456-426614174000"
    
    # Mock status updates stream
    async def mock_stream():
        updates = [
            {"fileId": file_id, "stage": "queued", "progress": 0},
            {"fileId": file_id, "stage": "parsing", "progress": 10},
            {"fileId": file_id, "stage": "chunking", "progress": 40, "totalChunks": 5},
            {"fileId": file_id, "stage": "embedding", "progress": 60, "chunksProcessed": 3, "totalChunks": 5},
            {"fileId": file_id, "stage": "completed", "progress": 100},
        ]
        for update in updates:
            yield update
    
    mock_status = Mock()
    mock_status.stream_status_updates = Mock(return_value=mock_stream())
    mock_status_service.return_value = mock_status
    
    # Make request (Note: TestClient doesn't fully support SSE, but we can test the route)
    response = client.get(
        f"/status/{file_id}",
        headers={"X-User-Id": user_id},
    )
    
    # Assertions
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    
    # Verify service was called
    mock_status.stream_status_updates.assert_called_once_with(file_id, user_id)


@pytest.mark.asyncio
@patch("api.routes.status.load_config")
@patch("api.routes.status.StatusService")
async def test_status_stream_error(
    mock_status_service,
    mock_load_config,
    client,
):
    """Test status stream with error."""
    # Setup mocks
    mock_config = Mock()
    mock_load_config.return_value = mock_config
    
    file_id = "123e4567-e89b-12d3-a456-426614174000"
    user_id = "123e4567-e89b-12d3-a456-426614174000"
    
    # Mock status service error
    async def mock_stream_error():
        raise Exception("Database connection failed")
        yield  # Make it a generator
    
    mock_status = Mock()
    mock_status.stream_status_updates = Mock(return_value=mock_stream_error())
    mock_status_service.return_value = mock_status
    
    # Make request
    response = client.get(
        f"/status/{file_id}",
        headers={"X-User-Id": user_id},
    )
    
    # Assertions
    assert response.status_code == 200  # SSE stream starts even if error occurs
    # Error should be in the stream data

