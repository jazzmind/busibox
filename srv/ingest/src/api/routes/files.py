"""
File metadata, download, deletion, chunk browsing, reprocessing, and export endpoints.

Handles:
- GET /files/{fileId}: Retrieve file metadata
- GET /files/{fileId}/download: Download original file from MinIO
- GET /files/{fileId}/chunks: Retrieve text chunks for a file
- POST /files/{fileId}/search: Search within a single document
- DELETE /files/{fileId}: Delete file and all associated data
- POST /files/{fileId}/reprocess: Reprocess document (delete chunks/vectors, re-run ingestion)
- GET /files/{fileId}/export: Export document in various formats (markdown, html, text, docx, pdf)
"""

import io
import json
import uuid
from typing import Optional, List

import redis.asyncio as redis_async
import structlog
from fastapi import APIRouter, Request, Query, status
from fastapi.responses import JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, Field

from api.services.minio_service import MinIOService
from api.services.postgres import PostgresService
from services.milvus_service import MilvusService
from services.processing_history_service import ProcessingHistoryService
from shared.config import Config

logger = structlog.get_logger()

router = APIRouter()


@router.get("/{fileId}")
async def get_file_metadata(fileId: str, request: Request):
    """
    Get file metadata and current status.
    
    Returns:
        File metadata including status, processing metrics, extracted metadata
    """
    user_id = request.state.user_id
    
    config = Config().to_dict()
    postgres_service = PostgresService(config)
    await postgres_service.connect()
    
    try:
        async with postgres_service.pool.acquire() as conn:
            # Get file record
            file_row = await conn.fetchrow("""
                SELECT 
                    f.file_id,
                    f.user_id,
                    f.filename,
                    f.original_filename,
                    f.mime_type,
                    f.size_bytes,
                    f.storage_path,
                    f.content_hash,
                    f.document_type,
                    f.primary_language,
                    f.detected_languages,
                    f.classification_confidence,
                    f.chunk_count,
                    f.vector_count,
                    f.processing_duration_seconds,
                    f.extracted_title,
                    f.extracted_author,
                    f.extracted_date,
                    f.extracted_keywords,
                    f.metadata,
                    f.permissions,
                    f.created_at,
                    f.updated_at
                FROM ingestion_files f
                WHERE f.file_id = $1
            """, uuid.UUID(fileId))
            
            if not file_row:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "File not found"}
                )
            
            # Verify ownership
            if str(file_row["user_id"]) != user_id:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": "Unauthorized access"}
                )
            
            # Get status
            status_row = await conn.fetchrow("""
                SELECT 
                    stage, progress, chunks_processed, total_chunks,
                    pages_processed, total_pages, error_message,
                    started_at, completed_at, updated_at
                FROM ingestion_status
                WHERE file_id = $1
            """, uuid.UUID(fileId))
            
            # Get processing strategies attempted
            strategy_rows = await conn.fetch("""
                SELECT 
                    processing_strategy,
                    success,
                    text_length,
                    chunk_count,
                    embedding_count,
                    visual_embedding_count,
                    processing_time_seconds,
                    error_message,
                    metadata,
                    created_at
                FROM processing_strategy_results
                WHERE file_id = $1
                ORDER BY created_at ASC
            """, uuid.UUID(fileId))
            
            strategies = [
                {
                    "strategy": row["processing_strategy"],
                    "success": row["success"],
                    "textLength": row["text_length"],
                    "chunkCount": row["chunk_count"],
                    "embeddingCount": row["embedding_count"],
                    "visualEmbeddingCount": row["visual_embedding_count"],
                    "processingTimeSeconds": float(row["processing_time_seconds"]) if row["processing_time_seconds"] else None,
                    "errorMessage": row["error_message"],
                    "metadata": row["metadata"],
                    "attemptedAt": row["created_at"].isoformat() if row["created_at"] else None,
                }
                for row in strategy_rows
            ]
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "fileId": str(file_row["file_id"]),
                    "filename": file_row["filename"],
                    "originalFilename": file_row["original_filename"],
                    "mimeType": file_row["mime_type"],
                    "sizeBytes": file_row["size_bytes"],
                    "contentHash": file_row["content_hash"],
                    "documentType": file_row["document_type"],
                    "primaryLanguage": file_row["primary_language"],
                    "detectedLanguages": file_row["detected_languages"],
                    "classificationConfidence": float(file_row["classification_confidence"]) if file_row["classification_confidence"] else None,
                    "chunkCount": file_row["chunk_count"],
                    "vectorCount": file_row["vector_count"],
                    "processingDurationSeconds": file_row["processing_duration_seconds"],
                    "extractedTitle": file_row["extracted_title"],
                    "extractedAuthor": file_row["extracted_author"],
                    "extractedDate": file_row["extracted_date"].isoformat() if file_row["extracted_date"] else None,
                    "extractedKeywords": file_row["extracted_keywords"],
                    "metadata": file_row["metadata"],
                    "permissions": file_row["permissions"],
                    "processingStrategies": strategies,
                    "status": {
                        "stage": status_row["stage"] if status_row else None,
                        "progress": status_row["progress"] if status_row else None,
                        "chunksProcessed": status_row["chunks_processed"] if status_row else None,
                        "totalChunks": status_row["total_chunks"] if status_row else None,
                        "pagesProcessed": status_row["pages_processed"] if status_row else None,
                        "totalPages": status_row["total_pages"] if status_row else None,
                        "errorMessage": status_row["error_message"] if status_row else None,
                        "startedAt": status_row["started_at"].isoformat() if status_row and status_row["started_at"] else None,
                        "completedAt": status_row["completed_at"].isoformat() if status_row and status_row["completed_at"] else None,
                        "updatedAt": status_row["updated_at"].isoformat() if status_row and status_row["updated_at"] else None,
                    },
                    "createdAt": file_row["created_at"].isoformat(),
                    "updatedAt": file_row["updated_at"].isoformat(),
                }
            )
    
    except ValueError as e:
        # Handle UUID parsing errors (invalid file ID format)
        logger.warning(
            "Invalid file ID format",
            file_id=fileId,
            user_id=user_id,
            error=str(e),
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Invalid file ID format"}
        )
    
    except Exception as e:
        logger.error(
            "Failed to get file metadata",
            file_id=fileId,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to retrieve file metadata", "details": str(e)}
        )
    
    finally:
        await postgres_service.disconnect()


