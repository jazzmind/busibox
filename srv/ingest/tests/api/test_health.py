"""
Unit tests for health check endpoint.
"""
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.mark.asyncio
@patch("api.routes.health.asyncpg")
@patch("api.routes.health.load_config")
@patch("api.routes.health.MinIOService")
@patch("api.routes.health.RedisService")
@patch("api.routes.health.connections")
@patch("api.routes.health.httpx")
async def test_health_all_healthy(
    mock_httpx,
    mock_milvus_connections,
    mock_redis_service,
    mock_minio_service,
    mock_load_config,
    mock_asyncpg,
    client,
):
    """Test health check when all services are healthy."""
    # Setup mocks
    mock_config = Mock()
    mock_config.get = Mock(side_effect=lambda key, default=None: {
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "test",
        "postgres_user": "test",
        "postgres_password": "test",
        "milvus_host": "localhost",
        "milvus_port": 19530,
        "litellm_base_url": "http://localhost:4000",
    }.get(key, default))
    mock_load_config.return_value = mock_config
    
    # Mock PostgreSQL
    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()
    mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
    
    # Mock MinIO
    mock_minio = Mock()
    mock_minio.check_health = AsyncMock()
    mock_minio_service.return_value = mock_minio
    
    # Mock Redis
    mock_redis = Mock()
    mock_redis.check_health = AsyncMock()
    mock_redis_service.return_value = mock_redis
    
    # Mock Milvus
    mock_milvus_connections.connect = Mock()
    mock_milvus_connections.disconnect = Mock()
    mock_utility = Mock()
    mock_utility.list_collections = Mock(return_value=["collection1", "collection2"])
    mock_milvus_connections.utility = mock_utility
    
    # Mock liteLLM
    mock_client = AsyncMock()
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_httpx.AsyncClient = Mock(return_value=mock_client)
    
    # Make request
    response = client.get("/health")
    
    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "checks" in data
    assert data["healthy"] == "5/5"


@pytest.mark.asyncio
@patch("api.routes.health.asyncpg")
@patch("api.routes.health.load_config")
@patch("api.routes.health.MinIOService")
@patch("api.routes.health.RedisService")
@patch("api.routes.health.connections")
@patch("api.routes.health.httpx")
async def test_health_critical_service_down(
    mock_httpx,
    mock_milvus_connections,
    mock_redis_service,
    mock_minio_service,
    mock_load_config,
    mock_asyncpg,
    client,
):
    """Test health check when critical service (PostgreSQL) is down."""
    # Setup mocks
    mock_config = Mock()
    mock_config.get = Mock(side_effect=lambda key, default=None: {
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "test",
        "postgres_user": "test",
        "postgres_password": "test",
        "milvus_host": "localhost",
        "milvus_port": 19530,
        "litellm_base_url": "http://localhost:4000",
    }.get(key, default))
    mock_load_config.return_value = mock_config
    
    # Mock PostgreSQL failure
    mock_asyncpg.connect = AsyncMock(side_effect=Exception("Connection refused"))
    
    # Mock MinIO (healthy)
    mock_minio = Mock()
    mock_minio.check_health = AsyncMock()
    mock_minio_service.return_value = mock_minio
    
    # Mock Redis (healthy)
    mock_redis = Mock()
    mock_redis.check_health = AsyncMock()
    mock_redis_service.return_value = mock_redis
    
    # Mock Milvus (healthy)
    mock_milvus_connections.connect = Mock()
    mock_milvus_connections.disconnect = Mock()
    mock_utility = Mock()
    mock_utility.list_collections = Mock(return_value=["collection1"])
    mock_milvus_connections.utility = mock_utility
    
    # Mock liteLLM (healthy)
    mock_client = AsyncMock()
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_httpx.AsyncClient = Mock(return_value=mock_client)
    
    # Make request
    response = client.get("/health")
    
    # Assertions
    assert response.status_code == 503  # Service Unavailable
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["checks"]["postgres"]["status"] == "unhealthy"


@pytest.mark.asyncio
@patch("api.routes.health.asyncpg")
@patch("api.routes.health.load_config")
@patch("api.routes.health.MinIOService")
@patch("api.routes.health.RedisService")
@patch("api.routes.health.connections")
@patch("api.routes.health.httpx")
async def test_health_degraded(
    mock_httpx,
    mock_milvus_connections,
    mock_redis_service,
    mock_minio_service,
    mock_load_config,
    mock_asyncpg,
    client,
):
    """Test health check when non-critical service (liteLLM) is down."""
    # Setup mocks
    mock_config = Mock()
    mock_config.get = Mock(side_effect=lambda key, default=None: {
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "test",
        "postgres_user": "test",
        "postgres_password": "test",
        "milvus_host": "localhost",
        "milvus_port": 19530,
        "litellm_base_url": "http://localhost:4000",
    }.get(key, default))
    mock_load_config.return_value = mock_config
    
    # Mock PostgreSQL (healthy)
    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()
    mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
    
    # Mock MinIO (healthy)
    mock_minio = Mock()
    mock_minio.check_health = AsyncMock()
    mock_minio_service.return_value = mock_minio
    
    # Mock Redis (healthy)
    mock_redis = Mock()
    mock_redis.check_health = AsyncMock()
    mock_redis_service.return_value = mock_redis
    
    # Mock Milvus (healthy)
    mock_milvus_connections.connect = Mock()
    mock_milvus_connections.disconnect = Mock()
    mock_utility = Mock()
    mock_utility.list_collections = Mock(return_value=["collection1"])
    mock_milvus_connections.utility = mock_utility
    
    # Mock liteLLM failure
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("Connection timeout"))
    mock_httpx.AsyncClient = Mock(return_value=mock_client)
    
    # Make request
    response = client.get("/health")
    
    # Assertions
    assert response.status_code == 200  # Still OK (non-critical)
    data = response.json()
    assert data["status"] == "degraded"
    assert data["checks"]["litellm"]["status"] == "unhealthy"
    assert data["healthy"] == "4/5"

