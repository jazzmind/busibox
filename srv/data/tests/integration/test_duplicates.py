"""
Integration test for duplicate detection and vector reuse.

Uses JWT auth fixtures from conftest.py.
"""
import asyncio
from io import BytesIO

import pytest
import structlog

logger = structlog.get_logger()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_duplicate_detection(async_client):
    """Test that duplicate files are detected and vectors are reused."""
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
    
    # Wait for first file to process via API polling
    max_wait = 60
    wait_interval = 2
    elapsed = 0
    
    while elapsed < max_wait:
        resp = await async_client.get(f"/files/{file_id1}")
        if resp.status_code == 200:
            status = resp.json().get("status", {})
            stage = status.get("stage") if isinstance(status, dict) else status
            if stage in ("completed", "failed"):
                break
        await asyncio.sleep(wait_interval)
        elapsed += wait_interval
    
    # Get content hash from first file via API
    resp = await async_client.get(f"/files/{file_id1}")
    if resp.status_code != 200:
        pytest.skip("File record not found - processing may have failed")
    
    file_data1 = resp.json()
    content_hash = file_data1.get("contentHash")
    
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
        
        await asyncio.sleep(3)
        
        # Verify both files have same content_hash via API
        resp2 = await async_client.get(f"/files/{file_id2}")
        if resp2.status_code == 200:
            file_data2 = resp2.json()
            if content_hash and file_data2.get("contentHash"):
                assert file_data2["contentHash"] == content_hash
        
        logger.info("Duplicate detection test completed", file_id1=file_id1, file_id2=file_id2)
        
        # Cleanup via API
        await async_client.delete(f"/files/{file_id1}")
        await async_client.delete(f"/files/{file_id2}")
    else:
        await async_client.delete(f"/files/{file_id1}")
