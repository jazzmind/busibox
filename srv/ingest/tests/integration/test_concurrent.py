"""
Integration test for concurrent uploads.
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
async def test_concurrent_uploads(config: Config, test_user_id: str, client: TestClient):
    """Test that multiple files can be uploaded concurrently."""
    # Create multiple test files
    num_files = 5
    file_contents = [
        BytesIO(f"Test document {i} for concurrent upload testing.".encode())
        for i in range(num_files)
    ]
    
    for i, content in enumerate(file_contents):
        content.name = f"concurrent_test_{i}.txt"
    
    # Upload all files concurrently
    logger.info("Uploading files concurrently", count=num_files)
    
    responses = []
    for i, content in enumerate(file_contents):
        response = client.post(
            "/upload",
            headers={"X-User-Id": test_user_id},
            files={"file": (f"concurrent_test_{i}.txt", content, "text/plain")},
        )
        responses.append(response)
    
    # Verify all uploads succeeded
    file_ids = []
    for response in responses:
        assert response.status_code == 200
        data = response.json()
        file_ids.append(data["fileId"])
    
    logger.info("All files uploaded", file_ids=file_ids)
    
    # Wait for all files to process (with timeout)
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    max_wait = 120  # 2 minutes for concurrent processing
    wait_interval = 3
    elapsed = 0
    
    while elapsed < max_wait:
        async with postgres_service.pool.acquire() as conn:
            status_rows = await conn.fetch("""
                SELECT file_id, stage
                FROM ingestion_status
                WHERE file_id = ANY($1::uuid[])
            """, [uuid.UUID(fid) for fid in file_ids])
            
            completed = sum(1 for row in status_rows if row["stage"] == "completed")
            failed = sum(1 for row in status_rows if row["stage"] == "failed")
            
            logger.info(
                "Processing status",
                completed=completed,
                failed=failed,
                total=len(file_ids),
            )
            
            if completed + failed == len(file_ids):
                break
        
        await asyncio.sleep(wait_interval)
        elapsed += wait_interval
    
    # Verify all files were processed
    async with postgres_service.pool.acquire() as conn:
        file_rows = await conn.fetch("""
            SELECT file_id, chunk_count, vector_count
            FROM ingestion_files
            WHERE file_id = ANY($1::uuid[])
        """, [uuid.UUID(fid) for fid in file_ids])
        
        assert len(file_rows) == num_files
        
        # Verify all have chunks and vectors
        for row in file_rows:
            assert row["chunk_count"] > 0
            assert row["vector_count"] > 0
    
    logger.info("Concurrent upload test completed", file_count=num_files)
    
    # Cleanup
    async with postgres_service.pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM ingestion_files WHERE file_id = ANY($1::uuid[])",
            [uuid.UUID(fid) for fid in file_ids]
        )
    
    await postgres_service.disconnect()

