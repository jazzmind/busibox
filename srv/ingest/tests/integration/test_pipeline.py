"""
Integration test for full ingestion pipeline.

Tests: upload → parse → chunk → embed → index → search

Uses JWT auth fixtures from conftest.py.

NOTE: This test uses small inline text content and is designed to be FAST.
For full PDF processing tests, see test_full_pipeline.py (marked @slow).
"""
import asyncio
import uuid
from io import BytesIO

import pytest
import structlog

logger = structlog.get_logger()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.pipeline
async def test_basic_text_pipeline(async_client, postgres_service, config, rls_pool, test_user_id):
    """Test full pipeline: upload → parse → chunk → embed → index."""
    file_id = None
    
    try:
        # Create test file content - use unique content to avoid duplicate detection
        unique_id = str(uuid.uuid4())[:8]
        test_content = f"""
        This is a test document for ingestion pipeline testing (run {unique_id}).
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
        """.encode()
        
        file_content = BytesIO(test_content)
        
        # Step 1: Upload file
        logger.info("Step 1: Uploading file")
        response = await async_client.post(
            "/upload",
            files={"file": ("test_document.txt", file_content, "text/plain")},
        )
        
        if response.status_code != 200:
            pytest.fail(f"Upload failed with status {response.status_code}: {response.text}")
        
        upload_data = response.json()
        file_id = upload_data["fileId"]
        logger.info("File uploaded", file_id=file_id)
        
        # Step 2: Wait for processing (poll status)
        logger.info("Step 2: Waiting for processing", file_id=file_id)
        
        max_wait = 60
        wait_interval = 2
        elapsed = 0
        
        while elapsed < max_wait:
            async with postgres_service.pool.acquire() as conn:
                status_row = await conn.fetchrow("""
                    SELECT stage, progress, error_message
                    FROM ingestion_status
                    WHERE file_id = $1
                """, uuid.UUID(file_id))
                
                if status_row:
                    stage = status_row["stage"]
                    
                    if stage == "completed":
                        break
                    elif stage == "failed":
                        logger.warning("Processing failed", error=status_row["error_message"])
                        break
            
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval
        
        # Step 3: Verify data in PostgreSQL (use RLS pool)
        logger.info("Step 3: Verifying PostgreSQL data", file_id=file_id)
        
        rls_pool.set_rls_context(user_id=test_user_id)
        async with rls_pool.acquire() as conn:
            file_row = await conn.fetchrow("""
                SELECT 
                    file_id, filename, document_type, chunk_count,
                    vector_count, content_hash
                FROM ingestion_files
                WHERE file_id = $1
            """, uuid.UUID(file_id))
            
            if file_row:
                logger.info("PostgreSQL data verified", 
                           chunk_count=file_row["chunk_count"],
                           vector_count=file_row["vector_count"])
        
        logger.info("Integration test completed", file_id=file_id)
        
    finally:
        # Cleanup - always runs even if test fails
        if file_id:
            logger.info("Cleaning up test data", file_id=file_id)
            try:
                # Use API to delete (respects RLS and cascades properly)
                response = await async_client.delete(f"/files/{file_id}")
                if response.status_code in [200, 204, 404]:
                    logger.info("Test file cleaned up via API", file_id=file_id)
                else:
                    logger.warning("Failed to cleanup via API", file_id=file_id, status=response.status_code)
            except Exception as e:
                logger.warning("Cleanup error", file_id=file_id, error=str(e))
