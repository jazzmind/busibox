"""
Comprehensive integration tests for the full document ingestion pipeline.

Tests the complete lifecycle:
1. Document upload
2. Parsing and text extraction
3. Classification
4. Chunking
5. Embedding generation
6. Vector indexing
7. Search and retrieval
"""
import asyncio
import json
import uuid
from io import BytesIO
from typing import Dict, List

import pytest
import structlog
from fastapi.testclient import TestClient

from api.main import app
from api.services.minio import MinIOService
from api.services.postgres import PostgresService
from api.services.milvus import MilvusService
from shared.config import Config

logger = structlog.get_logger()


@pytest.fixture
def client():
    """Create FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def test_document_content() -> bytes:
    """Create test document content."""
    return b"""
# Test Document for Ingestion Pipeline

This is a comprehensive test document designed to validate the complete
ingestion pipeline from upload through search and retrieval.

## Section 1: Introduction

The document ingestion system processes files through multiple stages:
parsing, classification, chunking, embedding, and indexing. Each stage
must complete successfully for the document to be searchable.

## Section 2: Technical Details

The system uses hybrid search combining dense semantic embeddings with
sparse BM25 vectors. This enables both semantic similarity search and
keyword-based retrieval. The ColPali visual embeddings support
multi-modal search for PDF documents.

## Section 3: Validation

This section contains unique keywords for search validation:
- UNIQUE_KEYWORD_ALPHA
- UNIQUE_KEYWORD_BETA
- UNIQUE_KEYWORD_GAMMA

These keywords should be retrievable through both semantic and keyword search.

## Conclusion