@router.get("/{fileId}/history")
async def get_processing_history(fileId: str, request: Request):
    """
    Get detailed processing history for a file.
    
    Returns:
        Processing history with steps, timing, and any errors
    """
    user_id = request.state.user_id
    
    try:
        # Validate UUID format
        try:
            uuid.UUID(fileId)
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "Invalid file ID format"}
            )
        
        config = Config().to_dict()
        history_service = ProcessingHistoryService(config)
        
        try:
            history = history_service.get_history(fileId)
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"history": history}
            )
        finally:
            history_service.close()
            
    except Exception as e:
        logger.error(
            "Failed to get processing history",
            file_id=fileId,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to retrieve processing history"}
        )


@router.get("/{fileId}/download")
async def download_file(fileId: str, request: Request):
    """
    Download original file from MinIO storage.
    
    Returns:
        StreamingResponse with file content
    """
    user_id = request.state.user_id
    
    config = Config().to_dict()
    postgres_service = PostgresService(config)
    minio_service = MinIOService(config)
    
    await postgres_service.connect()
    
    try:
        async with postgres_service.pool.acquire() as conn:
            # Get file record
            file_row = await conn.fetchrow("""
                SELECT user_id, storage_path, original_filename, mime_type
                FROM ingestion_files
                WHERE file_id = $1
            """, uuid.UUID(fileId))
            
            if not file_row:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "File not found"}
                )
            
            # Verify ownership
            if str(file_row["user_id"]) != user_id:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": "Unauthorized access"}
                )
            
            storage_path = file_row["storage_path"]
            original_filename = file_row["original_filename"]
            mime_type = file_row["mime_type"]
            
            # Get file from MinIO
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                
                # Get file object from MinIO
                file_data = await loop.run_in_executor(
                    None,
                    lambda: minio_service.client.get_object(
                        minio_service.bucket,
                        storage_path
                    )
                )
                
                # Read file content
                content = await loop.run_in_executor(None, file_data.read)
                
                logger.info(
                    "File downloaded",
                    file_id=fileId,
                    user_id=user_id,
                    filename=original_filename,
                    size_bytes=len(content),
                )
                
                # Return file as streaming response
                return StreamingResponse(
                    iter([content]),
                    media_type=mime_type,
                    headers={
                        'Content-Disposition': f'attachment; filename="{original_filename}"'
                    }
                )
                
            except Exception as e:
                logger.error(
                    "Failed to download file from MinIO",
                    file_id=fileId,
                    storage_path=storage_path,
                    error=str(e),
                    exc_info=True,
                )
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={"error": "Failed to download file from storage", "details": str(e)}
                )
    
    except Exception as e:
        logger.error(
            "Failed to process download request",
            file_id=fileId,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to process download request", "details": str(e)}
        )
    
    finally:
        await postgres_service.disconnect()


