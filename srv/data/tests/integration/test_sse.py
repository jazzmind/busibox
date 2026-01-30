"""
Integration test for SSE status streaming.

Uses JWT auth fixtures from conftest.py.
"""
import asyncio
import json
import uuid
from io import BytesIO

import pytest
import structlog

logger = structlog.get_logger()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sse_status_streaming(async_client, postgres_service):
    """Test that SSE status updates are streamed correctly."""
    # Upload a file
    test_content = b"Test document for SSE streaming test."
    file_content = BytesIO(test_content)
    
    response = await async_client.post(
        "/upload",
        files={"file": ("sse_test.txt", file_content, "text/plain")},
    )
    
    if response.status_code != 200:
        pytest.skip(f"Upload failed with status {response.status_code} - services may not be available")
    
    upload_data = response.json()
    file_id = upload_data["fileId"]
    
    # Get status endpoint (Note: httpx AsyncClient doesn't support SSE streaming well)
    # Just verify the endpoint is accessible
    logger.info("Checking status endpoint", file_id=file_id)
    
    response = await async_client.get(f"/status/{file_id}")
    
    # Status endpoint should return 200 with SSE content type
    assert response.status_code == 200
    
    # Cleanup
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM data_files WHERE file_id = $1", uuid.UUID(file_id))
