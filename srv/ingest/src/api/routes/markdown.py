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
    await postgres_service.connect()

    try:
        # Validate fileId is a valid UUID
        file_uuid = uuid.UUID(fileId)

        # Get file metadata
        async with postgres_service.pool.acquire() as conn:
            file_row = await conn.fetchrow(
                """SELECT file_id, user_id, original_filename, markdown_path, 
                          has_markdown, image_count
                   FROM ingestion_files 
                   WHERE file_id = $1 AND user_id = $2""",
                file_uuid,
                uuid.UUID(user_id),
            )

            if not file_row:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "File not found or unauthorized"}
                )

            if not file_row["has_markdown"] or not file_row["markdown_path"]:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "Markdown not available for this file"}
                )

            # Fetch markdown from MinIO
            markdown_content = minio_service.get_file_content(file_row["markdown_path"])

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "fileId": str(file_row["file_id"]),
                    "filename": file_row["original_filename"],
                    "markdown": markdown_content,
                    "hasImages": file_row["image_count"] > 0,
                    "imageCount": file_row["image_count"]
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
        await postgres_service.disconnect()


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
    await postgres_service.connect()

    try:
        # Validate fileId is a valid UUID
        file_uuid = uuid.UUID(fileId)

        # Get file metadata
        async with postgres_service.pool.acquire() as conn:
            file_row = await conn.fetchrow(
                """SELECT file_id, user_id, original_filename, markdown_path, 
                          has_markdown, image_count
                   FROM ingestion_files 
                   WHERE file_id = $1 AND user_id = $2""",
                file_uuid,
                uuid.UUID(user_id),
            )

            if not file_row:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "File not found or unauthorized"}
                )

            if not file_row["has_markdown"] or not file_row["markdown_path"]:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "Markdown not available for this file"}
                )

            # Fetch markdown from MinIO
            markdown_content = minio_service.get_file_content(file_row["markdown_path"])

            # Render to HTML
            renderer = HTMLRenderer()
            html_content, toc = renderer.render(markdown_content, file_id=fileId)

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "fileId": str(file_row["file_id"]),
                    "filename": file_row["original_filename"],
                    "html": html_content,
                    "toc": toc,
                    "hasImages": file_row["image_count"] > 0,
                    "imageCount": file_row["image_count"]
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
        await postgres_service.disconnect()


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
    await postgres_service.connect()

    try:
        # Validate fileId is a valid UUID
        file_uuid = uuid.UUID(fileId)

        # Validate imageIndex
        if imageIndex < 0:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "Invalid image index"}
            )

        # Get file metadata
        async with postgres_service.pool.acquire() as conn:
            file_row = await conn.fetchrow(
                """SELECT file_id, user_id, images_path, image_count
                   FROM ingestion_files 
                   WHERE file_id = $1 AND user_id = $2""",
                file_uuid,
                uuid.UUID(user_id),
            )

            if not file_row:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "File not found or unauthorized"}
                )

            if not file_row["images_path"] or file_row["image_count"] == 0:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": "No images available for this file"}
                )

            if imageIndex >= file_row["image_count"]:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"error": f"Image index {imageIndex} not found"}
                )

            # Construct image path
            image_path = f"{file_row['images_path']}/image_{imageIndex}.png"

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
        await postgres_service.disconnect()