@router.get("/{fileId}/presigned-url")
async def get_presigned_url(fileId: str, request: Request, expiry: int = 3600):
    """
    Generate a presigned URL for direct file access from MinIO.
    
    Args:
        fileId: File identifier
        expiry: URL expiration time in seconds (default: 3600 = 1 hour)
    
    Returns:
        JSON with presigned URL
    """
    user_id = request.state.user_id
    
    config = Config().to_dict()
    postgres_service = PostgresService(config)
    minio_service = MinIOService(config)
    
    await postgres_service.connect()
    
    try:
        async with postgres_service.pool.acquire() as conn:
            # Get file record
            file_row = await conn.fetchrow("""
                SELECT user_id, storage_path, original_filename, mime_type
                FROM ingestion_files
                WHERE file_id = $1
            """, uuid.UUID(fileId))
            
            if not file_row:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "File not found"}
                )
            
            # Verify ownership
            if str(file_row["user_id"]) != user_id:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": "Unauthorized access"}
                )
            
            storage_path = file_row["storage_path"]
            mime_type = file_row["mime_type"]
            filename = file_row["original_filename"]
            
            # Generate presigned URL
            try:
                import asyncio
                from datetime import timedelta
                
                loop = asyncio.get_event_loop()
                
                # Generate presigned GET URL
                presigned_url = await loop.run_in_executor(
                    None,
                    lambda: minio_service.client.presigned_get_object(
                        minio_service.bucket,
                        storage_path,
                        expires=timedelta(seconds=expiry)
                    )
                )
                
                logger.info(
                    "Presigned URL generated successfully",
                    file_id=fileId,
                    user_id=user_id,
                    storage_path=storage_path,
                    filename=filename,
                    mime_type=mime_type,
                    expiry_seconds=expiry,
                    presigned_url=presigned_url,
                    url_length=len(presigned_url),
                )
                
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={
                        "url": presigned_url,
                        "expiresIn": expiry,
                    }
                )
                
            except Exception as e:
                logger.error(
                    "Failed to generate presigned URL",
                    file_id=fileId,
                    storage_path=storage_path,
                    error=str(e),
                    exc_info=True,
                )
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={"error": "Failed to generate presigned URL", "details": str(e)}
                )
    
    except Exception as e:
        logger.error(
            "Failed to process presigned URL request",
            file_id=fileId,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to process request", "details": str(e)}
        )
    
    finally:
        await postgres_service.disconnect()


