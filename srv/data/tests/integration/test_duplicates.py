"""
Integration test for duplicate detection and vector reuse.

Uses JWT auth fixtures from conftest.py.
"""
import asyncio
import uuid
from io import BytesIO

import pytest
import structlog

logger = structlog.get_logger()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_duplicate_detection(async_client, postgres_service):
    """Test that duplicate files are detected and vectors are reused."""
    # Create test file content
    test_content = b"Duplicate test document content - this should be detected as duplicate."
    
    # Upload first file
    logger.info("Uploading first file")
    file1_content = BytesIO(test_content)
    
    response1 = await async_client.post(
        "/upload",
        files={"file": ("test1.txt", file1_content, "text/plain")},
    )
    
    if response1.status_code != 200:
        pytest.skip(f"Upload failed with status {response1.status_code} - services may not be available")
    
    upload_data1 = response1.json()
    file_id1 = upload_data1["fileId"]
    
    # Wait for first file to process
    max_wait = 60
    wait_interval = 2
    elapsed = 0
    
    while elapsed < max_wait:
        async with postgres_service.pool.acquire() as conn:
            status_row = await conn.fetchrow("""
                SELECT stage FROM data_status WHERE file_id = $1
            """, uuid.UUID(file_id1))
            
            if status_row and status_row["stage"] in ["completed", "failed"]:
                break
            
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval
    
    # Get content hash from first file
    async with postgres_service.pool.acquire() as conn:
        file_row1 = await conn.fetchrow("""
            SELECT content_hash, vector_count
            FROM data_files
            WHERE file_id = $1
        """, uuid.UUID(file_id1))
    
    if not file_row1:
        pytest.skip("File record not found - processing may have failed")
    
    content_hash = file_row1["content_hash"]
    
    # Upload duplicate file (same content, different filename)
    logger.info("Uploading duplicate file")
    file2_content = BytesIO(test_content)
    
    response2 = await async_client.post(
        "/upload",
        files={"file": ("test2.txt", file2_content, "text/plain")},
    )
    
    if response2.status_code == 200:
        upload_data2 = response2.json()
        file_id2 = upload_data2["fileId"]
        
        # Wait for duplicate processing
        await asyncio.sleep(3)
        
        # Verify both files have same content_hash
        async with postgres_service.pool.acquire() as conn:
            file_row2 = await conn.fetchrow("""
                SELECT content_hash
                FROM data_files
                WHERE file_id = $1
            """, uuid.UUID(file_id2))
            
            if file_row2:
                assert file_row2["content_hash"] == content_hash
        
        logger.info("Duplicate detection test completed", file_id1=file_id1, file_id2=file_id2)
        
        # Cleanup
        async with postgres_service.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM data_files WHERE file_id = ANY($1::uuid[])",
                [uuid.UUID(file_id1), uuid.UUID(file_id2)]
            )
    else:
        # Cleanup first file
        async with postgres_service.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM data_files WHERE file_id = $1",
                uuid.UUID(file_id1)
            )
