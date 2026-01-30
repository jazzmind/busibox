"""
Image Extractor Module

Extracts images from PDF and DOCX documents.
Converts images to standard format (PNG) and provides metadata.
"""

import os
from pathlib import Path
from typing import List, Optional, Tuple
from PIL import Image
import fitz  # PyMuPDF
import structlog
from io import BytesIO

logger = structlog.get_logger()


class ImageExtractor:
    """
    Extracts images from documents.
    Supports PDF and DOCX formats.
    """

    def __init__(self, max_image_size: int = 5_000_000, target_format: str = "PNG"):
        """
        Initialize image extractor.

        Args:
            max_image_size: Maximum size in bytes for an extracted image (default: 5MB)
            target_format: Target image format for conversion (default: PNG)
        """
        self.max_image_size = max_image_size
        self.target_format = target_format.upper()

    def extract_from_pdf(self, pdf_path: str) -> Tuple[List[dict], List[bytes]]:
        """
        Extract all images from a PDF file.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            Tuple of (metadata_list, image_data_list)
            Each metadata dict contains: index, page, width, height, format, size
            Each image_data is bytes in target_format
        """
        try:
            doc = fitz.open(pdf_path)
            images_metadata = []
            images_data = []
            image_index = 0

            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)

                for img_index, img in enumerate(image_list):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]

                        # Load image with PIL for processing
                        pil_image = Image.open(BytesIO(image_bytes))

                        # Convert to target format
                        output = BytesIO()
                        pil_image.save(output, format=self.target_format)
                        converted_bytes = output.getvalue()

                        # Check size limit
                        if len(converted_bytes) > self.max_image_size:
                            logger.warning(
                                "Image exceeds size limit, skipping",
                                image_index=image_index,
                                page=page_num + 1,
                                size=len(converted_bytes),
                                max_size=self.max_image_size
                            )
                            continue

                        # Store metadata
                        metadata = {
                            "index": image_index,
                            "page": page_num + 1,
                            "width": pil_image.width,
                            "height": pil_image.height,
                            "format": self.target_format.lower(),
                            "size": len(converted_bytes),
                            "original_format": image_ext
                        }

                        images_metadata.append(metadata)
                        images_data.append(converted_bytes)
                        image_index += 1

                        logger.debug(
                            "Extracted image from PDF",
                            image_index=image_index,
                            page=page_num + 1,
                            size=len(converted_bytes)
                        )

                    except Exception as e:
                        logger.warning(
                            "Failed to extract image from page",
                            page=page_num + 1,
                            img_index=img_index,
                            error=str(e)
                        )
                        continue

            doc.close()

            logger.info(
                "PDF image extraction complete",
                pdf_path=pdf_path,
                total_images=len(images_data)
            )

            return images_metadata, images_data

        except Exception as e:
            logger.error("Failed to extract images from PDF", pdf_path=pdf_path, error=str(e), exc_info=True)
            raise

    def extract_from_docx(self, docx_path: str) -> Tuple[List[dict], List[bytes]]:
        """
        Extract all images from a DOCX file.

        Args:
            docx_path: Path to the DOCX file

        Returns:
            Tuple of (metadata_list, image_data_list)
        """
        try:
            from docx import Document
            from docx.opc.constants import RELATIONSHIP_TYPE as RT

            doc = Document(docx_path)
            images_metadata = []
            images_data = []
            image_index = 0

            # Extract images from document relationships
            for rel in doc.part.rels.values():
                if "image" in rel.target_ref:
                    try:
                        image_bytes = rel.target_part.blob

                        # Load with PIL
                        pil_image = Image.open(BytesIO(image_bytes))

                        # Convert to target format
                        output = BytesIO()
                        pil_image.save(output, format=self.target_format)
                        converted_bytes = output.getvalue()

                        # Check size limit
                        if len(converted_bytes) > self.max_image_size:
                            logger.warning(
                                "Image exceeds size limit, skipping",
                                image_index=image_index,
                                size=len(converted_bytes)
                            )
                            continue

                        metadata = {
                            "index": image_index,
                            "width": pil_image.width,
                            "height": pil_image.height,
                            "format": self.target_format.lower(),
                            "size": len(converted_bytes),
                            "original_format": pil_image.format
                        }

                        images_metadata.append(metadata)
                        images_data.append(converted_bytes)
                        image_index += 1

                        logger.debug(
                            "Extracted image from DOCX",
                            image_index=image_index,
                            size=len(converted_bytes)
                        )

                    except Exception as e:
                        logger.warning(
                            "Failed to extract image from DOCX",
                            image_index=image_index,
                            error=str(e)
                        )
                        continue

            logger.info(
                "DOCX image extraction complete",
                docx_path=docx_path,
                total_images=len(images_data)
            )

            return images_metadata, images_data

        except Exception as e:
            logger.error("Failed to extract images from DOCX", docx_path=docx_path, error=str(e), exc_info=True)
            raise

    def extract(self, file_path: str, mime_type: Optional[str] = None) -> Tuple[List[dict], List[bytes]]:
        """
        Extract images from document (auto-detect format).

        Args:
            file_path: Path to the document
            mime_type: Optional MIME type hint

        Returns:
            Tuple of (metadata_list, image_data_list)
        """
        # Determine file type
        if mime_type:
            if "pdf" in mime_type.lower():
                return self.extract_from_pdf(file_path)
            elif "wordprocessingml" in mime_type.lower() or "msword" in mime_type.lower():
                return self.extract_from_docx(file_path)

        # Fallback to extension
        ext = Path(file_path).suffix.lower()
        if ext == ".pdf":
            return self.extract_from_pdf(file_path)
        elif ext in [".docx", ".doc"]:
            return self.extract_from_docx(file_path)
        else:
            logger.warning("Unsupported file type for image extraction", file_path=file_path, mime_type=mime_type)
            return [], []

    def get_image_filename(self, index: int, format: str = None) -> str:
        """
        Get standardized filename for an extracted image.

        Args:
            index: Image index (0-based)
            format: Image format (defaults to target_format)

        Returns:
            Filename like "image_0.png"
        """
        fmt = (format or self.target_format).lower()
        return f"image_{index}.{fmt}"