@router.get("/{fileId}/chunks")
async def get_file_chunks(
    fileId: str,
    request: Request,
    limit: int = 100,
    offset: int = 0
):
    """
    Get text chunks for a file.
    
    Args:
        fileId: File identifier
        limit: Number of chunks to return (default: 100)
        offset: Offset for pagination (default: 0)
    
    Returns:
        List of text chunks with metadata
    """
    user_id = request.state.user_id
    
    config = Config().to_dict()
    postgres_service = PostgresService(config)
    await postgres_service.connect()
    
    try:
        async with postgres_service.pool.acquire() as conn:
            # Verify file exists and user owns it
            file_row = await conn.fetchrow("""
                SELECT user_id, chunk_count
                FROM ingestion_files
                WHERE file_id = $1
            """, uuid.UUID(fileId))
            
            if not file_row:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "File not found"}
                )
            
            if str(file_row["user_id"]) != user_id:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": "Unauthorized access"}
                )
            
            # Get chunks
            chunks = await conn.fetch("""
                SELECT 
                    chunk_index,
                    text,
                    page_number,
                    char_offset,
                    token_count,
                    section_heading,
                    metadata,
                    created_at
                FROM ingestion_chunks
                WHERE file_id = $1
                ORDER BY chunk_index
                LIMIT $2 OFFSET $3
            """, uuid.UUID(fileId), limit, offset)
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "fileId": fileId,
                    "total": file_row["chunk_count"],
                    "limit": limit,
                    "offset": offset,
                    "chunks": [
                        {
                            "chunkIndex": chunk["chunk_index"],
                            "text": chunk["text"],
                            "pageNumber": chunk["page_number"],
                            "charOffset": chunk["char_offset"],
                            "tokenCount": chunk["token_count"],
                            "sectionHeading": chunk["section_heading"],
                            "metadata": chunk["metadata"],
                            "createdAt": chunk["created_at"].isoformat(),
                        }
                        for chunk in chunks
                    ]
                }
            )
    
    except Exception as e:
        logger.error(
            "Failed to get file chunks",
            file_id=fileId,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to retrieve chunks", "details": str(e)}
        )
    
    finally:
        await postgres_service.disconnect()


class DocumentSearchRequest(BaseModel):
    """Request model for single-document search."""
    query: str = Field(..., description="Search query")
    limit: int = Field(default=10, ge=1, le=100, description="Max results to return")


@router.post("/{fileId}/search")
async def search_within_document(
    fileId: str,
    request: Request,
    search_request: DocumentSearchRequest
):
    """
    Search within a single document using semantic search.
    
    Args:
        fileId: File identifier
        search_request: Search parameters
    
    Returns:
        List of matching chunks with relevance scores
    """
    user_id = request.state.user_id
    
    config = Config().to_dict()
    postgres_service = PostgresService(config)
    milvus_service = MilvusService(config)
    
    await postgres_service.connect()
    
    try:
        async with postgres_service.pool.acquire() as conn:
            # Verify file exists and user owns it
            file_row = await conn.fetchrow("""
                SELECT user_id
                FROM ingestion_files
                WHERE file_id = $1
            """, uuid.UUID(fileId))
            
            if not file_row:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "File not found"}
                )
            
            if str(file_row["user_id"]) != user_id:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": "Unauthorized access"}
                )
            
            # Generate embedding for query
            from processors.embedder import Embedder
            embedder = Embedder(config)
            query_embedding = await embedder.embed_single(search_request.query)
            
            if not query_embedding:
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={"error": "Failed to generate query embedding"}
                )
            
            # Search Milvus with file_id filter
            # Note: hybrid_search doesn't support custom filter_expr yet
            # So we'll get all results for the user and filter client-side
            all_results = milvus_service.hybrid_search(
                query_embedding=query_embedding,
                user_id=user_id,
                top_k=search_request.limit * 10,  # Get more results to filter
            )
            
            # Filter results to only this document
            search_results = [
                r for r in all_results 
                if r.get("file_id") == fileId
            ][:search_request.limit]
            
            # Get chunk details for results
            if search_results:
                # Build results directly from search results (they already have all needed fields)
                chunks = await conn.fetch("""
                    SELECT 
                        f.filename
                    FROM ingestion_files f
                    WHERE f.file_id = $1::uuid
                """, uuid.UUID(fileId))
                
                filename = chunks[0]["filename"] if chunks else "unknown"
                
                # Combine results
                results = []
                for result in search_results:
                    results.append({
                        "fileId": result["file_id"],
                        "filename": filename,
                        "chunkIndex": result["chunk_index"],
                        "pageNumber": result.get("page_number"),
                        "text": result["text"],
                        "score": result["score"],
                    })
                
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={
                        "query": search_request.query,
                        "fileId": fileId,
                        "results": results,
                        "total": len(results),
                        "limit": search_request.limit,
                    }
                )
            else:
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={
                        "query": search_request.query,
                        "fileId": fileId,
                        "results": [],
                        "total": 0,
                        "limit": search_request.limit,
                    }
                )
    
    except Exception as e:
        logger.error(
            "Failed to search within document",
            file_id=fileId,
            user_id=user_id,
            query=search_request.query,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Search failed", "details": str(e)}
        )
    
    finally:
        await postgres_service.disconnect()


