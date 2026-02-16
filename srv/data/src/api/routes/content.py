"""
Content data endpoint for web research and scraped content.

Handles:
- Ingesting text/markdown content (not file uploads)
- Library folder resolution (local, no external API calls)
- Document storage and processing queue
"""

import uuid
import hashlib
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.middleware.jwt_auth import ScopeChecker
from api.services.minio_service import MinIOService
from api.services.postgres import PostgresService
from api.services.redis_service import RedisService
from api.services.library_service import LibraryService
from api.services.encryption_client import EncryptionClient
from api.routes.upload import _create_delegation_token_for_processing
from shared.config import Config

logger = structlog.get_logger()

router = APIRouter()

# Scope dependencies
require_data_write = ScopeChecker("data.write")


def _extract_bearer_token_from_request(request: Request) -> Optional[str]:
    """Return bearer token from Authorization header if available."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        return token or None
    return None


class ContentDataRequest(BaseModel):
    """Request body for content data."""
    content: str = Field(..., description="Text or markdown content to data")
    title: str = Field(..., description="Document title")
    url: Optional[str] = Field(None, description="Source URL (for web content)")
    folder: Optional[str] = Field(None, description="Target folder name (e.g., 'personal-research')")
    library_id: Optional[str] = Field(None, description="Target library ID (alternative to folder)")
    metadata: Optional[dict] = Field(default_factory=dict, description="Additional metadata")


@router.post("/content", dependencies=[Depends(require_data_write)])
async def data_content(
    request: Request,
    body: ContentDataRequest,
):
    """
    Ingest text/markdown content as a document.
    
    This endpoint is designed for web research workflows that scrape content
    and need to store it in the user's document library.
    
    The folder parameter maps to personal library types:
    - "personal", "personal-docs", "docs" -> Personal DOCS library
    - "personal-research", "research" -> Personal RESEARCH library
    - "personal-tasks", "tasks" -> Personal TASKS library (for agent task outputs)
    
    Headers:
        Authorization: Bearer <JWT> - JWT with user identity
    
    Body:
        content: Text or markdown content
        title: Document title
        url: Optional source URL
        folder: Target folder name (resolved via Busibox Portal API)
        library_id: Target library ID (alternative to folder)
        metadata: Additional metadata
    
    Returns:
        fileId: UUID for tracking
        libraryId: Library where document was stored
        status: Initial processing status
    """
    user_id = request.state.user_id
    auth_header = request.headers.get("Authorization")
    
    # Validate we have content
    if not body.content or not body.content.strip():
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Content is required and cannot be empty"}
        )
    
    if not body.title or not body.title.strip():
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Title is required"}
        )
    
    # Generate file ID
    file_id = str(uuid.uuid4())
    
    # Load configuration
    config = Config().to_dict()
    
    # Initialize services
    from api.main import pg_service
    minio_service = MinIOService(config)
    redis_service = RedisService(config)
    encryption_client = EncryptionClient(config)
    
    await redis_service.connect()
    
    try:
        # Resolve library ID from folder name if provided
        library_id = body.library_id
        if body.folder and not library_id:
            library_id = await _resolve_library_from_folder(
                folder=body.folder,
                user_id=user_id,
                pg_service=pg_service,
            )
            if not library_id:
                logger.warning(
                    "Could not resolve folder to library",
                    folder=body.folder,
                    user_id=user_id,
                )
                # Continue without library_id - will be stored as personal
        
        # Prepare content as markdown
        content_bytes = body.content.encode("utf-8")
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        file_size = len(content_bytes)
        
        # Generate filename from title
        safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in body.title)
        safe_title = safe_title.strip()[:100] or "document"
        filename = f"{safe_title}.md"
        
        # Storage path
        storage_path = f"personal/{user_id}/{file_id}/{filename}"
        
        logger.info(
            "Starting content data",
            file_id=file_id,
            user_id=user_id,
            title=body.title,
            url=body.url,
            folder=body.folder,
            library_id=library_id,
            content_length=file_size,
        )
        
        # Encrypt content before storage (personal files only - use user_id as key owner)
        content_to_store = content_bytes
        is_encrypted = False
        
        if encryption_client.enabled:
            # Get user's JWT token from request context for keystore API calls
            user_ctx = getattr(request.state, 'user_context', None)
            user_token = user_ctx.token if user_ctx else None
            if not user_token:
                user_token = _extract_bearer_token_from_request(request)
            
            content_to_store = await encryption_client.encrypt_for_upload(
                file_id=file_id,
                content=content_bytes,
                user_token=user_token,
                user_id=user_id,  # Personal files use user_id for key ownership
            )
            
            # Check if encryption actually happened (content changed)
            is_encrypted = content_to_store != content_bytes
            
            if is_encrypted:
                logger.info(
                    "Content encrypted for storage",
                    file_id=file_id,
                    original_size=len(content_bytes),
                    encrypted_size=len(content_to_store),
                )
        
        # Store encrypted (or original if encryption disabled) content in MinIO
        await minio_service.upload_bytes(
            data=content_to_store,
            object_path=storage_path,
            content_type='text/markdown',
        )
        
        # Prepare metadata
        doc_metadata = body.metadata.copy() if body.metadata else {}
        if body.url:
            doc_metadata["source_url"] = body.url
        if body.folder:
            doc_metadata["folder"] = body.folder
        if library_id:
            doc_metadata["library_id"] = library_id
        
        # Create database record using pg_service method (handles RLS correctly)
        print(f"[content] Creating file record: file_id={file_id}, user_id={user_id}")
        print(f"[content] request.state.user_id={getattr(request.state, 'user_id', 'NOT SET')}")
        print(f"[content] request.state.role_ids={getattr(request.state, 'role_ids', 'NOT SET')}")
        
        await pg_service.create_file_record(
            file_id=file_id,
            user_id=user_id,
            filename=filename,
            original_filename=filename,
            mime_type="text/markdown",
            size_bytes=file_size,
            storage_path=storage_path,
            content_hash=content_hash,
            metadata=doc_metadata,
            visibility="personal",
            request=request,
            library_id=library_id,  # Associate with personal library
            is_encrypted=is_encrypted,  # Track encryption status
        )
        print(f"[content] File record created successfully: {file_id}, library_id={library_id}")
        
        # Create a delegation token so the worker can perform Zero Trust
        # token exchanges on behalf of this user during processing
        user_ctx = getattr(request.state, 'user_context', None)
        content_user_token = user_ctx.token if user_ctx else None
        if not content_user_token:
            content_user_token = _extract_bearer_token_from_request(request)
        delegation_token = await _create_delegation_token_for_processing(
            user_token=content_user_token,
            file_id=file_id,
        ) if content_user_token else None
        
        # Queue for processing using add_job method
        await redis_service.add_job(
            file_id=file_id,
            user_id=user_id,
            storage_path=storage_path,
            mime_type="text/markdown",
            original_filename=filename,
            metadata=doc_metadata,
            visibility="personal",
            delegation_token=delegation_token,
        )
        
        logger.info(
            "Content data queued",
            file_id=file_id,
            user_id=user_id,
            library_id=library_id,
        )
        
        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content={
                "fileId": file_id,
                "libraryId": library_id,
                "status": "queued",
                "title": body.title,
                "url": body.url,
            }
        )
        
    except Exception as e:
        logger.error(
            "Content data failed",
            file_id=file_id,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to ingest content", "details": str(e)}
        )
    finally:
        await redis_service.disconnect()


async def _resolve_library_from_folder(
    folder: str,
    user_id: str,
    pg_service: PostgresService,
) -> str | None:
    """
    Resolve a folder name to a library ID using the local library service.
    
    This replaces the previous implementation that called Busibox Portal API.
    The library service handles folder name mapping and auto-creates
    personal libraries (DOCS, RESEARCH, TASKS) if they don't exist.
    
    Args:
        folder: Folder name (e.g., "personal-research", "personal-tasks")
        user_id: User ID for personal library resolution
        pg_service: PostgreSQL service with connection pool
        
    Returns:
        Library ID or None if resolution fails
    """
    try:
        # Ensure we have a connection pool
        if not pg_service.pool:
            await pg_service.connect()
        
        # Use the local library service
        library_service = LibraryService(pg_service.pool)
        library = await library_service.get_library_by_folder(user_id, folder)
        
        if library:
            library_id = str(library["id"])
            logger.info(
                "Resolved folder to library",
                folder=folder,
                library_id=library_id,
                library_name=library["name"],
            )
            return library_id
        
        logger.warning(
            "Could not resolve folder to library",
            folder=folder,
            user_id=user_id,
        )
        return None
        
    except Exception as e:
        logger.error(
            "Library resolution error",
            folder=folder,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return None
