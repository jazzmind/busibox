"""
API routes for markdown and HTML rendering.

Provides endpoints to retrieve markdown content and rendered HTML.
"""

from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse, Response
import uuid
import structlog

from api.middleware.jwt_auth import ScopeChecker
from shared.config import Config
from api.services.minio_service import MinIOService
from processors.html_renderer import HTMLRenderer

logger = structlog.get_logger()

router = APIRouter()

# Scope dependencies
require_data_read = ScopeChecker("data.read")


async def _get_file_metadata(postgres_service, file_uuid, user_uuid, fields, request=None):
    """
    Helper to get file metadata using async PostgreSQL.
    
    Args:
        postgres_service: PostgresService instance (async)
        file_uuid: File UUID (string or UUID object)
        user_uuid: User UUID (string or UUID object)
        fields: List of field names to select
        request: Optional FastAPI Request for RLS context
        
    Returns:
        Dict with file data or None if not found
    """
    import uuid as uuid_mod
    field_list = ", ".join(fields)
    
    # Handle both string and UUID inputs
    file_id = file_uuid if isinstance(file_uuid, uuid_mod.UUID) else uuid_mod.UUID(file_uuid)
    user_id = user_uuid if isinstance(user_uuid, uuid_mod.UUID) else uuid_mod.UUID(user_uuid)
    
    async with postgres_service.acquire(request) as conn:
        # Use owner_id for RLS check, not user_id
        row = await conn.fetchrow(
            f"""SELECT {field_list}
               FROM data_files 
               WHERE file_id = $1 AND owner_id = $2""",
            file_id,
            user_id
        )
        
        if not row:
            return None
        
        # Convert row to dict
        return dict(row)


@router.get("/{fileId}/markdown", dependencies=[Depends(require_data_read)])
async def get_markdown(fileId: str, request: Request):
    """
    Get markdown content for a file.
    
    Supports both:
    - Processed files with markdown_path (generated from PDF/DOCX/etc)
    - Native markdown files (task outputs, web research content)

    Returns:
        JSON with markdown content and metadata
    """
    user_id = request.state.user_id

    config = Config().to_dict()
    from api.main import pg_service as postgres_service  # Use shared async PostgresService instance
    minio_service = MinIOService(config)

    try:
        # Validate fileId is a valid UUID
        file_uuid = uuid.UUID(fileId)
        user_uuid = uuid.UUID(user_id)

        # Get file metadata - include storage_path and mime_type for native markdown fallback
        file_data = await _get_file_metadata(
            postgres_service, 
            file_uuid, 
            user_uuid,
            ["file_id", "owner_id", "original_filename", "markdown_path", "has_markdown", "image_count", "storage_path", "mime_type"],
            request=request
        )

        if not file_data:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "File not found or unauthorized"}
            )

        # Determine which path to use for markdown content
        if file_data["has_markdown"] and file_data["markdown_path"]:
            # Use processed markdown
            markdown_content = minio_service.get_file_content(file_data["markdown_path"])
        elif file_data["mime_type"] in ("text/markdown", "text/x-markdown"):
            # Native markdown file - serve directly from storage_path
            markdown_content = minio_service.get_file_content(file_data["storage_path"])
        else:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "Markdown not available for this file"}
            )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "fileId": str(file_data["file_id"]),
                "filename": file_data["original_filename"],
                "markdown": markdown_content,
                "hasImages": file_data["image_count"] > 0 if file_data["image_count"] else False,
                "imageCount": file_data["image_count"] or 0
            }
        )

    except ValueError as e:
        logger.warning(
            "Invalid file ID format for markdown",
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
            "Failed to get markdown",
            file_id=fileId,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to retrieve markdown", "details": str(e)}
        )
    finally:
        # Don't close - pg_service is a singleton shared across requests
        pass