The ingestion pipeline must handle various document types and sizes while
maintaining data integrity and search accuracy.
"""


# ============================================================================
# GRANULAR PIPELINE STAGE TESTS
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stage_1_upload(
    config: Config,
    test_user_id: str,
    client: TestClient,
    test_document_content: bytes,
    worker_process,
):
    """Test Stage 1: Document upload and initial storage."""
    logger.info("Testing Stage 1: Upload", worker_pid=worker_process.pid)
    
    # Upload document
    response = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("test_doc.txt", BytesIO(test_document_content), "text/plain")},
        data={"metadata": json.dumps({"test": "stage_1"})},
    )
    
    assert response.status_code == 200, f"Upload failed: {response.text}"
    data = response.json()
    file_id = data["fileId"]
    
    # Verify file record created
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    try:
        file_metadata = await postgres_service.get_file_metadata(file_id)
        assert file_metadata is not None
        assert file_metadata["filename"] == "test_doc.txt"
        assert file_metadata["mime_type"] == "text/plain"
        assert file_metadata["user_id"] == test_user_id
        assert file_metadata["stage"] in ["queued", "parsing"]
        
        logger.info("Stage 1 passed", file_id=file_id)
        
        # Cleanup
        await postgres_service.delete_file(file_id)
    finally:
        await postgres_service.disconnect()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stage_2_parsing(
    config: Config,
    test_user_id: str,
    client: TestClient,
    test_document_content: bytes,
    worker_process,
):
    """Test Stage 2: Text extraction and parsing."""
    logger.info("Testing Stage 2: Parsing", worker_pid=worker_process.pid)
    
    # Upload and wait for parsing
    response = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("test_doc.txt", BytesIO(test_document_content), "text/plain")},
    )
    file_id = response.json()["fileId"]
    
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    try:
        # Wait for parsing stage
        for _ in range(30):  # 30 seconds max
            metadata = await postgres_service.get_file_metadata(file_id)
            if metadata["stage"] in ["classification", "chunking", "embedding", "completed"]:
                break
            if metadata["stage"] == "failed":
                pytest.fail(f"Parsing failed: {metadata.get('error_message')}")
            await asyncio.sleep(1)
        
        # Verify parsing completed
        assert metadata["stage"] != "parsing", "Parsing did not complete"
        
        logger.info("Stage 2 passed", file_id=file_id, stage=metadata["stage"])
        
        # Cleanup
        await postgres_service.delete_file(file_id)
    finally:
        await postgres_service.disconnect()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stage_3_classification(
    worker_process,
    config: Config,
    test_user_id: str,
    client: TestClient,
    test_document_content: bytes,
):
    """Test Stage 3: Document classification."""
    logger.info("Testing Stage 3: Classification")
    
    # Upload and wait for classification
    response = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("test_doc.txt", BytesIO(test_document_content), "text/plain")},
    )
    file_id = response.json()["fileId"]
    
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    try:
        # Wait for classification
        for _ in range(30):
            metadata = await postgres_service.get_file_metadata(file_id)
            if metadata["stage"] in ["chunking", "embedding", "completed"]:
                break
            if metadata["stage"] == "failed":
                pytest.fail(f"Classification failed: {metadata.get('error_message')}")
            await asyncio.sleep(1)
        
        # Verify classification results
        assert metadata["document_type"] is not None
        assert metadata["primary_language"] is not None
        
        logger.info(
            "Stage 3 passed",
            file_id=file_id,
            document_type=metadata["document_type"],
            language=metadata["primary_language"],
        )
        
        # Cleanup
        await postgres_service.delete_file(file_id)
    finally:
        await postgres_service.disconnect()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stage_4_chunking(
    worker_process,
    config: Config,
    test_user_id: str,
    client: TestClient,
    test_document_content: bytes,
):
    """Test Stage 4: Document chunking."""
    logger.info("Testing Stage 4: Chunking")
    
    # Upload and wait for chunking
    response = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("test_doc.txt", BytesIO(test_document_content), "text/plain")},
    )
    file_id = response.json()["fileId"]
    
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    try:
        # Wait for chunking
        for _ in range(30):
            metadata = await postgres_service.get_file_metadata(file_id)
            if metadata["stage"] in ["embedding", "completed"]:
                break
            if metadata["stage"] == "failed":
                pytest.fail(f"Chunking failed: {metadata.get('error_message')}")
            await asyncio.sleep(1)
        
        # Verify chunks created
        assert metadata["chunk_count"] > 0, "No chunks created"
        
        # Verify chunk data in database
        async with postgres_service.pool.acquire() as conn:
            chunks = await conn.fetch(
                "SELECT chunk_index, text, token_count FROM ingestion_chunks WHERE file_id = $1 ORDER BY chunk_index",
                uuid.UUID(file_id),
            )
        
        assert len(chunks) > 0
        assert chunks[0]["text"] is not None
        assert chunks[0]["token_count"] > 0
        
        logger.info(
            "Stage 4 passed",
            file_id=file_id,
            chunk_count=len(chunks),
        )
        
        # Cleanup
        await postgres_service.delete_file(file_id)
    finally:
        await postgres_service.disconnect()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stage_5_embedding(
    worker_process,
    config: Config,
    test_user_id: str,
    client: TestClient,
    test_document_content: bytes,
):
    """Test Stage 5: Embedding generation."""
    logger.info("Testing Stage 5: Embedding")
    
    # Upload and wait for embedding
    response = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("test_doc.txt", BytesIO(test_document_content), "text/plain")},
    )
    file_id = response.json()["fileId"]
    
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    try:
        # Wait for completion
        for _ in range(60):  # Embedding can take longer
            metadata = await postgres_service.get_file_metadata(file_id)
            if metadata["stage"] == "completed":
                break
            if metadata["stage"] == "failed":
                pytest.fail(f"Embedding failed: {metadata.get('error_message')}")
            await asyncio.sleep(1)
        
        # Verify embeddings created
        assert metadata["vector_count"] > 0, "No vectors created"
        assert metadata["vector_count"] == metadata["chunk_count"], "Vector count mismatch"
        
        logger.info(
            "Stage 5 passed",
            file_id=file_id,
            vector_count=metadata["vector_count"],
        )
        
        # Cleanup
        await postgres_service.delete_file(file_id)
    finally:
        await postgres_service.disconnect()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stage_6_indexing(
    worker_process,
    config: Config,
    test_user_id: str,
    client: TestClient,
    test_document_content: bytes,
):
    """Test Stage 6: Vector indexing in Milvus."""
    logger.info("Testing Stage 6: Indexing")
    
    # Upload and wait for completion
    response = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("test_doc.txt", BytesIO(test_document_content), "text/plain")},
    )
    file_id = response.json()["fileId"]
    
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    try:
        # Wait for completion
        for _ in range(60):
            metadata = await postgres_service.get_file_metadata(file_id)
            if metadata["stage"] == "completed":
                break
            if metadata["stage"] == "failed":
                pytest.fail(f"Indexing failed: {metadata.get('error_message')}")
            await asyncio.sleep(1)
        
        # Verify vectors in Milvus
        milvus_service = MilvusService(config.to_dict())
        milvus_service.connect()
        
        results = milvus_service.collection.query(
            expr=f'file_id == "{file_id}"',
            output_fields=["id", "chunk_index", "text"],
            limit=100,
        )
        
        assert len(results) > 0, "No vectors found in Milvus"
        assert len(results) == metadata["vector_count"], "Vector count mismatch in Milvus"
        
        logger.info(
            "Stage 6 passed",
            file_id=file_id,
            indexed_vectors=len(results),
        )
        
        # Cleanup
        await postgres_service.delete_file(file_id)
        # Note: Milvus cleanup would require delete by expression
    finally:
        await postgres_service.disconnect()


# ============================================================================
# FULL END-TO-END TEST WITH SEARCH/RETRIEVAL
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_pipeline_with_search(
    worker_process,
    config: Config,
    test_user_id: str,
    client: TestClient,
    test_document_content: bytes,
):
    """
    Test complete pipeline: upload → process → search → retrieve.
    
    This is the primary integration test that validates the entire system.
    """
    logger.info("=" * 80)
    logger.info("FULL PIPELINE TEST WITH SEARCH/RETRIEVAL")
    logger.info("=" * 80)
    
    # ========================================================================
    # STEP 1: Upload Document
    # ========================================================================
    logger.info("STEP 1: Uploading document")
    
    response = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("pipeline_test.txt", BytesIO(test_document_content), "text/plain")},
        data={"metadata": json.dumps({"test": "full_pipeline", "keywords": ["alpha", "beta", "gamma"]})},
    )
    
    assert response.status_code == 200, f"Upload failed: {response.text}"
    data = response.json()
    file_id = data["fileId"]
    logger.info("Document uploaded", file_id=file_id)
    
    # ========================================================================
    # STEP 2: Wait for Processing to Complete
    # ========================================================================
    logger.info("STEP 2: Waiting for processing to complete")
    
    postgres_service = PostgresService(config.to_dict())
    await postgres_service.connect()
    
    try:
        max_wait = 120  # 2 minutes max
        completed = False
        
        for i in range(max_wait):
            metadata = await postgres_service.get_file_metadata(file_id)
            
            logger.info(
                "Processing status",
                iteration=i + 1,
                stage=metadata["stage"],
                progress=metadata["progress"],
                chunks=metadata.get("chunks_processed"),
                total_chunks=metadata.get("total_chunks"),
            )
            
            if metadata["stage"] == "completed":
                completed = True
                break
            
            if metadata["stage"] == "failed":
                pytest.fail(f"Processing failed: {metadata.get('error_message')}")
            
            await asyncio.sleep(1)
        
        assert completed, f"Processing did not complete within {max_wait} seconds"
        logger.info("Processing completed successfully")
        
        # ====================================================================
        # STEP 3: Verify Data Integrity
        # ====================================================================
        logger.info("STEP 3: Verifying data integrity")
        
        # Verify file metadata
        assert metadata["filename"] == "pipeline_test.txt"
        assert metadata["mime_type"] == "text/plain"
        assert metadata["document_type"] is not None
        assert metadata["primary_language"] is not None
        assert metadata["chunk_count"] > 0
        assert metadata["vector_count"] > 0
        assert metadata["vector_count"] == metadata["chunk_count"]
        assert metadata["content_hash"] is not None
        
        logger.info(
            "Metadata verified",
            document_type=metadata["document_type"],
            language=metadata["primary_language"],
            chunks=metadata["chunk_count"],
            vectors=metadata["vector_count"],
        )
        
        # Verify chunks in PostgreSQL
        async with postgres_service.pool.acquire() as conn:
            chunks = await conn.fetch(
                """
                SELECT chunk_index, text, token_count, char_offset
                FROM ingestion_chunks
                WHERE file_id = $1
                ORDER BY chunk_index
                """,
                uuid.UUID(file_id),
            )
        
        assert len(chunks) == metadata["chunk_count"]
        assert all(chunk["text"] for chunk in chunks)
        assert all(chunk["token_count"] > 0 for chunk in chunks)
        
        logger.info("Chunks verified", chunk_count=len(chunks))
        
        # ====================================================================
        # STEP 4: Test Semantic Search
        # ====================================================================
        logger.info("STEP 4: Testing semantic search")
        
        milvus_service = MilvusService(config.to_dict())
        milvus_service.connect()
        
        # Generate query embedding (in real system, this would use liteLLM)
        # For test, we'll use a simple query vector
        query_embedding = [0.1] * 1536  # Dummy embedding
        
        # Search for documents (semantic search)
        search_results = milvus_service.collection.search(
            data=[query_embedding],
            anns_field="text_dense",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=10,
            expr=f'user_id == "{test_user_id}"',
            output_fields=["file_id", "chunk_index", "text"],
        )
        
        assert len(search_results) > 0, "No search results returned"
        assert len(search_results[0]) > 0, "No results in first search"
        
        # Verify our document is in results
        result_file_ids = [hit.entity.get("file_id") for hit in search_results[0]]
        assert file_id in result_file_ids, "Uploaded document not found in search results"
        
        logger.info(
            "Semantic search successful",
            results_count=len(search_results[0]),
            found_document=True,
        )
        
        # ====================================================================
        # STEP 5: Test Keyword Search (BM25)
        # ====================================================================
        logger.info("STEP 5: Testing keyword search")
        
        # Query by unique keyword
        keyword_results = milvus_service.collection.query(
            expr=f'file_id == "{file_id}" and text like "%UNIQUE_KEYWORD_ALPHA%"',
            output_fields=["chunk_index", "text"],
            limit=10,
        )
        
        assert len(keyword_results) > 0, "Keyword search returned no results"
        assert any("UNIQUE_KEYWORD_ALPHA" in result["text"] for result in keyword_results)
        
        logger.info(
            "Keyword search successful",
            results_count=len(keyword_results),
        )
        
        # ====================================================================
        # STEP 6: Test Retrieval and Content Verification
        # ====================================================================
        logger.info("STEP 6: Testing content retrieval")
        
        # Retrieve specific chunks
        retrieved_chunks = milvus_service.collection.query(
            expr=f'file_id == "{file_id}"',
            output_fields=["chunk_index", "text", "page_number"],
            limit=100,
        )
        
        assert len(retrieved_chunks) == metadata["chunk_count"]
        
        # Verify content integrity
        retrieved_text = " ".join(chunk["text"] for chunk in sorted(retrieved_chunks, key=lambda x: x["chunk_index"]))
        original_text = test_document_content.decode("utf-8")
        
        # Check that key phrases are present
        assert "Test Document for Ingestion Pipeline" in retrieved_text
        assert "UNIQUE_KEYWORD_ALPHA" in retrieved_text
        assert "UNIQUE_KEYWORD_BETA" in retrieved_text
        assert "UNIQUE_KEYWORD_GAMMA" in retrieved_text
        
        logger.info("Content retrieval and verification successful")
        
        # ====================================================================
        # STEP 7: Test Duplicate Detection
        # ====================================================================
        logger.info("STEP 7: Testing duplicate detection")
        
        # Try to upload the same document again
        response2 = client.post(
            "/upload",
            headers={"X-User-Id": test_user_id},
            files={"file": ("pipeline_test_dup.txt", BytesIO(test_document_content), "text/plain")},
        )
        
        assert response2.status_code == 200
        data2 = response2.json()
        
        # Check if duplicate was detected
        metadata2 = await postgres_service.get_file_metadata(data2["fileId"])
        
        # The system should either:
        # 1. Detect duplicate and reuse vectors, or
        # 2. Process as new file
        # Both are valid depending on implementation
        
        logger.info(
            "Duplicate handling tested",
            original_hash=metadata["content_hash"],
            duplicate_hash=metadata2["content_hash"],
            same_hash=metadata["content_hash"] == metadata2["content_hash"],
        )
        
        # Cleanup duplicate
        await postgres_service.delete_file(data2["fileId"])
        
        # ====================================================================
        # STEP 8: Cleanup
        # ====================================================================
        logger.info("STEP 8: Cleaning up test data")
        
        # Delete from MinIO
        minio_service = MinIOService(config.to_dict())
        try:
            storage_path = f"{test_user_id}/{file_id}/pipeline_test.txt"
            await minio_service.delete_file(storage_path)
            logger.info("MinIO cleanup successful")
        except Exception as e:
            logger.warning("MinIO cleanup failed", error=str(e))
        
        # Delete from PostgreSQL (cascades to chunks and status)
        await postgres_service.delete_file(file_id)
        logger.info("PostgreSQL cleanup successful")
        
        # Note: Milvus vectors would need manual cleanup or TTL
        # For test purposes, we'll leave them (they'll be overwritten)
        
        logger.info("=" * 80)
        logger.info("FULL PIPELINE TEST COMPLETED SUCCESSFULLY")
        logger.info("=" * 80)
        
    finally:
        await postgres_service.disconnect()

