"""
Pytest configuration and shared fixtures.
"""
import pytest
from unittest.mock import Mock, AsyncMock
from fastapi.testclient import TestClient


@pytest.fixture
def mock_postgres_service():
    """Mock PostgreSQL service."""
    service = Mock()
    service.create_file_record = Mock(return_value="file-test-123")
    service.update_status = Mock()
    service.get_file_metadata = Mock(return_value={
        "file_id": "file-test-123",
        "filename": "test.pdf",
        "status": "completed",
    })
    service.delete_file = Mock()
    return service


@pytest.fixture
def mock_minio_service():
    """Mock MinIO service."""
    service = Mock()
    service.upload_file = Mock(return_value="s3://bucket/path")
    service.delete_file = Mock()
    service.file_exists = Mock(return_value=True)
    return service


@pytest.fixture
def mock_redis_service():
    """Mock Redis service."""
    service = Mock()
    service.add_job = Mock()
    service.get_job = Mock(return_value=None)
    return service


@pytest.fixture
def mock_milvus_service():
    """Mock Milvus service."""
    service = Mock()
    service.insert_text_chunks = Mock()
    service.insert_page_images = Mock()
    service.check_duplicate = Mock(return_value=None)
    return service


@pytest.fixture
def mock_config():
    """Mock configuration."""
    config = Mock()
    config.postgres_host = "localhost"
    config.postgres_port = 5432
    config.postgres_db = "test_db"
    config.postgres_user = "test_user"
    config.postgres_password = "test_pass"
    config.minio_endpoint = "localhost:9000"
    config.minio_access_key = "minioadmin"
    config.minio_secret_key = "minioadmin"
    config.redis_host = "localhost"
    config.redis_port = 6379
    config.milvus_host = "localhost"
    config.milvus_port = 19530
    config.litellm_base_url = "http://localhost:4000"
    config.litellm_api_key = "test-key"
    return config

