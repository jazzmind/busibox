"""
Unit tests for files endpoint (metadata retrieval and deletion).
"""
import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.mark.asyncio
@patch("api.routes.files.load_config")
@patch("api.routes.files.PostgresService")
async def test_get_file_metadata_success(
    mock_postgres_service,
    mock_load_config,
    client,
):
    """Test successful file metadata retrieval."""
    # Setup mocks
    mock_config = Mock()
    mock_load_config.return_value = mock_config
    
    file_id = str(uuid.uuid4())
    user_id = "123e4567-e89b-12d3-a456-426614174000"
    
    # Mock PostgreSQL
    mock_postgres = Mock()
    mock_postgres.connect = AsyncMock()
    mock_postgres.disconnect = AsyncMock()
    mock_pool = Mock()
    mock_conn = AsyncMock()
    
    # Mock file record
    mock_file_row = Mock()
    mock_file_row.__getitem__ = Mock(side_effect=lambda key: {
        "file_id": uuid.UUID(file_id),
        "user_id": uuid.UUID(user_id),
        "filename": "test.pdf",
        "original_filename": "test.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 1024,
        "storage_path": f"{user_id}/{file_id}/test.pdf",
        "content_hash": "hash-abc123",
        "document_type": "document",
        "primary_language": "en",
        "detected_languages": ["en"],
        "classification_confidence": 0.95,
        "chunk_count": 5,
        "vector_count": 5,
        "processing_duration_seconds": 10.5,
        "extracted_title": "Test Document",
        "extracted_author": "Test Author",
        "extracted_date": None,
        "extracted_keywords": ["test", "document"],
        "metadata": {},
        "permissions": {},
        "created_at": Mock(isoformat=Mock(return_value="2025-01-01T00:00:00")),
        "updated_at": Mock(isoformat=Mock(return_value="2025-01-01T00:01:00")),
    }.get(key))
    
    # Mock status record
    mock_status_row = Mock()
    mock_status_row.__getitem__ = Mock(side_effect=lambda key: {
        "stage": "completed",
        "progress": 100,
        "chunks_processed": 5,
        "total_chunks": 5,
        "pages_processed": None,
        "total_pages": None,
        "error_message": None,
        "started_at": Mock(isoformat=Mock(return_value="2025-01-01T00:00:00")),
        "completed_at": Mock(isoformat=Mock(return_value="2025-01-01T00:00:10")),
        "updated_at": Mock(isoformat=Mock(return_value="2025-01-01T00:00:10")),
    }.get(key))
    
    mock_conn.fetchrow = AsyncMock(side_effect=[mock_file_row, mock_status_row])
    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire = Mock(return_value=mock_acquire)
    mock_postgres.pool = mock_pool
    mock_postgres_service.return_value = mock_postgres
    
    # Make request
    response = client.get(
        f"/files/{file_id}",
        headers={"X-User-Id": user_id},
    )
    
    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["fileId"] == file_id
    assert data["filename"] == "test.pdf"
    assert data["status"]["stage"] == "completed"
    assert data["status"]["progress"] == 100


@pytest.mark.asyncio
@patch("api.routes.files.load_config")
@patch("api.routes.files.PostgresService")
async def test_get_file_metadata_not_found(
    mock_postgres_service,
    mock_load_config,
    client,
):
    """Test file metadata retrieval when file doesn't exist."""
    # Setup mocks
    mock_config = Mock()
    mock_load_config.return_value = mock_config
    
    file_id = str(uuid.uuid4())
    user_id = "123e4567-e89b-12d3-a456-426614174000"
    
    # Mock PostgreSQL
    mock_postgres = Mock()
    mock_postgres.connect = AsyncMock()
    mock_postgres.disconnect = AsyncMock()
    mock_pool = Mock()
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)  # File not found
    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire = Mock(return_value=mock_acquire)
    mock_postgres.pool = mock_pool
    mock_postgres_service.return_value = mock_postgres
    
    # Make request
    response = client.get(
        f"/files/{file_id}",
        headers={"X-User-Id": user_id},
    )
    
    # Assertions
    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert "not found" in data["error"].lower()


