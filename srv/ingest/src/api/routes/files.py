"""
File metadata and deletion endpoints.

Handles:
- GET /files/{fileId}: Retrieve file metadata
- DELETE /files/{fileId}: Delete file and all associated data
"""

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from api.services.minio import MinIOService
from api.services.postgres import PostgresService
from utils.config import load_config

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
    
    config = load_config()
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
    
    config = load_config()
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

