"""
API routes for markdown and HTML rendering.

Provides endpoints to retrieve markdown content and rendered HTML.
"""

from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import JSONResponse, Response
import uuid
import structlog

from shared.config import Config
from services.postgres_service import PostgresService
from api.services.minio_service import MinIOService
from processors.html_renderer import HTMLRenderer

logger = structlog.get_logger()

router = APIRouter()


def _get_file_metadata(postgres_service, file_uuid, user_uuid, fields):
    """
    Helper to get file metadata using synchronous PostgreSQL.
    
    Args:
        postgres_service: PostgresService instance
        file_uuid: File UUID
        user_uuid: User UUID
        fields: List of field names to select
        
    Returns:
        Dict with file data or None if not found
    """
    conn = postgres_service.pool.getconn()
    try:
        with conn.cursor() as cur:
            field_list = ", ".join(fields)
            cur.execute(
                f"""SELECT {field_list}
                   FROM ingestion_files 
                   WHERE file_id = %s AND user_id = %s""",
                (file_uuid, user_uuid)
            )
            row = cur.fetchone()
            
            if not row:
                return None
            
            # Convert tuple to dict
            return dict(zip(fields, row))
    finally:
        postgres_service.pool.putconn(conn)


@router.get("/{fileId}/markdown")
async def get_markdown(fileId: str, request: Request):
    """
    Get markdown content for a file.

    Returns:
        JSON with markdown content and metadata
    """
    user_id = request.state.user_id

    config = Config().to_dict()
    postgres_service = PostgresService(config)
    minio_service = MinIOService(config)
    postgres_service.connect()

    try:
        # Validate fileId is a valid UUID
        file_uuid = uuid.UUID(fileId)
        user_uuid = uuid.UUID(user_id)

        # Get file metadata
        file_data = _get_file_metadata(
            postgres_service, 
            file_uuid, 
            user_uuid,
            ["file_id", "user_id", "original_filename", "markdown_path", "has_markdown", "image_count"]
        )

        if not file_data:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "File not found or unauthorized"}
            )

        if not file_data["has_markdown"] or not file_data["markdown_path"]:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "Markdown not available for this file"}
            )

        # Fetch markdown from MinIO
        markdown_content = minio_service.get_file_content(file_data["markdown_path"])

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
        postgres_service.disconnect()


@router.get("/{fileId}/html")
async def get_html(fileId: str, request: Request):
    """
    Get rendered HTML content for a file with table of contents.

    Returns:
        JSON with HTML content and TOC
    """
    user_id = request.state.user_id

    config = Config().to_dict()
    postgres_service = PostgresService(config)
    minio_service = MinIOService(config)
    postgres_service.connect()

    try:
        # Validate fileId is a valid UUID
        file_uuid = uuid.UUID(fileId)
        user_uuid = uuid.UUID(user_id)

        # Get file metadata
        file_data = _get_file_metadata(
            postgres_service,
            file_uuid,
            user_uuid,
            ["file_id", "user_id", "original_filename", "markdown_path", "has_markdown", "image_count"]
        )

        if not file_data:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "File not found or unauthorized"}
            )

        if not file_data["has_markdown"] or not file_data["markdown_path"]:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "Markdown not available for this file"}
            )

        # Fetch markdown from MinIO
        markdown_content = minio_service.get_file_content(file_data["markdown_path"])

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
        postgres_service.disconnect()


@router.get("/{fileId}/images/{imageIndex}")
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
    postgres_service = PostgresService(config)
    minio_service = MinIOService(config)
    postgres_service.connect()

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
        file_data = _get_file_metadata(
            postgres_service,
            file_uuid,
            user_uuid,
            ["file_id", "user_id", "images_path", "image_count"]
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
        postgres_service.disconnect()