@pytest.mark.asyncio
@patch("api.routes.files.load_config")
@patch("api.routes.files.PostgresService")
async def test_get_file_metadata_unauthorized(
    mock_postgres_service,
    mock_load_config,
    client,
):
    """Test file metadata retrieval when user doesn't own the file."""
    # Setup mocks
    mock_config = Mock()
    mock_load_config.return_value = mock_config
    
    file_id = str(uuid.uuid4())
    user_id = "123e4567-e89b-12d3-a456-426614174000"
    other_user_id = "223e4567-e89b-12d3-a456-426614174001"
    
    # Mock PostgreSQL
    mock_postgres = Mock()
    mock_postgres.connect = AsyncMock()
    mock_postgres.disconnect = AsyncMock()
    mock_pool = Mock()
    mock_conn = AsyncMock()
    
    # Mock file record with different user_id
    mock_file_row = Mock()
    mock_file_row.__getitem__ = Mock(side_effect=lambda key: {
        "file_id": uuid.UUID(file_id),
        "user_id": uuid.UUID(other_user_id),  # Different user
    }.get(key))
    
    mock_conn.fetchrow = AsyncMock(return_value=mock_file_row)
    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire = Mock(return_value=mock_acquire)
    mock_postgres.pool = mock_pool
    mock_postgres_service.return_value = mock_postgres
    
    # Make request
    response = client.get(
        f"/files/{file_id}",
        headers={"X-User-Id": user_id},
    )
    
    # Assertions
    assert response.status_code == 403
    data = response.json()
    assert "error" in data
    assert "unauthorized" in data["error"].lower()


@pytest.mark.asyncio
@patch("api.routes.files.load_config")
@patch("api.routes.files.PostgresService")
@patch("api.routes.files.MinIOService")
async def test_delete_file_success(
    mock_minio_service,
    mock_postgres_service,
    mock_load_config,
    client,
):
    """Test successful file deletion."""
    # Setup mocks
    mock_config = Mock()
    mock_load_config.return_value = mock_config
    
    file_id = str(uuid.uuid4())
    user_id = "123e4567-e89b-12d3-a456-426614174000"
    
    # Mock PostgreSQL
    mock_postgres = Mock()
    mock_postgres.connect = AsyncMock()
    mock_postgres.disconnect = AsyncMock()
    mock_pool = Mock()
    mock_conn = AsyncMock()
    
    # Mock file record
    mock_file_row = Mock()
    mock_file_row.__getitem__ = Mock(side_effect=lambda key: {
        "user_id": uuid.UUID(user_id),
        "storage_path": f"{user_id}/{file_id}/test.pdf",
        "content_hash": "hash-abc123",
    }.get(key))
    
    # Mock other files check (no other files share hash)
    mock_other_files = Mock()
    mock_other_files.__getitem__ = Mock(return_value=0)
    
    mock_conn.fetchrow = AsyncMock(side_effect=[mock_file_row, mock_other_files])
    mock_conn.execute = AsyncMock()
    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire = Mock(return_value=mock_acquire)
    mock_postgres.pool = mock_pool
    mock_postgres_service.return_value = mock_postgres
    
    # Mock MinIO
    mock_minio = Mock()
    mock_minio.delete_file = AsyncMock()
    mock_minio_service.return_value = mock_minio
    
    # Make request
    response = client.delete(
        f"/files/{file_id}",
        headers={"X-User-Id": user_id},
    )
    
    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "deleted" in data["message"].lower()
    assert data["vectorsShared"] is False
    
    # Verify deletion was called
    mock_minio.delete_file.assert_called_once()
    mock_conn.execute.assert_called_once()


@pytest.mark.asyncio
@patch("api.routes.files.load_config")
@patch("api.routes.files.PostgresService")
@patch("api.routes.files.MinIOService")
async def test_delete_file_shared_vectors(
    mock_minio_service,
    mock_postgres_service,
    mock_load_config,
    client,
):
    """Test file deletion when vectors are shared with other files."""
    # Setup mocks
    mock_config = Mock()
    mock_load_config.return_value = mock_config
    
    file_id = str(uuid.uuid4())
    user_id = "123e4567-e89b-12d3-a456-426614174000"
    
    # Mock PostgreSQL
    mock_postgres = Mock()
    mock_postgres.connect = AsyncMock()
    mock_postgres.disconnect = AsyncMock()
    mock_pool = Mock()
    mock_conn = AsyncMock()
    
    # Mock file record
    mock_file_row = Mock()
    mock_file_row.__getitem__ = Mock(side_effect=lambda key: {
        "user_id": uuid.UUID(user_id),
        "storage_path": f"{user_id}/{file_id}/test.pdf",
        "content_hash": "hash-shared",
    }.get(key))
    
    # Mock other files check (other files share hash)
    mock_other_files = Mock()
    mock_other_files.__getitem__ = Mock(return_value=2)  # 2 other files
    
    mock_conn.fetchrow = AsyncMock(side_effect=[mock_file_row, mock_other_files])
    mock_conn.execute = AsyncMock()
    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire = Mock(return_value=mock_acquire)
    mock_postgres.pool = mock_pool
    mock_postgres_service.return_value = mock_postgres
    
    # Mock MinIO
    mock_minio = Mock()
    mock_minio.delete_file = AsyncMock()
    mock_minio_service.return_value = mock_minio
    
    # Make request
    response = client.delete(
        f"/files/{file_id}",
        headers={"X-User-Id": user_id},
    )
    
    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["vectorsShared"] is True

