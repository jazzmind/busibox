"""
Granular service-level integration tests.

Tests each service independently with real backends.
Uses fixtures from conftest.py.
"""
import asyncio
import uuid
from io import BytesIO

import pytest
import structlog

logger = structlog.get_logger()


@pytest.fixture
def test_file_id():
    """Generate a test file ID."""
    return str(uuid.uuid4())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_minio_service(minio_service, test_file_id):
    """Test MinIO service operations."""
    test_user_id = str(uuid.uuid4())
    
    # Test health check
    logger.info("Testing MinIO health check")
    try:
        is_healthy = await minio_service.check_health()
        assert is_healthy is True, "MinIO health check failed"
    except Exception as e:
        pytest.skip(f"MinIO not available: {e}")
    
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
async def test_postgres_service(test_file_id, test_user_id, rls_pool):
    """Test PostgreSQL service operations with RLS context using shared test_utils."""
    import json
    
    # Use the shared RLSEnabledPool which handles connection lifecycle properly
    # Set RLS context for the test user
    rls_pool.set_rls_context(user_id=test_user_id)
    
    try:
        # Test health check (implicit in acquire)
        logger.info("Testing PostgreSQL connection with RLS context")
        
        async with rls_pool.acquire() as conn:
            # RLS context is automatically set by RLSEnabledPool
            # Test file record creation
            logger.info("Testing file record creation", file_id=test_file_id, user_id=test_user_id)
            await conn.execute("""
                INSERT INTO ingestion_files 
                (file_id, user_id, owner_id, filename, original_filename, mime_type, size_bytes, 
                 storage_path, content_hash, metadata, visibility, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW())
            """, 
                uuid.UUID(test_file_id), uuid.UUID(test_user_id), uuid.UUID(test_user_id),
                "test.txt", "test.txt", "text/plain", 100,
                f"{test_user_id}/{test_file_id}/test.txt", "test-hash-123",
                json.dumps({}), "personal"
            )
            logger.info("File record created successfully")
            
            # Test reading back the record
            logger.info("Testing file record retrieval")
            row = await conn.fetchrow(
                "SELECT * FROM ingestion_files WHERE file_id = $1",
                uuid.UUID(test_file_id)
            )
            assert row is not None, "Should be able to read own record with RLS"
            assert str(row["owner_id"]) == test_user_id
            logger.info("File record retrieved successfully")
            
            # Cleanup
            logger.info("Cleaning up test data")
            await conn.execute(
                "DELETE FROM ingestion_files WHERE file_id = $1",
                uuid.UUID(test_file_id)
            )
            logger.info("Test data cleaned up")
        
    except Exception as e:
        logger.error("PostgreSQL test failed", error=str(e))
        # Cleanup on error
        try:
            async with rls_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM ingestion_files WHERE file_id = $1",
                    uuid.UUID(test_file_id)
                )
        except:
            pass
        raise


@pytest.mark.asyncio
@pytest.mark.integration
async def test_service_integration(minio_service, rls_pool):
    """Test services working together in a mini-pipeline using RLS-enabled pool."""
    import json
    
    test_file_id = str(uuid.uuid4())
    test_user_id = str(uuid.uuid4())
    
    # Set RLS context for this test user
    rls_pool.set_rls_context(user_id=test_user_id)
    
    logger.info("Testing service integration", file_id=test_file_id, user_id=test_user_id)
    
    try:
        # Step 1: Upload to MinIO
        logger.info("Step 1: Upload to MinIO")
        test_content = b"Integration test content"
        file_obj = BytesIO(test_content)
        storage_path = f"{test_user_id}/{test_file_id}/integration_test.txt"
        content_hash = await minio_service.upload_file_stream(file_obj, storage_path)
        logger.info("MinIO upload complete", content_hash=content_hash)
        
        # Step 2: Create PostgreSQL record with RLS context
        logger.info("Step 2: Create PostgreSQL record")
        async with rls_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO ingestion_files 
                (file_id, user_id, owner_id, filename, original_filename, mime_type, size_bytes, 
                 storage_path, content_hash, metadata, visibility, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW())
            """, 
                uuid.UUID(test_file_id), uuid.UUID(test_user_id), uuid.UUID(test_user_id),
                "integration_test.txt", "integration_test.txt", "text/plain", len(test_content),
                storage_path, content_hash, json.dumps({}), "personal"
            )
        logger.info("PostgreSQL record created")
        
        # Step 3: Verify PostgreSQL data
        logger.info("Step 3: Verify data in PostgreSQL")
        async with rls_pool.acquire() as conn:
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
        async with rls_pool.acquire() as conn:
            await conn.execute("DELETE FROM ingestion_files WHERE file_id = $1", uuid.UUID(test_file_id))
        logger.info("Integration test cleanup complete")
        
    except Exception as e:
        logger.error("Service integration test failed", error=str(e))
        # Cleanup on error
        try:
            await minio_service.delete_file(storage_path)
        except:
            pass
        try:
            async with rls_pool.acquire() as conn:
                await conn.execute("DELETE FROM ingestion_files WHERE file_id = $1", uuid.UUID(test_file_id))
        except:
            pass
        raise
    
    logger.info("Service integration test completed successfully")
