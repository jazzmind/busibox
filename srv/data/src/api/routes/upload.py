"""
File upload endpoint with chunked upload support and role-based access control.

Handles:
- Streaming file upload with SHA-256 hash calculation
- Duplicate detection and vector reuse
- File storage in MinIO
- Database record creation
- Job queuing in Redis Streams
- Role-based visibility (personal or shared with roles)
"""

import json
import os
import uuid
from typing import List, Optional

import httpx
import structlog
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse

from api.middleware.jwt_auth import ScopeChecker
from api.services.minio_service import MinIOService
from api.services.postgres import PostgresService
from api.services.redis_service import RedisService
from api.services.encryption_client import EncryptionClient
from shared.config import Config

logger = structlog.get_logger()

router = APIRouter()


async def _create_delegation_token_for_processing(user_token: str, file_id: str) -> Optional[str]:
    """
    Create a delegation token for the background worker to process this file.
    
    The delegation token preserves the user's identity and scopes so the worker
    can perform Zero Trust token exchanges for downstream operations (decryption,
    RLS-enforced DB queries, etc.) even after the upload request completes.
    
    Args:
        user_token: The user's current JWT from the upload request
        file_id: File ID for logging/naming the delegation
        
    Returns:
        Delegation JWT token string, or None if creation fails (non-fatal)
    """
    authz_base_url = os.getenv("AUTHZ_BASE_URL", "")
    if not authz_base_url:
        logger.warning(
            "AUTHZ_BASE_URL not configured, cannot create delegation token for worker",
            file_id=file_id,
        )
        return None
    
    delegation_url = f"{authz_base_url}/oauth/delegation"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                delegation_url,
                json={
                    "subject_token": user_token,
                    "name": f"data-processing:{file_id[:8]}",
                    "scopes": [],  # Empty = inherit all user's scopes from RBAC
                    "expires_in_seconds": 86400,  # 24 hours for processing
                },
            )
            
            if response.status_code == 200:
                data = response.json()
                delegation_token = data.get("delegation_token")
                if delegation_token:
                    logger.info(
                        "Delegation token created for file processing",
                        file_id=file_id,
                        jti=data.get("jti"),
                        expires_in=data.get("expires_in"),
                    )
                    return delegation_token
                else:
                    logger.warning(
                        "No delegation_token in response",
                        file_id=file_id,
                    )
                    return None
            else:
                logger.warning(
                    "Failed to create delegation token for processing",
                    file_id=file_id,
                    status_code=response.status_code,
                    response=response.text[:200],
                )
                return None
                
    except Exception as e:
        logger.warning(
            "Error creating delegation token for processing (non-fatal)",
            file_id=file_id,
            error=str(e),
        )
        return None

# Scope dependencies
require_data_write = ScopeChecker("data.write")

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


