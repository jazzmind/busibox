"""
Unit tests for upload endpoint.
"""
import json
from io import BytesIO
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_request():
    """Mock request with user_id."""
    request = Mock(spec=Request)
    request.state.user_id = "123e4567-e89b-12d3-a456-426614174000"  # Valid UUID
    return request


@pytest.fixture
def sample_file():
    """Create sample file for upload."""
    content = b"Test document content"
    file = BytesIO(content)
    file.name = "test.txt"
    file.content_type = "text/plain"
    file.size = len(content)
    return file


@pytest.mark.asyncio
@patch("api.routes.upload.load_config")
@patch("api.routes.upload.MinIOService")
@patch("api.routes.upload.PostgresService")
@patch("api.routes.upload.RedisService")
async def test_upload_success(
    mock_redis_service,
    mock_postgres_service,
    mock_minio_service,
    mock_load_config,
    client,
    sample_file,
):
    """Test successful file upload."""
    # Setup mocks
    mock_config = Mock()
    mock_load_config.return_value = mock_config
    
    mock_minio = Mock()
    mock_minio.upload_file_stream = AsyncMock(return_value="hash-abc123")
    mock_minio_service.return_value = mock_minio
    
    mock_postgres = Mock()
    mock_postgres.connect = AsyncMock()
    mock_postgres.disconnect = AsyncMock()
    mock_postgres.check_duplicate = AsyncMock(return_value=None)
    mock_postgres.create_file_record = AsyncMock()
    mock_postgres_service.return_value = mock_postgres
    
    mock_redis = Mock()
    mock_redis.connect = AsyncMock()
    mock_redis.disconnect = AsyncMock()
    mock_redis.ensure_consumer_group = AsyncMock()
    mock_redis.add_job = AsyncMock()
    mock_redis_service.return_value = mock_redis
    
    # Make request
    response = client.post(
        "/upload",
        headers={"X-User-Id": "123e4567-e89b-12d3-a456-426614174000"},
        files={"file": ("test.txt", sample_file, "text/plain")},
    )
    
    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert "fileId" in data
    assert data["status"] == "queued"
    assert data["duplicate"] is False
    
    # Verify services were called
    mock_minio.upload_file_stream.assert_called_once()
    mock_postgres.create_file_record.assert_called_once()
    mock_redis.add_job.assert_called_once()


@pytest.mark.asyncio
@patch("api.routes.upload.load_config")
@patch("api.routes.upload.MinIOService")
@patch("api.routes.upload.PostgresService")
@patch("api.routes.upload.RedisService")
async def test_upload_duplicate_detection(
    mock_redis_service,
    mock_postgres_service,
    mock_minio_service,
    mock_load_config,
    client,
    sample_file,
):
    """Test duplicate file detection and vector reuse."""
    # Setup mocks
    mock_config = Mock()
    mock_load_config.return_value = mock_config
    
    mock_minio = Mock()
    mock_minio.upload_file_stream = AsyncMock(return_value="hash-existing")
    mock_minio_service.return_value = mock_minio
    
    mock_postgres = Mock()
    mock_postgres.connect = AsyncMock()
    mock_postgres.disconnect = AsyncMock()
    mock_postgres.check_duplicate = AsyncMock(return_value={
        "file_id": "file-existing-123",
        "content_hash": "hash-existing",
    })
    mock_postgres.reuse_vectors = AsyncMock()
    mock_postgres_service.return_value = mock_postgres
    
    mock_redis = Mock()
    mock_redis.connect = AsyncMock()
    mock_redis.disconnect = AsyncMock()
    mock_redis_service.return_value = mock_redis
    
    # Make request
    response = client.post(
        "/upload",
        headers={"X-User-Id": "123e4567-e89b-12d3-a456-426614174000"},
        files={"file": ("test.txt", sample_file, "text/plain")},
    )
    
    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["duplicate"] is True
    assert "existingFileId" in data
    
    # Verify vector reuse was called
    mock_postgres.reuse_vectors.assert_called_once()
    # Verify job was NOT queued (duplicate)
    mock_redis.add_job.assert_not_called()


def test_upload_invalid_mime_type(client, sample_file):
    """Test upload with unsupported MIME type."""
    response = client.post(
        "/upload",
        headers={"X-User-Id": "123e4567-e89b-12d3-a456-426614174000"},
        files={"file": ("test.exe", sample_file, "application/x-msdownload")},
    )
    
    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    assert "Unsupported file type" in data["error"]


