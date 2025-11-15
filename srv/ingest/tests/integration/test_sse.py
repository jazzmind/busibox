"""
Integration test for SSE status streaming.
"""
import asyncio
import json
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
async def test_sse_status_streaming(config: Config, test_user_id: str, client: TestClient):
    """Test that SSE status updates are streamed correctly."""
    # Upload a file
    test_content = b"Test document for SSE streaming test."
    file_content = BytesIO(test_content)
    file_content.name = "sse_test.txt"
    
    response = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("sse_test.txt", file_content, "text/plain")},
    )
    
    assert response.status_code == 200
    upload_data = response.json()
    file_id = upload_data["fileId"]
    
    # Get status stream
    logger.info("Starting SSE stream", file_id=file_id)
    response = client.get(
        f"/status/{file_id}",
        headers={"X-User-Id": test_user_id},
    )
    
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    
    # Read SSE events
    events = []
    lines = response.text.split("\n")
    
    for i, line in enumerate(lines):
        if line.startswith("data: "):
            data_str = line[6:]  # Remove "data: " prefix
            try:
                data = json.loads(data_str)
                events.append(data)
                logger.info("SSE event received", event=data)
            except json.JSONDecodeError:
                pass
    
    # Verify we got status updates
    assert len(events) > 0
    
    # Check that we see progression through stages
    stages_seen = [e.get("stage") for e in events if "stage" in e]
    logger.info("Stages seen", stages=stages_seen)
    
    # Should see at least "queued" stage
    assert "queued" in stages_seen or any(e.get("stage") == "queued" for e in events)
    
    # Cleanup
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE file_id = $1", uuid.UUID(file_id))
    
    await postgres_service.disconnect()

