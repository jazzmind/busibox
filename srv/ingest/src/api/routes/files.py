"""
File metadata, download, deletion, and chunk browsing endpoints.

Handles:
- GET /files/{fileId}: Retrieve file metadata
- GET /files/{fileId}/download: Download original file from MinIO
- GET /files/{fileId}/chunks: Retrieve text chunks for a file
- POST /files/{fileId}/search: Search within a single document
- DELETE /files/{fileId}: Delete file and all associated data
"""

import uuid
from typing import Optional, List

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from api.services.minio import MinIOService
from api.services.postgres import PostgresService
from services.milvus_service import MilvusService
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
                    chunk_type,
                    char_count,
                    word_count,
                    token_count,
                    embedding_dense_id,
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
                            "chunkType": chunk["chunk_type"],
                            "charCount": chunk["char_count"],
                            "wordCount": chunk["word_count"],
                            "tokenCount": chunk["token_count"],
                            "embeddingDenseId": chunk["embedding_dense_id"],
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
            search_results = milvus_service.hybrid_search(
                dense_vector=query_embedding,
                user_id=user_id,
                limit=search_request.limit,
                filter_expr=f'file_id == "{fileId}"'  # Filter to this document only
            )
            
            # Get chunk details for results
            if search_results:
                chunk_ids = [result["chunk_id"] for result in search_results]
                chunks = await conn.fetch("""
                    SELECT 
                        c.chunk_id,
                        c.file_id,
                        c.chunk_index,
                        c.text,
                        c.page_number,
                        f.filename
                    FROM ingestion_chunks c
                    JOIN ingestion_files f ON c.file_id = f.file_id
                    WHERE c.chunk_id = ANY($1::uuid[])
                """, chunk_ids)
                
                # Build chunk lookup
                chunk_lookup = {str(c["chunk_id"]): c for c in chunks}
                
                # Combine results
                results = []
                for result in search_results:
                    chunk_id = result["chunk_id"]
                    if chunk_id in chunk_lookup:
                        chunk = chunk_lookup[chunk_id]
                        results.append({
                            "fileId": str(chunk["file_id"]),
                            "filename": chunk["filename"],
                            "chunkIndex": chunk["chunk_index"],
                            "pageNumber": chunk["page_number"],
                            "text": chunk["text"],
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
        - Vectors from Milvus (if not shared via content_hash)
        - Metadata from PostgreSQL
    
    Note: Vectors are shared via content_hash, so deletion only removes
    this file's reference. Vectors remain if other files reference them.
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
                SELECT user_id, storage_path, content_hash
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
            content_hash = file_row["content_hash"]
            
            # Check if other files share this content_hash
            other_files = await conn.fetchrow("""
                SELECT COUNT(*) as count
                FROM ingestion_files
                WHERE content_hash = $1 AND file_id != $2
            """, content_hash, uuid.UUID(fileId))
            
            # Delete file from MinIO
            try:
                await minio_service.delete_file(storage_path)
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
            
            # Note: Milvus vectors are not deleted here
            # They remain for other files with same content_hash
            # If no other files share the hash, vectors can be cleaned up separately
            
            logger.info(
                "File deleted",
                file_id=fileId,
                user_id=user_id,
                storage_path=storage_path,
                shared_content=other_files["count"] > 0 if other_files else False,
            )
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "message": "File deleted successfully",
                    "fileId": fileId,
                    "vectorsShared": other_files["count"] > 0 if other_files else False,
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

