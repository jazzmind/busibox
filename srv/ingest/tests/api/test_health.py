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
@patch("asyncpg.connect")
@patch("api.routes.health.load_config")
@patch("api.routes.health.MinIOService")
@patch("api.routes.health.RedisService")
@patch("pymilvus.connections")
@patch("pymilvus.utility")
@patch("httpx.AsyncClient")
async def test_health_all_healthy(
    mock_httpx_client,
    mock_milvus_utility,
    mock_milvus_connections,
    mock_redis_service,
    mock_minio_service,
    mock_load_config,
    mock_asyncpg_connect,
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
    mock_asyncpg_connect.return_value = mock_conn
    
    # Mock MinIO
    mock_minio_instance = Mock()
    mock_minio_instance.check_health = AsyncMock()
    mock_minio_service.return_value = mock_minio_instance
    
    # Mock Redis
    mock_redis_instance = Mock()
    mock_redis_instance.check_health = AsyncMock()
    mock_redis_service.return_value = mock_redis_instance
    
    # Mock Milvus
    mock_milvus_connections.connect = Mock(return_value=None)
    mock_milvus_connections.disconnect = Mock(return_value=None)
    mock_milvus_utility.list_collections = Mock(return_value=["collection1", "collection2"])
    
    # Mock liteLLM
    mock_client_instance = AsyncMock()
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_client_instance.get = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)
    mock_httpx_client.return_value = mock_client_instance
    
    # Make request
    response = client.get("/health")
    
    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "checks" in data
    assert data["healthy"] == "5/5"


@pytest.mark.asyncio
@patch("asyncpg.connect")
@patch("api.routes.health.load_config")
@patch("api.routes.health.MinIOService")
@patch("api.routes.health.RedisService")
@patch("pymilvus.connections")
@patch("pymilvus.utility")
@patch("httpx.AsyncClient")
async def test_health_critical_service_down(
    mock_httpx_client,
    mock_milvus_utility,
    mock_milvus_connections,
    mock_redis_service,
    mock_minio_service,
    mock_load_config,
    mock_asyncpg_connect,
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
    mock_asyncpg_connect.side_effect = Exception("Connection refused")
    
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
    mock_client_instance = AsyncMock()
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_client_instance.get = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)
    mock_httpx_client.return_value = mock_client_instance
    
    # Make request
    response = client.get("/health")
    
    # Assertions
    assert response.status_code == 503  # Service Unavailable
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["checks"]["postgres"]["status"] == "unhealthy"


@pytest.mark.asyncio
@patch("asyncpg.connect")
@patch("api.routes.health.load_config")
@patch("api.routes.health.MinIOService")
@patch("api.routes.health.RedisService")
@patch("pymilvus.connections")
@patch("pymilvus.utility")
@patch("httpx.AsyncClient")
async def test_health_degraded(
    mock_httpx_client,
    mock_milvus_utility,
    mock_milvus_connections,
    mock_redis_service,
    mock_minio_service,
    mock_load_config,
    mock_asyncpg_connect,
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
    mock_asyncpg_connect.return_value = mock_conn
    
    # Mock MinIO (healthy)
    mock_minio_instance = Mock()
    mock_minio_instance.check_health = AsyncMock()
    mock_minio_service.return_value = mock_minio_instance
    
    # Mock Redis (healthy)
    mock_redis_instance = Mock()
    mock_redis_instance.check_health = AsyncMock()
    mock_redis_service.return_value = mock_redis_instance
    
    # Mock Milvus (healthy)
    mock_milvus_connections.connect = Mock()
    mock_milvus_connections.disconnect = Mock()
    mock_utility = Mock()
    mock_utility.list_collections = Mock(return_value=["collection1"])
    mock_milvus_connections.utility = mock_utility
    
    # Mock liteLLM failure
    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(side_effect=Exception("Connection timeout"))
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)
    mock_httpx_client.return_value = mock_client_instance
    
    # Make request
    response = client.get("/health")
    
    # Assertions
    assert response.status_code == 200  # Still OK (non-critical)
    data = response.json()
    assert data["status"] == "degraded"
    assert data["checks"]["litellm"]["status"] == "unhealthy"
    assert data["healthy"] == "4/5"

