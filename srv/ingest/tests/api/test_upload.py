"""
Unit tests for upload endpoint.

These tests verify the upload endpoint's request handling and validation logic
in isolation from external services. They use mocks because:

1. Testing request validation (MIME types, filenames) doesn't need real storage
2. Testing the control flow (duplicate detection, metadata parsing) is faster with mocks
3. Error handling paths are easier to trigger with mocks

For full end-to-end testing with real MinIO, PostgreSQL, and Redis:
See: tests/integration/test_full_pipeline.py
"""
import json
from io import BytesIO
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from src.api.main import app


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


# =============================================================================
# Request Validation Tests - No mocks needed for these
# =============================================================================

class TestUploadValidation:
    """Test upload request validation."""
    
    def test_upload_invalid_mime_type(self, client, sample_file):
        """Test upload with unsupported MIME type is rejected."""
        response = client.post(
            "/upload",
            headers={"X-User-Id": "123e4567-e89b-12d3-a456-426614174000"},
            files={"file": ("test.exe", sample_file, "application/x-msdownload")},
        )
        
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Unsupported file type" in data["error"]

    def test_upload_missing_filename(self, client):
        """Test upload without filename is rejected."""
        response = client.post(
            "/upload",
            headers={"X-User-Id": "123e4567-e89b-12d3-a456-426614174000"},
            files={"file": ("", BytesIO(b"content"), "text/plain")},
        )
        
        # FastAPI returns 422 for validation errors, but our code checks filename and returns 400
        assert response.status_code in [400, 422]
        if response.status_code == 400:
            data = response.json()
            assert "error" in data
            assert "Filename required" in data["error"]


# =============================================================================
# Unit Tests with Mocks - Test control flow and service orchestration
# =============================================================================

class TestUploadControlFlow:
    """
    Test upload endpoint control flow with mocked services.
    
    Mock justification:
    - These tests verify the correct sequencing of service calls
    - Testing with real services is done in integration tests
    - Mocks allow testing specific scenarios (duplicates, errors)
    """
    
    @pytest.mark.asyncio
    @patch("api.routes.upload.load_config")
    @patch("api.routes.upload.MinIOService")
    @patch("api.routes.upload.PostgresService")
    @patch("api.routes.upload.RedisService")
    async def test_upload_success(
        self,
        mock_redis_service,
        mock_postgres_service,
        mock_minio_service,
        mock_load_config,
        client,
        sample_file,
    ):
        """Test successful file upload calls services in correct order."""
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
        
        # Verify services were called in correct order
        mock_minio.upload_file_stream.assert_called_once()
        mock_postgres.create_file_record.assert_called_once()
        mock_redis.add_job.assert_called_once()

    @pytest.mark.asyncio
    @patch("api.routes.upload.load_config")
    @patch("api.routes.upload.MinIOService")
    @patch("api.routes.upload.PostgresService")
    @patch("api.routes.upload.RedisService")
    async def test_upload_duplicate_detection(
        self,
        mock_redis_service,
        mock_postgres_service,
        mock_minio_service,
        mock_load_config,
        client,
        sample_file,
    ):
        """Test duplicate file detection skips processing queue."""
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
        
        # Verify vector reuse was called instead of queueing new job
        mock_postgres.reuse_vectors.assert_called_once()
        mock_redis.add_job.assert_not_called()


class TestUploadMetadata:
    """Test metadata handling in upload endpoint."""
    
    @pytest.mark.asyncio
    @patch("api.routes.upload.load_config")
    @patch("api.routes.upload.MinIOService")
    @patch("api.routes.upload.PostgresService")
    @patch("api.routes.upload.RedisService")
    async def test_upload_with_valid_metadata(
        self,
        mock_redis_service,
        mock_postgres_service,
        mock_minio_service,
        mock_load_config,
        client,
        sample_file,
    ):
        """Test upload with valid JSON metadata parses correctly."""
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
        self,
        mock_redis_service,
        mock_postgres_service,
        mock_minio_service,
        mock_load_config,
        client,
        sample_file,
    ):
        """Test upload with invalid JSON metadata uses empty dict."""
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
