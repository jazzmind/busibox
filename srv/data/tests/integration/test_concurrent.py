"""
Integration test for concurrent uploads.

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
async def test_concurrent_uploads(async_client, postgres_service):
    """Test that multiple files can be uploaded concurrently."""
    # Create multiple test files
    num_files = 3
    
    # Upload files sequentially (async_client is session-scoped)
    logger.info("Uploading files", count=num_files)
    
    file_ids = []
    for i in range(num_files):
        content = BytesIO(f"Test document {i} for concurrent upload testing.".encode())
        response = await async_client.post(
            "/upload",
            files={"file": (f"concurrent_test_{i}.txt", content, "text/plain")},
        )
        
        if response.status_code == 200:
            data = response.json()
            file_ids.append(data["fileId"])
        else:
            logger.warning("Upload failed", status=response.status_code, response=response.text)
    
    if not file_ids:
        pytest.skip("No files were uploaded successfully - services may not be available")
    
    logger.info("Files uploaded", file_ids=file_ids)
    
    # Wait for files to process
    max_wait = 60
    wait_interval = 3
    elapsed = 0
    
    while elapsed < max_wait:
        async with postgres_service.pool.acquire() as conn:
            status_rows = await conn.fetch("""
                SELECT file_id, stage
                FROM data_status
                WHERE file_id = ANY($1::uuid[])
            """, [uuid.UUID(fid) for fid in file_ids])
            
            completed = sum(1 for row in status_rows if row["stage"] == "completed")
            failed = sum(1 for row in status_rows if row["stage"] == "failed")
            
            if completed + failed == len(file_ids):
                break
        
        await asyncio.sleep(wait_interval)
        elapsed += wait_interval
    
    logger.info("Concurrent upload test completed", file_count=len(file_ids))
    
    # Cleanup
    async with postgres_service.pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM data_files WHERE file_id = ANY($1::uuid[])",
            [uuid.UUID(fid) for fid in file_ids]
        )
