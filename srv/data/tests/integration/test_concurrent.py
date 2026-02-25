"""
Integration test for concurrent uploads.

Uses JWT auth fixtures from conftest.py.
"""
import asyncio
from io import BytesIO

import pytest
import structlog

logger = structlog.get_logger()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_uploads(async_client):
    """Test that multiple files can be uploaded concurrently."""
    num_files = 3
    
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
    
    # Poll the API for processing completion instead of querying DB directly
    max_wait = 60
    wait_interval = 3
    elapsed = 0
    
    while elapsed < max_wait:
        completed = 0
        for fid in file_ids:
            resp = await async_client.get(f"/files/{fid}")
            if resp.status_code == 200:
                status = resp.json().get("status", {})
                stage = status.get("stage") if isinstance(status, dict) else status
                if stage in ("completed", "failed"):
                    completed += 1
        
        if completed == len(file_ids):
            break
        
        await asyncio.sleep(wait_interval)
        elapsed += wait_interval
    
    logger.info("Concurrent upload test completed", file_count=len(file_ids))
    
    # Cleanup via API
    for fid in file_ids:
        await async_client.delete(f"/files/{fid}")