def test_upload_missing_filename(client):
    """Test upload without filename."""
    response = client.post(
        "/upload",
        headers={"X-User-Id": "123e4567-e89b-12d3-a456-426614174000"},
        files={"file": ("", BytesIO(b"content"), "text/plain")},
    )
    
    # FastAPI returns 422 for validation errors, but our code checks filename and returns 400
    # In test, if filename is empty string, FastAPI validation might happen first
    assert response.status_code in [400, 422]
    if response.status_code == 400:
        data = response.json()
        assert "error" in data
        assert "Filename required" in data["error"]


@pytest.mark.asyncio
@patch("api.routes.upload.load_config")
@patch("api.routes.upload.MinIOService")
@patch("api.routes.upload.PostgresService")
@patch("api.routes.upload.RedisService")
async def test_upload_with_metadata(
    mock_redis_service,
    mock_postgres_service,
    mock_minio_service,
    mock_load_config,
    client,
    sample_file,
):
    """Test upload with valid JSON metadata."""
    # Setup mocks
    mock_config = Mock()
    mock_load_config.return_value = mock_config
    
    mock_minio = Mock()
    mock_minio.upload_file_stream = AsyncMock(return_value="hash-abc123")
    mock_minio_service.return_value = mock_minio
    
    mock_postgres = Mock()
    mock_postgres.connect = AsyncMock()
    mock_postgres.disconnect = AsyncMock()
    mock_postgres.check_duplicate = AsyncMock(return_value=None)
    mock_postgres.create_file_record = AsyncMock()
    mock_postgres_service.return_value = mock_postgres
    
    mock_redis = Mock()
    mock_redis.connect = AsyncMock()
    mock_redis.disconnect = AsyncMock()
    mock_redis.ensure_consumer_group = AsyncMock()
    mock_redis.add_job = AsyncMock()
    mock_redis_service.return_value = mock_redis
    
    # Metadata as JSON string
    metadata = json.dumps({"title": "Test Document", "author": "Test Author"})
    
    # Make request
    response = client.post(
        "/upload",
        headers={"X-User-Id": "123e4567-e89b-12d3-a456-426614174000"},
        files={"file": ("test.txt", sample_file, "text/plain")},
        data={"metadata": metadata},
    )
    
    # Assertions
    assert response.status_code == 200
    
    # Verify metadata was parsed and passed to create_file_record
    call_args = mock_postgres.create_file_record.call_args
    assert call_args is not None
    assert call_args.kwargs["metadata"] == {"title": "Test Document", "author": "Test Author"}


@pytest.mark.asyncio
@patch("api.routes.upload.load_config")
@patch("api.routes.upload.MinIOService")
@patch("api.routes.upload.PostgresService")
@patch("api.routes.upload.RedisService")
async def test_upload_invalid_metadata(
    mock_redis_service,
    mock_postgres_service,
    mock_minio_service,
    mock_load_config,
    client,
    sample_file,
):
    """Test upload with invalid JSON metadata (should use empty dict)."""
    # Setup mocks
    mock_config = Mock()
    mock_load_config.return_value = mock_config
    
    mock_minio = Mock()
    mock_minio.upload_file_stream = AsyncMock(return_value="hash-abc123")
    mock_minio_service.return_value = mock_minio
    
    mock_postgres = Mock()
    mock_postgres.connect = AsyncMock()
    mock_postgres.disconnect = AsyncMock()
    mock_postgres.check_duplicate = AsyncMock(return_value=None)
    mock_postgres.create_file_record = AsyncMock()
    mock_postgres_service.return_value = mock_postgres
    
    mock_redis = Mock()
    mock_redis.connect = AsyncMock()
    mock_redis.disconnect = AsyncMock()
    mock_redis.ensure_consumer_group = AsyncMock()
    mock_redis.add_job = AsyncMock()
    mock_redis_service.return_value = mock_redis
    
    # Invalid JSON metadata
    invalid_metadata = "{invalid json}"
    
    # Make request
    response = client.post(
        "/upload",
        headers={"X-User-Id": "123e4567-e89b-12d3-a456-426614174000"},
        files={"file": ("test.txt", sample_file, "text/plain")},
        data={"metadata": invalid_metadata},
    )
    
    # Should still succeed, but with empty metadata
    assert response.status_code == 200
    
    # Verify empty metadata was used
    call_args = mock_postgres.create_file_record.call_args
    assert call_args.kwargs["metadata"] == {}

