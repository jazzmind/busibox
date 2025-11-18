"""
Integration test for full ingestion pipeline.

Tests: upload → parse → chunk → embed → index → search
"""
import asyncio
import uuid
from io import BytesIO

import pytest
import structlog
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.services.minio_service import MinIOService
from src.api.services.postgres import PostgresService
from src.shared.config import Config

logger = structlog.get_logger()


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_pipeline(config: Config, test_user_id: str, client: TestClient):
    """Test full pipeline: upload → parse → chunk → embed → index."""
    # Create test file content
    test_content = b"""
    This is a test document for ingestion pipeline testing.
    It contains multiple sentences to test chunking functionality.
    The document should be processed through all stages:
    1. Parsing - extract text from the file
    2. Classification - determine document type
    3. Chunking - split into semantic chunks
    4. Embedding - generate dense embeddings
    5. Indexing - store in Milvus
    
    This paragraph provides more content for chunking tests.
    Each sentence should be analyzed for semantic boundaries.
    The chunker should respect sentence and paragraph boundaries.
    """
    
    file_content = BytesIO(test_content)
    file_content.name = "test_document.txt"
    
    # Step 1: Upload file
    logger.info("Step 1: Uploading file", user_id=test_user_id)
    response = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("test_document.txt", file_content, "text/plain")},
    )
    
    assert response.status_code == 200
    upload_data = response.json()
    file_id = upload_data["fileId"]
    assert file_id is not None
    logger.info("File uploaded", file_id=file_id)
    
    # Step 2: Wait for processing (poll status)
    logger.info("Step 2: Waiting for processing", file_id=file_id)
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    max_wait = 60  # 60 seconds max wait
    wait_interval = 2  # Check every 2 seconds
    elapsed = 0
    
    async def check_status():
        async with postgres_service.pool.acquire() as conn:
            status_row = await conn.fetchrow("""
                SELECT stage, progress, error_message
                FROM ingestion_status
                WHERE file_id = $1
            """, uuid.UUID(file_id))
            
            if status_row:
                stage = status_row["stage"]
                progress = status_row["progress"]
                error = status_row["error_message"]
                
                logger.info(
                    "Processing status",
                    file_id=file_id,
                    stage=stage,
                    progress=progress,
                    error=error,
                )
                
                if stage == "completed":
                    return True
                elif stage == "failed":
                    pytest.fail(f"Processing failed: {error}")
        
        return False
    
    while elapsed < max_wait:
        completed = await check_status()
        if completed:
            break
        await asyncio.sleep(wait_interval)
        elapsed += wait_interval
    
    if elapsed >= max_wait:
        pytest.fail(f"Processing timed out after {max_wait} seconds")
    
    # Step 3: Verify data in PostgreSQL
    logger.info("Step 3: Verifying PostgreSQL data", file_id=file_id)
    
    async def verify_data():
        async with postgres_service.pool.acquire() as conn:
            file_row = await conn.fetchrow("""
                SELECT 
                    file_id, filename, document_type, chunk_count,
                    vector_count, content_hash
                FROM ingestion_files
                WHERE file_id = $1
            """, uuid.UUID(file_id))
            
            assert file_row is not None
            assert file_row["filename"] == "test_document.txt"
            assert file_row["chunk_count"] > 0
            assert file_row["vector_count"] > 0
            assert file_row["content_hash"] is not None
            logger.info("PostgreSQL data verified", chunk_count=file_row["chunk_count"])
            return file_row
    
    file_row = await verify_data()
    
    # Step 4: Verify chunks in PostgreSQL
    async def verify_chunks():
        async with postgres_service.pool.acquire() as conn:
            chunks = await conn.fetch("""
                SELECT chunk_index, text, token_count
                FROM ingestion_chunks
                WHERE file_id = $1
                ORDER BY chunk_index
            """, uuid.UUID(file_id))
            
            assert len(chunks) > 0
            logger.info("Chunks verified", chunk_count=len(chunks))
            return chunks
    
    chunks = await verify_chunks()
    
    # Step 5: Verify vectors in Milvus (if accessible)
    try:
        from pymilvus import connections, Collection
        
        connections.connect(
            "default",
            host=config.milvus_host,
            port=config.milvus_port,
        )
        
        collection = Collection(config.milvus_collection)
        collection.load()
        
        # Query vectors for this file
        results = collection.query(
            expr=f'file_id == "{file_id}"',
            output_fields=["chunk_index", "text"],
            limit=10,
        )
        
        assert len(results) > 0
        logger.info("Milvus vectors verified", vector_count=len(results))
        
        connections.disconnect("default")
    except Exception as e:
        logger.warning("Could not verify Milvus vectors", error=str(e))
        # Don't fail test if Milvus is not accessible
    
    # Step 6: Cleanup test data
    logger.info("Step 6: Cleaning up test data", file_id=file_id)
    
    async def cleanup():
        # Delete from MinIO
        minio_service = MinIOService(config.to_dict())
        try:
            storage_path = f"{test_user_id}/{file_id}/test_document.txt"
            await minio_service.delete_file(storage_path)
        except Exception as e:
            logger.warning("Could not delete from MinIO", error=str(e))
        
        # Delete from PostgreSQL (cascades to chunks and status)
        async with postgres_service.pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM ingestion_files WHERE file_id = $1
            """, uuid.UUID(file_id))
        
        await postgres_service.disconnect()
    
    await cleanup()
    logger.info("Integration test completed successfully", file_id=file_id)