@router.delete("/{fileId}")
async def delete_file(fileId: str, request: Request):
    """
    Delete file and all associated data.
    
    Deletes:
        - File from MinIO storage
        - Vectors from Milvus
        - Metadata from PostgreSQL (cascades to chunks and status)
    """
    user_id = request.state.user_id
    
    config = Config().to_dict()
    postgres_service = PostgresService(config)
    minio_service = MinIOService(config)
    milvus_service = MilvusService(config)
    
    await postgres_service.connect()
    
    try:
        async with postgres_service.pool.acquire() as conn:
            # Get file record
            file_row = await conn.fetchrow("""
                SELECT user_id, storage_path
                FROM ingestion_files
                WHERE file_id = $1
            """, uuid.UUID(fileId))
            
            if not file_row:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "File not found"}
                )
            
            # Verify ownership
            if str(file_row["user_id"]) != user_id:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": "Unauthorized access"}
                )
            
            storage_path = file_row["storage_path"]
            
            # Delete vectors from Milvus
            try:
                milvus_service.delete_file_vectors(fileId)
                logger.info("Deleted Milvus vectors", file_id=fileId)
            except Exception as e:
                logger.warning(
                    "Failed to delete vectors from Milvus",
                    file_id=fileId,
                    error=str(e),
                )
            
            # Delete file from MinIO
            try:
                await minio_service.delete_file(storage_path)
                logger.info("Deleted MinIO file", file_id=fileId, storage_path=storage_path)
            except Exception as e:
                logger.warning(
                    "Failed to delete file from MinIO",
                    file_id=fileId,
                    storage_path=storage_path,
                    error=str(e),
                )
            
            # Delete from PostgreSQL (cascades to status and chunks)
            await conn.execute("""
                DELETE FROM ingestion_files WHERE file_id = $1
            """, uuid.UUID(fileId))
            
            logger.info(
                "File deleted successfully",
                file_id=fileId,
                user_id=user_id,
                storage_path=storage_path,
            )
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "message": "File deleted successfully",
                    "fileId": fileId,
                }
            )
    
    except Exception as e:
        logger.error(
            "Failed to delete file",
            file_id=fileId,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to delete file", "details": str(e)}
        )
    
    finally:
        await postgres_service.disconnect()