@router.post("", dependencies=[Depends(require_data_write)])
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(None),
    processing_config: Optional[str] = Form(None),
    visibility: str = Form("personal"),
    role_ids: Optional[str] = Form(None),
    force_reprocess: Optional[str] = Form(None),
    library_id: Optional[str] = Form(None),
):
    """
    Upload a document for processing with role-based access control.
    
    Supports chunked upload with streaming. Calculates SHA-256 hash during upload.
    Detects duplicates and reuses vectors if content already processed (unless force_reprocess=true).
    
    Headers:
        Authorization: Bearer <JWT> (preferred) - JWT with user identity and role permissions
        Authorization: Bearer <JWT> (required)
    
    Body:
        file: Document file (multipart/form-data)
        metadata: Optional JSON metadata string
        processing_config: Optional JSON processing configuration string
        visibility: 'personal' (default) or 'shared'
        role_ids: Comma-separated role UUIDs (required if visibility='shared')
        library_id: Optional target library UUID
    
    Returns:
        fileId: UUID for tracking status
        status: Initial status
        duplicate: Whether this is a duplicate (vectors reused)
        visibility: Document visibility setting
        roles: List of role IDs (if shared)
        libraryId: Library ID where document was stored
    """
    user_id = request.state.user_id
    
    # Parse role_ids from comma-separated string
    parsed_role_ids: List[str] = []
    if role_ids:
        parsed_role_ids = [r.strip() for r in role_ids.split(",") if r.strip()]
    
    # Validate visibility
    if visibility not in ("personal", "shared"):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": f"Invalid visibility: {visibility}. Must be 'personal' or 'shared'."}
        )
    
    # Validate role_ids for shared documents
    if visibility == "shared":
        if not parsed_role_ids:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "role_ids required for shared visibility"}
            )
        
        # Verify user has create permission on all specified roles
        user_create_roles = getattr(request.state, "role_ids_create", [])
        for role_id in parsed_role_ids:
            if role_id not in user_create_roles:
                logger.warning(
                    "User lacks create permission on role",
                    user_id=user_id,
                    role_id=role_id,
                    user_create_roles=user_create_roles,
                )
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "error": f"You don't have 'create' permission on role: {role_id}",
                        "hint": "You can only upload documents to roles you have create permission on",
                    }
                )
    
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
    from api.main import pg_service  # Use shared PostgresService instance
    from api.services.library_service import LibraryService
    minio_service = MinIOService(config)
    redis_service = RedisService(config)
    encryption_client = EncryptionClient(config)
    library_service = LibraryService(pg_service.pool)
    
    await redis_service.connect()
    
    try:
        # Resolve library_id (auto-create personal library if needed for personal files)
        resolved_library_id = library_id
        if visibility == "personal" and not library_id:
            # Ensure user has a personal DOCS library and use it
            await library_service.ensure_all_personal_libraries(user_id)
            personal_libs = await library_service.list_user_libraries(user_id, include_shared=False)
            docs_lib = next((lib for lib in personal_libs if lib["library_type"] == "DOCS"), None)
            if docs_lib:
                resolved_library_id = str(docs_lib["id"])
                logger.info(
                    "Auto-assigned to personal DOCS library",
                    file_id=file_id,
                    library_id=resolved_library_id,
                )
        # Determine storage path based on visibility
        # Personal files: personal/{user_id}/{file_id}/{filename}
        # Shared files: role/{primary_role_id}/{file_id}/{filename}
        if visibility == "shared" and parsed_role_ids:
            # Use first role as the "owner" role for storage organization
            primary_role_id = parsed_role_ids[0]
            storage_path = f"role/{primary_role_id}/{file_id}/{file.filename}"
        else:
            storage_path = f"personal/{user_id}/{file_id}/{file.filename}"
        
        logger.info(
            "Starting file upload",
            file_id=file_id,
            user_id=user_id,
            filename=file.filename,
            mime_type=file.content_type,
            visibility=visibility,
            storage_path=storage_path,
        )
        
        # Read file content to calculate size and hash
        file_content = await file.read()
        file_size = len(file_content)
        
        # Calculate hash BEFORE encryption (for deduplication)
        import hashlib
        content_hash = hashlib.sha256(file_content).hexdigest()
        
        # Encrypt content if envelope encryption is enabled
        # Skip encryption for video and image files (large binary, not user-sensitive documents)
        content_to_store = file_content
        is_encrypted = False
        is_media_file = (file.content_type or "").startswith("video/") or (file.content_type or "").startswith("image/")
        
        if encryption_client.enabled and not is_media_file:
            # Determine which keys to wrap the DEK with
            encrypt_role_ids = parsed_role_ids if visibility == "shared" else None
            encrypt_user_id = user_id if visibility == "personal" else None
            
            # Get user's JWT token from request context for keystore API calls
            user_token = getattr(request.state, 'user_context', None)
            user_token = user_token.token if user_token else None
            
            content_to_store = await encryption_client.encrypt_for_upload(
                file_id=file_id,
                content=file_content,
                user_token=user_token,
                role_ids=encrypt_role_ids,
                user_id=encrypt_user_id,
            )
            
            # Check if encryption actually happened (content changed)
            is_encrypted = content_to_store != file_content
            
            if is_encrypted:
                logger.info(
                    "File content encrypted for storage",
                    file_id=file_id,
                    original_size=len(file_content),
                    encrypted_size=len(content_to_store),
                )
        else:
            # Encryption disabled or media file - still need user_token for delegation token
            user_token = getattr(request.state, 'user_context', None)
            user_token = user_token.token if user_token else None
            if is_media_file:
                logger.info(
                    "Skipping encryption for media file",
                    file_id=file_id,
                    mime_type=file.content_type,
                )
        
        # Reset file pointer for upload
        import io
        file_stream = io.BytesIO(content_to_store)
        
        # Upload encrypted (or original) content to MinIO
        # Note: content_hash was calculated before encryption for consistency
        await minio_service.upload_file_stream(
            file_stream,
            storage_path,
        )
        
        # Check for duplicate (skip if force_reprocess requested)
        should_check_duplicate = force_reprocess != "true"
        existing = await pg_service.check_duplicate(content_hash, request) if should_check_duplicate else None
        
        if existing:
            # Duplicate detected - reuse vectors
            logger.info(
                "Duplicate file detected, reusing vectors",
                file_id=file_id,
                existing_file_id=existing["file_id"],
                content_hash=content_hash,
            )
            
            await pg_service.reuse_vectors(
                file_id,
                existing["file_id"],
                user_id,
                request=request,
            )
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "fileId": file_id,
                    "filename": file.filename,
                    "mimeType": file.content_type,
                    "sizeBytes": file_size,
                    "url": f"/files/{file_id}",
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
        
        # Add force_reprocess flag to processing config if requested
        if force_reprocess == "true":
            parsed_processing_config["force_reprocess"] = True
            logger.info(
                "Force reprocess enabled, skipping duplicate detection",
                file_id=file_id,
            )
        
        # New file - create record with visibility, roles, and library
        await pg_service.create_file_record(
            file_id=file_id,
            user_id=user_id,
            filename=file.filename,
            original_filename=file.filename,
            mime_type=file.content_type,
            size_bytes=file.size or 0,  # Note: file.size may not be accurate for streaming
            storage_path=storage_path,
            content_hash=content_hash,
            metadata=parsed_metadata,
            visibility=visibility,
            role_ids=parsed_role_ids if visibility == "shared" else None,
            request=request,
            library_id=resolved_library_id,
            is_encrypted=is_encrypted,  # Track encryption status
        )
        
        # Skip processing queue for video and image files (they're stored but not processed)
        is_video = file.content_type and file.content_type.startswith("video/")
        is_image = file.content_type and file.content_type.startswith("image/")
        
        if not is_video and not is_image:
            # Create a delegation token so the worker can perform Zero Trust
            # token exchanges on behalf of this user during processing
            delegation_token = await _create_delegation_token_for_processing(
                user_token=user_token,
                file_id=file_id,
            ) if user_token else None
            
            # Queue job in Redis with processing config and role information for non-video files
            await redis_service.ensure_consumer_group()
            await redis_service.add_job(
                file_id=file_id,
                user_id=user_id,
                storage_path=storage_path,
                mime_type=file.content_type,
                original_filename=file.filename,
                processing_config=parsed_processing_config,
                visibility=visibility,
                role_ids=parsed_role_ids if visibility == "shared" else None,
                delegation_token=delegation_token,
            )
            
            logger.info(
                "File uploaded and queued",
                file_id=file_id,
                user_id=user_id,
                visibility=visibility,
                role_count=len(parsed_role_ids) if parsed_role_ids else 0,
                content_hash=content_hash,
            )
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "fileId": file_id,
                    "filename": file.filename,
                    "mimeType": file.content_type,
                    "sizeBytes": file_size,
                    "url": f"/files/{file_id}",
                    "status": "queued",
                    "duplicate": False,
                    "message": "File uploaded and queued for processing",
                    "visibility": visibility,
                    "roles": parsed_role_ids if visibility == "shared" else None,
                    "libraryId": resolved_library_id,
                }
            )
        else:
            # Video and image files are stored but not processed
            file_type = "Video" if is_video else "Image"
            logger.info(
                f"{file_type} file uploaded and stored",
                file_id=file_id,
                user_id=user_id,
                visibility=visibility,
                role_count=len(parsed_role_ids) if parsed_role_ids else 0,
                content_hash=content_hash,
            )
            
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "fileId": file_id,
                    "filename": file.filename,
                    "mimeType": file.content_type,
                    "sizeBytes": file_size,
                    "url": f"/files/{file_id}",
                    "status": "completed",
                    "duplicate": False,
                    "message": f"{file_type} file uploaded and stored",
                    "visibility": visibility,
                    "roles": parsed_role_ids if visibility == "shared" else None,
                    "libraryId": resolved_library_id,
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
        # Don't disconnect pg_service - it's a singleton shared across requests
        await redis_service.disconnect()

