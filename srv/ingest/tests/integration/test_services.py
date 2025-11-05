"""
Granular service-level integration tests.

Tests each service independently with real backends.
"""
import asyncio
import uuid
from io import BytesIO

import pytest
import structlog

from api.services.minio import MinIOService
from api.services.postgres import PostgresService
from api.services.redis import RedisService
from services.milvus_service import MilvusService
from shared.config import Config

logger = structlog.get_logger()


@pytest.fixture
def test_file_id():
    """Generate a test file ID."""
    return str(uuid.uuid4())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_minio_service(config: Config, test_user_id: str, test_file_id: str):
    """Test MinIO service operations."""
    minio_service = MinIOService(config.to_dict())
    
    # Test health check
    logger.info("Testing MinIO health check")
    is_healthy = await minio_service.check_health()
    assert is_healthy is True, "MinIO health check failed"
    
    # Test file upload
    logger.info("Testing MinIO file upload", file_id=test_file_id)
    test_content = b"Test content for MinIO upload test"
    file_obj = BytesIO(test_content)
    storage_path = f"{test_user_id}/{test_file_id}/test.txt"
    
    content_hash = await minio_service.upload_file_stream(
        file_obj,
        storage_path,
    )
    
    assert content_hash is not None, "Upload should return content hash"
    logger.info("MinIO upload successful", content_hash=content_hash)
    
    # Test file deletion
    logger.info("Testing MinIO file deletion")
    await minio_service.delete_file(storage_path)
    logger.info("MinIO deletion successful")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_postgres_service(config: Config, test_user_id: str, test_file_id: str):
    """Test PostgreSQL service operations."""
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    try:
        # Test health check (implicit in connect)
        logger.info("PostgreSQL connected successfully")
        
        # Test file record creation
        logger.info("Testing file record creation", file_id=test_file_id)
        import json
        await postgres_service.create_file_record(
            file_id=test_file_id,
            user_id=test_user_id,
            filename="test.txt",
            original_filename="test.txt",
            mime_type="text/plain",
            size_bytes=100,
            storage_path=f"{test_user_id}/{test_file_id}/test.txt",
            content_hash="test-hash-123",
            metadata=json.dumps({}),
        )
        logger.info("File record created successfully")
        
        # TODO: Add more tests when additional methods are implemented
        logger.info("PostgreSQL service test passed")
        
    finally:
        await postgres_service.disconnect()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_redis_service(config: Config, test_file_id: str):
    """Test Redis service operations."""
    redis_service = RedisService(config.to_dict())
    await redis_service.connect()
    
    try:
        # Test health check
        logger.info("Testing Redis health check")
        is_healthy = await redis_service.check_health()
        assert is_healthy is True, "Redis health check failed"
        
        # Test consumer group creation
        logger.info("Testing consumer group creation")
        await redis_service.ensure_consumer_group()
        logger.info("Consumer group ready")
        
        # Test job addition
        logger.info("Testing job addition", file_id=test_file_id)
        job_id = await redis_service.add_job({
            "file_id": test_file_id,
            "user_id": "test-user-123",
            "storage_path": f"test-user-123/{test_file_id}/test.txt",
        })
        assert job_id is not None, "Job addition should return job ID"
        logger.info("Job added successfully", job_id=job_id)
        
        # Note: Not reading/acknowledging the job to avoid interfering with worker
        # In a real scenario, the worker would pick this up
        
    finally:
        await redis_service.disconnect()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_milvus_service(config: Config, test_file_id: str, test_user_id: str):
    """Test Milvus service operations."""
    milvus_service = MilvusService(config.to_dict())
    
    try:
        # Test connection (implicit in init)
        logger.info("Testing Milvus connection")
        
        # Test text chunk insertion
        logger.info("Testing text chunk insertion", file_id=test_file_id)
        # Prepare test data
        chunks = [
            {
                "chunk_index": 0,
                "text": "This is a test chunk for Milvus integration test.",
                "char_offset": 0,
                "token_count": 10,
            }
        ]
        embeddings = [[0.1] * 1536]  # text-embedding-3-small dimension
        content_hash = "test-hash-123"
        
        # Insert chunks
        count = milvus_service.insert_text_chunks(
            file_id=test_file_id,
            user_id=test_user_id,
            chunks=chunks,
            embeddings=embeddings,
            content_hash=content_hash,
        )
        logger.info("Text chunks inserted successfully", count=count)
        
        # Test query (verify insertion)
        logger.info("Testing Milvus query")
        from pymilvus import Collection
        collection = Collection(config.milvus_collection)
        collection.load()
        
        results = collection.query(
            expr=f'file_id == "{test_file_id}"',
            output_fields=["chunk_index", "text"],
            limit=10,
        )
        
        assert len(results) > 0, "Should find the chunk we just inserted"
        logger.info("Query successful", result_count=len(results))
        
        # Cleanup
        logger.info("Cleaning up test data from Milvus")
        collection.delete(expr=f'file_id == "{test_file_id}"')
        logger.info("Milvus test data cleaned up")
        
    except Exception as e:
        logger.error("Milvus test failed", error=str(e))
        raise


