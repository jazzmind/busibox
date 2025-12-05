"""
Text extraction endpoint for remote Marker processing.

This endpoint allows other ingest services (e.g., test environment) to use
this service's Marker installation for PDF text extraction without running
Marker locally.

Usage:
    POST /extract
    - file: PDF file (multipart/form-data)
    
Returns:
    - text: Extracted markdown text
    - page_count: Number of pages
    - extraction_method: Method used (marker, pdfplumber)
"""

import os
import tempfile
from pathlib import Path

import structlog
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from shared.config import Config
from processors.text_extractor import TextExtractor

logger = structlog.get_logger()
router = APIRouter()


@router.post("/extract")
async def extract_text(
    file: UploadFile = File(...),
):
    """
    Extract text from a document using Marker.
    
    This endpoint is designed for remote extraction - allowing test environments
    to use production Marker without running it locally.
    
    Args:
        file: Document file (PDF supported, others pass through)
    
    Returns:
        JSON with:
        - text: Extracted text/markdown
        - page_count: Number of pages
        - extraction_method: Method used
    """
    # Only support PDF for now (Marker's primary use case)
    if file.content_type != "application/pdf":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": f"Only PDF files supported, got {file.content_type}"}
        )
    
    logger.info(
        "Remote extraction request",
        filename=file.filename,
        content_type=file.content_type,
        size=file.size,
    )
    
    # Create temp file for extraction
    temp_dir = "/tmp/ingest/extract"
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # Save uploaded file
        suffix = Path(file.filename).suffix if file.filename else ".pdf"
        with tempfile.NamedTemporaryFile(
            dir=temp_dir,
            suffix=suffix,
            delete=False,
        ) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_path = temp_file.name
        
        logger.debug("Saved temp file", path=temp_path, size=len(content))
        
        # Initialize extractor with Marker enabled, no remote URL (we ARE the remote)
        config = Config().to_dict()
        config["marker_enabled"] = True
        config["marker_service_url"] = None  # Don't recurse!
        
        extractor = TextExtractor(config)
        
        # Extract text
        result = extractor.extract(temp_path, file.content_type)
        
        logger.info(
            "Remote extraction complete",
            filename=file.filename,
            text_length=len(result.text),
            page_count=result.page_count,
            extraction_method=result.metadata.get("extraction_method"),
        )
        
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "text": result.text,
                "markdown": result.markdown,
                "page_count": result.page_count,
                "extraction_method": result.metadata.get("extraction_method", "unknown"),
            }
        )
        
    except Exception as e:
        logger.error(
            "Remote extraction failed",
            filename=file.filename,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(e)}
        )
        
    finally:
        # Clean up temp file
        try:
            if 'temp_path' in locals():
                os.unlink(temp_path)
        except Exception:
            pass


