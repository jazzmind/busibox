"""
Integration test for duplicate detection and vector reuse.
"""
import asyncio
import uuid
from io import BytesIO

import pytest
import structlog
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.services.postgres import PostgresService
from src.shared.config import Config

logger = structlog.get_logger()


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_duplicate_detection(config: Config, test_user_id: str, client: TestClient):
    """Test that duplicate files are detected and vectors are reused."""
    # Create test file content
    test_content = b"Duplicate test document content - this should be detected as duplicate."
    
    # Upload first file
    logger.info("Uploading first file", user_id=test_user_id)
    file1_content = BytesIO(test_content)
    file1_content.name = "test1.txt"
    
    response1 = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("test1.txt", file1_content, "text/plain")},
    )
    
    assert response1.status_code == 200
    upload_data1 = response1.json()
    file_id1 = upload_data1["fileId"]
    assert upload_data1["duplicate"] is False
    
    # Wait for first file to process
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    max_wait = 60
    wait_interval = 2
    elapsed = 0
    
    while elapsed < max_wait:
        async with postgres_service.pool.acquire() as conn:
            status_row = await conn.fetchrow("""
                SELECT stage FROM ingestion_status WHERE file_id = $1
            """, uuid.UUID(file_id1))
            
            if status_row and status_row["stage"] == "completed":
                break
            
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval
    
    if elapsed >= max_wait:
        pytest.fail("First file processing timed out")
    
    # Get content hash from first file
    async with postgres_service.pool.acquire() as conn:
        file_row1 = await conn.fetchrow("""
            SELECT content_hash, vector_count
            FROM ingestion_files
            WHERE file_id = $1
        """, uuid.UUID(file_id1))
        
        content_hash = file_row1["content_hash"]
        vector_count1 = file_row1["vector_count"]
    
    # Upload duplicate file (same content, different filename)
    logger.info("Uploading duplicate file", user_id=test_user_id)
    file2_content = BytesIO(test_content)
    file2_content.name = "test2.txt"
    
    response2 = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("test2.txt", file2_content, "text/plain")},
    )
    
    assert response2.status_code == 200
    upload_data2 = response2.json()
    file_id2 = upload_data2["fileId"]
    
    # Check if duplicate was detected
    if upload_data2.get("duplicate"):
        logger.info("Duplicate detected immediately", file_id2=file_id2)
        assert upload_data2["status"] == "completed"
    else:
        # Wait a bit for duplicate detection
        await asyncio.sleep(2)
        
        # Check status - should be completed quickly
        async with postgres_service.pool.acquire() as conn:
            status_row = await conn.fetchrow("""
                SELECT stage FROM ingestion_status WHERE file_id = $1
            """, uuid.UUID(file_id2))
            
            if status_row:
                assert status_row["stage"] == "completed"
    
    # Verify both files have same content_hash
    async with postgres_service.pool.acquire() as conn:
        file_row2 = await conn.fetchrow("""
            SELECT content_hash, vector_count
            FROM ingestion_files
            WHERE file_id = $1
        """, uuid.UUID(file_id2))
        
        assert file_row2["content_hash"] == content_hash
        # Vector count should be same (vectors reused)
        assert file_row2["vector_count"] == vector_count1
    
    logger.info("Duplicate detection verified", file_id1=file_id1, file_id2=file_id2)
    
    # Cleanup
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE file_id = ANY($1::uuid[])", 
                          [uuid.UUID(file_id1), uuid.UUID(file_id2)])
    
    await postgres_service.disconnect()

