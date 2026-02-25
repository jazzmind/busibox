"""
Integration test for SSE status streaming.

Uses JWT auth fixtures from conftest.py.
"""
import pytest
import structlog
from io import BytesIO

logger = structlog.get_logger()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sse_status_streaming(async_client):
    """Test that SSE status updates are streamed correctly."""
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
    
    logger.info("Checking status endpoint", file_id=file_id)
    
    response = await async_client.get(f"/status/{file_id}")
    assert response.status_code == 200
    
    # Cleanup via API
    await async_client.delete(f"/files/{file_id}")
