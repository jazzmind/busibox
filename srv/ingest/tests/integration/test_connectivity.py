"""
Connectivity tests for external services.

Tests basic connectivity to PostgreSQL, Milvus, and liteLLM.
"""
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env from busibox root directory
busibox_root = Path(__file__).parent.parent.parent.parent.parent
env_file = busibox_root / ".env"
if env_file.exists():
    load_dotenv(env_file)
    print(f"Loaded environment from {env_file}")

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import os
import structlog

logger = structlog.get_logger()


@pytest.mark.integration
def test_postgres_connectivity():
    """Test PostgreSQL connectivity."""
    import asyncpg
    
    host = os.getenv("POSTGRES_HOST", "10.96.201.203")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    database = os.getenv("POSTGRES_DB", "agent_server")
    user = os.getenv("POSTGRES_USER", "busibox_test_user")
    password = os.getenv("POSTGRES_PASSWORD", "")
    
    logger.info("Testing PostgreSQL connectivity", host=host, port=port, database=database, user=user)
    
    async def test_connection():
        try:
            conn = await asyncpg.connect(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                timeout=5,
            )
            
            # Test query
            result = await conn.fetchval("SELECT version()")
            logger.info("PostgreSQL connected successfully", version=result[:50])
            
            # Check if ingestion tables exist
            tables = await conn.fetch("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name LIKE 'ingestion%'
                ORDER BY table_name
            """)
            
            table_names = [row["table_name"] for row in tables]
            logger.info("Ingestion tables found", tables=table_names)
            
            await conn.close()
            return True
        except Exception as e:
            logger.error("PostgreSQL connection failed", error=str(e))
            raise
    
    import asyncio
    result = asyncio.run(test_connection())
    assert result is True


@pytest.mark.integration
def test_milvus_connectivity():
    """Test Milvus connectivity."""
    from pymilvus import connections, utility
    
    host = os.getenv("MILVUS_HOST", "10.96.201.204")
    port = int(os.getenv("MILVUS_PORT", "19530"))
    
    logger.info("Testing Milvus connectivity", host=host, port=port)
    
    try:
        connections.connect(
            "default",
            host=host,
            port=port,
            timeout=5,
        )
        
        # Get server version
        version = utility.get_server_version()
        logger.info("Milvus connected successfully", version=version)
        
        # List collections
        collections = utility.list_collections()
        logger.info("Milvus collections", collections=collections)
        
        # Check if documents collection exists (try both names)
        collection_name = os.getenv("MILVUS_COLLECTION", "document_embeddings")
        if collection_name not in collections:
            # Try alternative name
            if "documents" in collections:
                collection_name = "documents"
            elif "document_embeddings" in collections:
                collection_name = "document_embeddings"
        
        if collection_name in collections:
            logger.info("Collection found", collection=collection_name)
            from pymilvus import Collection
            collection = Collection(collection_name)
            logger.info("Collection loaded", num_entities=collection.num_entities)
        else:
            logger.warning("Collection not found", requested=os.getenv("MILVUS_COLLECTION", "documents"), available=collections)
        
        connections.disconnect("default")
        assert True
    except Exception as e:
        logger.error("Milvus connection failed", error=str(e))
        raise


@pytest.mark.integration
def test_litellm_connectivity():
    """Test liteLLM connectivity."""
    import httpx
    
    base_url = os.getenv("LITELLM_BASE_URL", "http://10.96.201.207:4000")
    api_key = os.getenv("LITELLM_API_KEY", "")
    
    logger.info("Testing liteLLM connectivity", base_url=base_url, has_api_key=bool(api_key))
    
    async def test_connection():
        try:
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Test health endpoint with API key if available
                health_response = await client.get(f"{base_url}/health", headers=headers)
                health_response.raise_for_status()
                logger.info("liteLLM health check passed", status=health_response.status_code)
                
                # Test models endpoint (with API key if available)
                models_response = await client.get(f"{base_url}/v1/models", headers=headers)
                models_response.raise_for_status()
                models_data = models_response.json()
                logger.info("liteLLM models endpoint accessible", model_count=len(models_data.get("data", [])))
                
                # Test embedding endpoint if available
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                
                embedding_response = await client.post(
                    f"{base_url}/v1/embeddings",
                    headers=headers,
                    json={
                        "model": "text-embedding-3-small",
                        "input": "test"
                    },
                    timeout=10.0,
                )
                
                if embedding_response.status_code == 200:
                    embedding_data = embedding_response.json()
                    logger.info(
                        "liteLLM embedding test passed",
                        model=embedding_data.get("model"),
                        embedding_dim=len(embedding_data.get("data", [{}])[0].get("embedding", [])) if embedding_data.get("data") else 0,
                    )
                else:
                    logger.warning(
                        "liteLLM embedding test failed",
                        status=embedding_response.status_code,
                        response=embedding_response.text[:200],
                    )
                
                return True
        except Exception as e:
            logger.error("liteLLM connection failed", error=str(e))
            raise
    
    import asyncio
    result = asyncio.run(test_connection())
    assert result is True


@pytest.mark.integration
def test_redis_connectivity():
    """Test Redis connectivity."""
    import redis.asyncio as redis
    
    # Redis might be on ingest container or separate
    # Try common locations
    host = os.getenv("REDIS_HOST", "10.96.201.203")
    port = int(os.getenv("REDIS_PORT", "6379"))
    
    logger.info("Testing Redis connectivity", host=host, port=port)
    
    async def test_connection():
        try:
            r = redis.Redis(host=host, port=port, decode_responses=True, socket_connect_timeout=5)
            
            # Test ping
            pong = await r.ping()
            assert pong is True
            logger.info("Redis ping successful")
            
            # Test set/get
            await r.set("test_key", "test_value")
            value = await r.get("test_key")
            assert value == "test_value"
            await r.delete("test_key")
            logger.info("Redis set/get test passed")
            
            await r.close()
            return True
        except Exception as e:
            logger.error("Redis connection failed", error=str(e))
            raise
    
    import asyncio
    result = asyncio.run(test_connection())
    assert result is True


@pytest.mark.integration
def test_minio_connectivity():
    """Test MinIO connectivity."""
    from minio import Minio
    from minio.error import S3Error
    
    # MinIO is on files container
    endpoint = os.getenv("MINIO_ENDPOINT", "10.96.201.205:9000")
    access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
    
    # Parse endpoint
    if ":" in endpoint:
        host, port = endpoint.split(":")
        port = int(port)
    else:
        host = endpoint
        port = 9000
    
    logger.info("Testing MinIO connectivity", host=host, port=port, secure=secure)
    
    try:
        client = Minio(
            f"{host}:{port}",
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        
        # List buckets
        buckets = client.list_buckets()
        bucket_names = [bucket.name for bucket in buckets]
        logger.info("MinIO connected successfully", buckets=bucket_names)
        
        # Check if documents bucket exists
        bucket_name = os.getenv("MINIO_BUCKET", "documents")
        if bucket_name in bucket_names:
            logger.info("Documents bucket found", bucket=bucket_name)
        else:
            logger.warning("Documents bucket not found", bucket=bucket_name, available=bucket_names)
        
        assert True
    except Exception as e:
        logger.error("MinIO connection failed", error=str(e))
        raise