@router.get("/{fileId}/html", dependencies=[Depends(require_data_read)])
async def get_html(fileId: str, request: Request):
    """
    Get rendered HTML content for a file with table of contents.
    
    Supports both:
    - Processed files with markdown_path (generated from PDF/DOCX/etc)
    - Native markdown files (task outputs, web research content)

    Returns:
        JSON with HTML content and TOC
    """
    user_id = request.state.user_id

    config = Config().to_dict()
    from api.main import pg_service as postgres_service  # Use shared async PostgresService instance
    minio_service = MinIOService(config)

    try:
        # Validate fileId is a valid UUID
        file_uuid = uuid.UUID(fileId)
        user_uuid = uuid.UUID(user_id)

        # Get file metadata - include storage_path and mime_type for native markdown fallback
        file_data = await _get_file_metadata(
            postgres_service,
            file_uuid,
            user_uuid,
            ["file_id", "owner_id", "original_filename", "markdown_path", "has_markdown", "image_count", "storage_path", "mime_type"],
            request=request
        )

        if not file_data:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "File not found or unauthorized"}
            )

        # Determine which path to use for markdown content
        if file_data["has_markdown"] and file_data["markdown_path"]:
            # Use processed markdown
            markdown_content = minio_service.get_file_content(file_data["markdown_path"])
        elif file_data["mime_type"] in ("text/markdown", "text/x-markdown"):
            # Native markdown file - serve directly from storage_path
            markdown_content = minio_service.get_file_content(file_data["storage_path"])
        else:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "Markdown not available for this file"}
            )

        # Render to HTML
        renderer = HTMLRenderer()
        html_content, toc = renderer.render(markdown_content, file_id=fileId)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "fileId": str(file_data["file_id"]),
                "filename": file_data["original_filename"],
                "html": html_content,
                "toc": toc,
                "hasImages": file_data["image_count"] > 0 if file_data["image_count"] else False,
                "imageCount": file_data["image_count"] or 0
            }
        )

    except ValueError as e:
        logger.warning(
            "Invalid file ID format for HTML",
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
            "Failed to get HTML",
            file_id=fileId,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to retrieve HTML", "details": str(e)}
        )
    finally:
        # Don't close - pg_service is a singleton shared across requests
        pass

@router.get("/{fileId}/image-urls", dependencies=[Depends(require_data_read)])
async def get_image_urls(fileId: str, request: Request, expiry: int = 3600):
    """
    Get presigned MinIO URLs for all images of a file in a single auth call.

    The frontend can use these URLs directly in <img> tags, bypassing per-image
    auth overhead. Each URL is self-authenticating via MinIO's presigned signature.

    Args:
        fileId: File UUID
        expiry: URL expiration in seconds (default 1 hour)

    Returns:
        JSON with mapping of image index to presigned URL
    """
    user_id = request.state.user_id

    config = Config().to_dict()
    from api.main import pg_service as postgres_service
    minio_service = MinIOService(config)

    try:
        file_uuid = uuid.UUID(fileId)
        user_uuid = uuid.UUID(user_id)

        file_data = await _get_file_metadata(
            postgres_service,
            file_uuid,
            user_uuid,
            ["file_id", "user_id", "images_path", "image_count"],
            request=request,
        )

        if not file_data:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "File not found or unauthorized"},
            )

        image_count = file_data.get("image_count") or 0
        images_path = file_data.get("images_path")

        if not images_path or image_count == 0:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"fileId": fileId, "imageCount": 0, "urls": {}},
            )

        import asyncio
        import json as json_mod
        from datetime import timedelta

        loop = asyncio.get_event_loop()
        urls: dict[str, str] = {}

        # URL rewriting: convert internal MinIO URLs to external nginx proxy path
        minio_endpoint = config.get("minio_endpoint", "minio:9000")
        minio_secure = config.get("minio_secure", False)
        internal_scheme = "https" if minio_secure else "http"
        internal_prefix = f"{internal_scheme}://{minio_endpoint}/"
        external_prefix = f"{config.get('minio_external_base_url', '/files')}/"

        for idx in range(image_count):
            image_path = f"{images_path}/image_{idx}.png"
            try:
                url = await loop.run_in_executor(
                    None,
                    lambda p=image_path: minio_service.client.presigned_get_object(
                        minio_service.bucket, p, expires=timedelta(seconds=expiry)
                    ),
                )
                urls[str(idx)] = url.replace(internal_prefix, external_prefix, 1)
            except Exception as e:
                logger.warning(
                    "Failed to generate presigned URL for image",
                    file_id=fileId,
                    image_index=idx,
                    error=str(e),
                )

        # Load image metadata (duplicate/decorative/background flags) if available
        image_metadata: dict[str, dict] = {}
        try:
            metadata_path = f"{images_path}/metadata.json"
            metadata_json = minio_service.get_file_content(metadata_path)
            raw_metadata = json_mod.loads(metadata_json)
            if isinstance(raw_metadata, list):
                for item in raw_metadata:
                    image_metadata[str(item.get("index", ""))] = {
                        "width": item.get("width"),
                        "height": item.get("height"),
                        "is_duplicate": item.get("is_duplicate", False),
                        "is_decorative": item.get("is_decorative", False),
                        "is_background": item.get("is_background", False),
                    }
        except Exception:
            pass

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "fileId": fileId,
                "imageCount": image_count,
                "urls": urls,
                "metadata": image_metadata,
                "expiresIn": expiry,
            },
        )

    except ValueError as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Invalid file ID format"},
        )
    except Exception as e:
        logger.error(
            "Failed to get image URLs",
            file_id=fileId,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to generate image URLs"},
        )
    finally:
        pass