@router.post("/{fileId}/reprocess")
async def reprocess_file(fileId: str, request: Request):
    """
    Reprocess a document - delete existing chunks/vectors and re-run ingestion.
    
    This is useful when:
    - Chunking strategy has been updated
    - Embedding model has changed
    - Document processing failed partially
    - You want to regenerate embeddings
    
    Process:
    1. Verify file exists and user owns it
    2. Delete existing chunks from PostgreSQL
    3. Delete existing vectors from Milvus
    4. Reset ingestion status to 'queued'
    5. Add job back to Redis queue for reprocessing
    
    Returns:
        Success message with file_id
    """
    user_id = request.state.user_id
    
    config = Config().to_dict()
    postgres_service = PostgresService(config)
    milvus_service = MilvusService(config)
    
    await postgres_service.connect()
    
    try:
        async with postgres_service.pool.acquire() as conn:
            # Get file record
            file_row = await conn.fetchrow("""
                SELECT user_id, filename, storage_path, mime_type, original_filename
                FROM ingestion_files
                WHERE file_id = $1
            """, uuid.UUID(fileId))
            
            if not file_row:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "File not found"}
                )
            
            # Verify ownership
            if str(file_row["user_id"]) != user_id:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": "Unauthorized access"}
                )
            
            filename = file_row["filename"]
            storage_path = file_row["storage_path"]
            mime_type = file_row["mime_type"]
            original_filename = file_row["original_filename"]
            
            logger.info(
                "Starting document reprocessing",
                file_id=fileId,
                filename=filename,
                mime_type=mime_type,
                user_id=user_id,
            )
            
            # Delete existing chunks from PostgreSQL
            deleted_chunks = await conn.execute("""
                DELETE FROM ingestion_chunks WHERE file_id = $1
            """, uuid.UUID(fileId))
            
            logger.info(
                "Deleted existing chunks",
                file_id=fileId,
                chunks_deleted=deleted_chunks,
            )
            
            # Delete existing vectors from Milvus
            try:
                milvus_service.delete_file_vectors(fileId)
                logger.info("Deleted Milvus vectors", file_id=fileId)
            except Exception as e:
                logger.warning(
                    "Failed to delete vectors from Milvus (may not exist)",
                    file_id=fileId,
                    error=str(e),
                )
            
            # Reset ingestion status to 'queued'
            await conn.execute("""
                UPDATE ingestion_status
                SET 
                    stage = 'queued',
                    progress = 0,
                    chunks_processed = 0,
                    total_chunks = 0,
                    pages_processed = 0,
                    total_pages = 0,
                    error_message = NULL,
                    updated_at = NOW()
                WHERE file_id = $1
            """, uuid.UUID(fileId))
            
            logger.info("Reset ingestion status", file_id=fileId)
            
            # Add job back to Redis queue
            redis_client = redis_async.Redis(
                host=config.get("redis_host", "localhost"),
                port=config.get("redis_port", 6379),
                decode_responses=True,
            )
            
            try:
                job_data = {
                    "job_id": fileId,  # Worker expects job_id field
                    "file_id": fileId,
                    "user_id": user_id,
                    "storage_path": storage_path,
                    "original_filename": original_filename or filename,
                    "mime_type": mime_type,  # Required for text extraction
                    "reprocess": "true",  # Flag to indicate this is a reprocess
                }
                
                stream_name = config.get("redis_stream", "jobs:ingestion")
                await redis_client.xadd(stream_name, job_data)
                
                logger.info(
                    "Added reprocess job to queue",
                    file_id=fileId,
                    stream=stream_name,
                )
            finally:
                await redis_client.aclose()
            
            logger.info(
                "Document reprocessing initiated",
                file_id=fileId,
                filename=filename,
                user_id=user_id,
            )
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "message": "Document reprocessing initiated",
                    "fileId": fileId,
                    "filename": filename,
                    "status": "queued",
                }
            )
    
    except Exception as e:
        logger.error(
            "Failed to reprocess file",
            file_id=fileId,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to reprocess file", "details": str(e)}
        )
    
    finally:
        await postgres_service.disconnect()


