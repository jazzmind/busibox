"""
File upload endpoint with chunked upload support.

Handles:
- Streaming file upload with SHA-256 hash calculation
- Duplicate detection and vector reuse
- File storage in MinIO
- Database record creation
- Job queuing in Redis Streams
"""

import json
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse

from api.services.minio import MinIOService
from api.services.postgres import PostgresService
from api.services.redis import RedisService
from shared.config import Config

logger = structlog.get_logger()

router = APIRouter()

# Supported MIME types
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # DOCX
    "text/plain",
    "text/html",
    "text/markdown",
    "text/csv",
    "application/json",
    "video/mp4",  # Video files (stored but not processed)
    "image/jpeg",  # Image files (for video posters, stored but not processed)
    "image/png",   # Image files (for video posters, stored but not processed)
    "image/webp",  # Image files (for video posters, stored but not processed)
}


def validate_mime_type(mime_type: str) -> bool:
    """Validate that MIME type is supported."""
    return mime_type in SUPPORTED_MIME_TYPES


@router.post("")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(None),
    processing_config: Optional[str] = Form(None),
):
    """
    Upload a document for processing.
    
    Supports chunked upload with streaming. Calculates SHA-256 hash during upload.
    Detects duplicates and reuses vectors if content already processed.
    
    Headers:
        X-User-Id: User UUID (required)
    
    Body:
        file: Document file (multipart/form-data)
        metadata: Optional JSON metadata string
        processing_config: Optional JSON processing configuration string
    
    Returns:
        fileId: UUID for tracking status
        status: Initial status
        duplicate: Whether this is a duplicate (vectors reused)
    """
    user_id = request.state.user_id
    
    # Validate file
    if not file.filename:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Filename required"}
        )
    
    # Validate MIME type
    if not validate_mime_type(file.content_type):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": f"Unsupported file type: {file.content_type}",
                "supported_types": list(SUPPORTED_MIME_TYPES),
            }
        )
    
    # Generate file ID
    file_id = str(uuid.uuid4())
    
    # Load configuration
    config = Config().to_dict()
    
    # Initialize services
    minio_service = MinIOService(config)
    postgres_service = PostgresService(config)
    redis_service = RedisService(config)
    
    await postgres_service.connect()
    await redis_service.connect()
    
    try:
        # Calculate content hash and upload to MinIO
        storage_path = f"{user_id}/{file_id}/{file.filename}"
        
        logger.info(
            "Starting file upload",
            file_id=file_id,
            user_id=user_id,
            filename=file.filename,
            mime_type=file.content_type,
        )
        
        # Upload file and calculate hash
        content_hash = await minio_service.upload_file_stream(
            file.file,
            storage_path,
        )
        
        # Check for duplicate
        existing = await postgres_service.check_duplicate(content_hash)
        
        if existing:
            # Duplicate detected - reuse vectors
            logger.info(
                "Duplicate file detected, reusing vectors",
                file_id=file_id,
                existing_file_id=existing["file_id"],
                content_hash=content_hash,
            )
            
            await postgres_service.reuse_vectors(
                file_id,
                existing["file_id"],
                user_id,
            )
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "fileId": file_id,
                    "status": "completed",
                    "duplicate": True,
                    "message": "File already processed, vectors reused",
                    "existingFileId": existing["file_id"],
                }
            )
        
        # Parse metadata if provided
        parsed_metadata = {}
        if metadata:
            try:
                parsed_metadata = json.loads(metadata)
                if not isinstance(parsed_metadata, dict):
                    logger.warning(
                        "Metadata is not a JSON object, using empty dict",
                        file_id=file_id,
                        metadata_type=type(parsed_metadata).__name__,
                    )
                    parsed_metadata = {}
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to parse metadata JSON, using empty dict",
                    file_id=file_id,
                    error=str(e),
                )
                parsed_metadata = {}
        
        # Parse processing config if provided
        parsed_processing_config = {}
        if processing_config:
            try:
                parsed_processing_config = json.loads(processing_config)
                if not isinstance(parsed_processing_config, dict):
                    logger.warning(
                        "Processing config is not a JSON object, using empty dict",
                        file_id=file_id,
                        config_type=type(parsed_processing_config).__name__,
                    )
                    parsed_processing_config = {}
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to parse processing config JSON, using empty dict",
                    file_id=file_id,
                    error=str(e),
                )
                parsed_processing_config = {}
        
        # New file - create record
        await postgres_service.create_file_record(
            file_id=file_id,
            user_id=user_id,
            filename=file.filename,
            original_filename=file.filename,
            mime_type=file.content_type,
            size_bytes=file.size or 0,  # Note: file.size may not be accurate for streaming
            storage_path=storage_path,
            content_hash=content_hash,
            metadata=parsed_metadata,
        )
        
        # Skip processing queue for video and image files (they're stored but not processed)
        is_video = file.content_type and file.content_type.startswith("video/")
        is_image = file.content_type and file.content_type.startswith("image/")
        
        if not is_video and not is_image:
            # Queue job in Redis with processing config for non-video files
            await redis_service.ensure_consumer_group()
            await redis_service.add_job(
                file_id=file_id,
                user_id=user_id,
                storage_path=storage_path,
                mime_type=file.content_type,
                original_filename=file.filename,
                processing_config=parsed_processing_config,
            )
            
            logger.info(
                "File uploaded and queued",
                file_id=file_id,
                user_id=user_id,
                content_hash=content_hash,
            )
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "fileId": file_id,
                    "status": "queued",
                    "duplicate": False,
                    "message": "File uploaded and queued for processing",
                }
            )
        else:
            # Video and image files are stored but not processed
            file_type = "Video" if is_video else "Image"
            logger.info(
                f"{file_type} file uploaded and stored",
                file_id=file_id,
                user_id=user_id,
                content_hash=content_hash,
            )
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "fileId": file_id,
                    "status": "completed",
                    "duplicate": False,
                    "message": f"{file_type} file uploaded and stored",
                }
            )
    
    except Exception as e:
        logger.error(
            "File upload failed",
            file_id=file_id,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "File upload failed", "details": str(e)}
        )
    
    finally:
        await postgres_service.disconnect()
        await redis_service.disconnect()