@pytest.mark.asyncio
@pytest.mark.integration
async def test_service_integration(config: Config, test_user_id: str):
    """Test services working together in a mini-pipeline."""
    test_file_id = str(uuid.uuid4())
    logger.info("Testing service integration", file_id=test_file_id)
    
    # Step 1: Upload to MinIO
    logger.info("Step 1: Upload to MinIO")
    minio_service = MinIOService(config.to_dict())
    test_content = b"Integration test content"
    file_obj = BytesIO(test_content)
    storage_path = f"{test_user_id}/{test_file_id}/integration_test.txt"
    content_hash = await minio_service.upload_file_stream(file_obj, storage_path)
    logger.info("MinIO upload complete", content_hash=content_hash)
    
    # Step 2: Create PostgreSQL record
    logger.info("Step 2: Create PostgreSQL record")
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    try:
        await postgres_service.create_file_record(
            file_id=test_file_id,
            user_id=test_user_id,
            filename="integration_test.txt",
            original_filename="integration_test.txt",
            mime_type="text/plain",
            size_bytes=len(test_content),
            storage_path=storage_path,
            content_hash=content_hash,
            metadata={},
        )
        logger.info("PostgreSQL record created")
        
        # Step 3: Queue job in Redis
        logger.info("Step 3: Queue job in Redis")
        redis_service = RedisService(config.to_dict())
        await redis_service.connect()
        try:
            await redis_service.ensure_consumer_group()
            job_id = await redis_service.add_job({
                "file_id": test_file_id,
                "user_id": test_user_id,
                "storage_path": storage_path,
            })
            logger.info("Redis job queued", job_id=job_id)
        finally:
            await redis_service.disconnect()
        
        # Step 4: Verify PostgreSQL status
        logger.info("Step 4: Verify data in PostgreSQL")
        async with postgres_service.pool.acquire() as conn:
            file_row = await conn.fetchrow(
                "SELECT file_id, filename, content_hash FROM ingestion_files WHERE file_id = $1",
                uuid.UUID(test_file_id)
            )
            assert file_row is not None
            assert file_row["filename"] == "integration_test.txt"
            assert file_row["content_hash"] == content_hash
        logger.info("PostgreSQL verification complete")
        
        # Cleanup
        logger.info("Cleaning up integration test data")
        await minio_service.delete_file(storage_path)
        async with postgres_service.pool.acquire() as conn:
            await conn.execute("DELETE FROM ingestion_files WHERE file_id = $1", uuid.UUID(test_file_id))
        logger.info("Integration test cleanup complete")
        
    finally:
        await postgres_service.disconnect()
    
    logger.info("Service integration test completed successfully")