@router.get("/{fileId}/export")
async def export_file(
    fileId: str,
    request: Request,
    format: str = Query("markdown", regex="^(markdown|html|text|docx|pdf)$")
):
    """
    Export document by reconstructing from markdown chunks.
    
    Supports formats:
    - markdown: Original markdown with preserved structure
    - html: HTML conversion from markdown
    - text: Plain text (markdown stripped)
    - docx: Microsoft Word document
    - pdf: PDF document
    
    Args:
        fileId: File UUID
        format: Export format (markdown, html, text, docx, pdf)
    
    Returns:
        File content in requested format
    """
    user_id = request.state.user_id
    
    config = Config().to_dict()
    postgres_service = PostgresService(config)
    
    await postgres_service.connect()
    
    try:
        async with postgres_service.pool.acquire() as conn:
            # Get file metadata and verify ownership
            file_row = await conn.fetchrow("""
                SELECT file_id, filename, original_filename, user_id
                FROM ingestion_files
                WHERE file_id = $1
            """, uuid.UUID(fileId))
            
            if not file_row:
                logger.warning("File not found for export", file_id=fileId, user_id=user_id)
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "File not found"}
                )
            
            if str(file_row["user_id"]) != user_id:
                logger.warning("Unauthorized export attempt", file_id=fileId, user_id=user_id, owner_id=str(file_row["user_id"]))
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": "Unauthorized"}
                )
            
            filename = file_row["original_filename"] or file_row["filename"]
            
            # Get all chunks ordered by chunk_index
            chunk_rows = await conn.fetch("""
                SELECT chunk_index, text, page_number
                FROM ingestion_chunks
                WHERE file_id = $1
                ORDER BY chunk_index ASC
            """, uuid.UUID(fileId))
            
            if not chunk_rows:
                logger.warning("No chunks found for export", file_id=fileId, user_id=user_id)
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "No content available for export"}
                )
            
            # Reconstruct document from chunks
            markdown_content = "\n\n".join([row["text"] for row in chunk_rows])
            
            logger.info(
                "Exporting document",
                file_id=fileId,
                filename=filename,
                format=format,
                chunk_count=len(chunk_rows),
                user_id=user_id,
            )
            
            # Convert to requested format
            if format == "markdown":
                content = markdown_content.encode("utf-8")
                media_type = "text/markdown"
                extension = "md"
            
            elif format == "html":
                # Convert markdown to HTML
                try:
                    import markdown
                    html_body = markdown.markdown(
                        markdown_content,
                        extensions=['extra', 'codehilite', 'tables', 'toc']
                    )
                    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{filename}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 20px; }}
        h1, h2, h3 {{ margin-top: 24px; margin-bottom: 16px; }}
        code {{ background-color: #f4f4f4; padding: 2px 6px; border-radius: 3px; }}
        pre {{ background-color: #f4f4f4; padding: 16px; border-radius: 6px; overflow-x: auto; }}
        blockquote {{ border-left: 4px solid #ddd; padding-left: 16px; color: #666; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f4f4f4; }}
    </style>
</head>
<body>
{html_body}
</body>
</html>"""
                    content = html_content.encode("utf-8")
                    media_type = "text/html"
                    extension = "html"
                except ImportError:
                    logger.error("markdown library not available for HTML export")
                    return JSONResponse(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        content={"error": "HTML export not available"}
                    )
            
            elif format == "text":
                # Strip markdown formatting
                import re
                text_content = markdown_content
                # Remove markdown headers
                text_content = re.sub(r'^#+\s+', '', text_content, flags=re.MULTILINE)
                # Remove bold/italic
                text_content = re.sub(r'\*\*(.+?)\*\*', r'\1', text_content)
                text_content = re.sub(r'\*(.+?)\*', r'\1', text_content)
                text_content = re.sub(r'__(.+?)__', r'\1', text_content)
                text_content = re.sub(r'_(.+?)_', r'\1', text_content)
                # Remove links but keep text
                text_content = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text_content)
                # Remove inline code
                text_content = re.sub(r'`(.+?)`', r'\1', text_content)
                
                content = text_content.encode("utf-8")
                media_type = "text/plain"
                extension = "txt"
            
            elif format == "docx":
                # Convert markdown to DOCX
                try:
                    from docx import Document
                    from docx.shared import Pt, Inches
                    import re
                    
                    doc = Document()
                    
                    # Parse markdown and add to document
                    lines = markdown_content.split('\n')
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        
                        # Headings
                        if line.startswith('# '):
                            doc.add_heading(line[2:], level=1)
                        elif line.startswith('## '):
                            doc.add_heading(line[3:], level=2)
                        elif line.startswith('### '):
                            doc.add_heading(line[4:], level=3)
                        # Lists
                        elif line.startswith('- ') or line.startswith('* '):
                            doc.add_paragraph(line[2:], style='List Bullet')
                        elif re.match(r'^\d+\.\s', line):
                            doc.add_paragraph(re.sub(r'^\d+\.\s', '', line), style='List Number')
                        # Regular paragraph
                        else:
                            doc.add_paragraph(line)
                    
                    # Save to bytes
                    docx_buffer = io.BytesIO()
                    doc.save(docx_buffer)
                    content = docx_buffer.getvalue()
                    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    extension = "docx"
                except ImportError:
                    logger.error("python-docx library not available for DOCX export")
                    return JSONResponse(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        content={"error": "DOCX export not available"}
                    )
            
            elif format == "pdf":
                # Convert markdown to PDF
                try:
                    from reportlab.lib.pagesizes import letter
                    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
                    from reportlab.lib.units import inch
                    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
                    from reportlab.lib.enums import TA_LEFT
                    import re
                    
                    pdf_buffer = io.BytesIO()
                    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
                    
                    styles = getSampleStyleSheet()
                    story = []
                    
                    # Parse markdown and add to PDF
                    lines = markdown_content.split('\n')
                    for line in lines:
                        line = line.strip()
                        if not line:
                            story.append(Spacer(1, 0.2*inch))
                            continue
                        
                        # Escape HTML entities
                        line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                        
                        # Headings
                        if line.startswith('# '):
                            story.append(Paragraph(line[2:], styles['Heading1']))
                        elif line.startswith('## '):
                            story.append(Paragraph(line[3:], styles['Heading2']))
                        elif line.startswith('### '):
                            story.append(Paragraph(line[4:], styles['Heading3']))
                        # Lists
                        elif line.startswith('- ') or line.startswith('* '):
                            story.append(Paragraph(f"• {line[2:]}", styles['BodyText']))
                        elif re.match(r'^\d+\.\s', line):
                            story.append(Paragraph(line, styles['BodyText']))
                        # Regular paragraph
                        else:
                            story.append(Paragraph(line, styles['BodyText']))
                    
                    doc.build(story)
                    content = pdf_buffer.getvalue()
                    media_type = "application/pdf"
                    extension = "pdf"
                except ImportError as e:
                    logger.error("reportlab library not available for PDF export", error=str(e))
                    return JSONResponse(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        content={"error": "PDF export not available"}
                    )
            
            # Generate filename
            base_filename = filename.rsplit('.', 1)[0] if '.' in filename else filename
            export_filename = f"{base_filename}.{extension}"
            
            logger.info(
                "Document exported successfully",
                file_id=fileId,
                filename=export_filename,
                format=format,
                size_bytes=len(content),
                user_id=user_id,
            )
            
            return Response(
                content=content,
                media_type=media_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{export_filename}"'
                }
            )
    
    except Exception as e:
        logger.error(
            "Failed to export file",
            file_id=fileId,
            format=format,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to export file", "details": str(e)}
        )
    
    finally:
        await postgres_service.disconnect()