@router.get("/{fileId}/images/{imageIndex}", dependencies=[Depends(require_data_read)])
async def get_image(fileId: str, imageIndex: int, request: Request):
    """
    Get an extracted image by index.

    Args:
        fileId: File UUID
        imageIndex: Image index (0-based)

    Returns:
        Image binary data with appropriate content-type
    """
    user_id = request.state.user_id

    config = Config().to_dict()
    from api.main import pg_service as postgres_service  # Use shared async PostgresService instance
    minio_service = MinIOService(config)

    try:
        # Validate fileId is a valid UUID
        file_uuid = uuid.UUID(fileId)
        user_uuid = uuid.UUID(user_id)

        # Validate imageIndex
        if imageIndex < 0:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "Invalid image index"}
            )

        # Get file metadata
        file_data = await _get_file_metadata(
            postgres_service,
            file_uuid,
            user_uuid,
            ["file_id", "user_id", "images_path", "image_count"],
            request=request
        )

        if not file_data:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "File not found or unauthorized"}
            )

        if not file_data["images_path"] or not file_data["image_count"] or file_data["image_count"] == 0:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "No images available for this file"}
            )

        if imageIndex >= file_data["image_count"]:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": f"Image index {imageIndex} not found"}
            )

        # Construct image path
        image_path = f"{file_data['images_path']}/image_{imageIndex}.png"

        # Fetch image from MinIO
        try:
            image_data = minio_service.get_file_bytes(image_path)
        except Exception as e:
            logger.warning(
                "Image not found in MinIO",
                file_id=fileId,
                image_index=imageIndex,
                image_path=image_path,
                error=str(e)
            )
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "Image not found"}
            )

        # Return image with appropriate content-type
        return Response(
            content=image_data,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=86400",  # Cache for 1 day
                "Content-Disposition": f'inline; filename="image_{imageIndex}.png"'
            }
        )

    except ValueError as e:
        logger.warning(
            "Invalid file ID format for image",
            file_id=fileId,
            image_index=imageIndex,
            user_id=user_id,
            error=str(e),
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Invalid file ID format"}
        )
    except Exception as e:
        logger.error(
            "Failed to get image",
            file_id=fileId,
            image_index=imageIndex,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to retrieve image", "details": str(e)}
        )
    finally:
        # Don't close - pg_service is a singleton shared across requests
        pass