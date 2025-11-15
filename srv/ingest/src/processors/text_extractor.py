"""
Text extraction from various file formats.

Supports:
- PDF: Marker (primary), TATR (tables), pdfplumber (fallback)
- DOCX: python-docx
- TXT, HTML, Markdown, CSV, JSON: Direct parsing
- Page image extraction for ColPali (PDFs only)
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pdfplumber
import structlog
from docx import Document

logger = structlog.get_logger()


class ExtractionResult:
    """Result of text extraction."""
    
    def __init__(
        self,
        text: str,
        markdown: Optional[str] = None,
        page_images: Optional[List[str]] = None,
        page_count: int = 0,
        tables: Optional[List[Dict]] = None,
        metadata: Optional[Dict] = None,
    ):
        self.text = text
        self.markdown = markdown
        self.page_images = page_images or []
        self.page_count = page_count
        self.tables = tables or []
        self.metadata = metadata or {}


class TextExtractor:
    """Extract text from various file formats."""
    
    def __init__(self, config: dict):
        """Initialize text extractor."""
        self.config = config
        self.temp_dir = config.get("temp_dir", "/tmp/ingest")
        os.makedirs(self.temp_dir, exist_ok=True)
    
    def extract(self, file_path: str, mime_type: str) -> ExtractionResult:
        """
        Extract text from file.
        
        Args:
            file_path: Path to file
            mime_type: MIME type of file
            
        Returns:
            ExtractionResult with text, markdown, page images, etc.
        """
        if mime_type == "application/pdf":
            return self._extract_pdf(file_path)
        elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return self._extract_docx(file_path)
        elif mime_type == "text/plain":
            return self._extract_txt(file_path)
        elif mime_type == "text/html":
            return self._extract_html(file_path)
        elif mime_type == "text/markdown":
            return self._extract_markdown(file_path)
        elif mime_type == "text/csv":
            return self._extract_csv(file_path)
        elif mime_type == "application/json":
            return self._extract_json(file_path)
        else:
            raise ValueError(f"Unsupported MIME type: {mime_type}")
    
    def _extract_pdf(self, file_path: str) -> ExtractionResult:
        """Extract text from PDF using Marker, TATR, and page images."""
        page_images = []
        markdown_text = None
        text_content = ""
        tables = []
        page_count = 0
        
        try:
            # Try Marker first (best quality)
            try:
                logger.debug("Attempting to import marker.convert")
                from marker.convert import convert_single_pdf
                
                logger.info("Using Marker for PDF extraction", file_path=file_path)
                markdown_text, images, metadata = convert_single_pdf(file_path)
                text_content = markdown_text
                page_count = len(images) if images else 0
                
                # Extract page images for ColPali
                page_images = self._extract_pdf_page_images(file_path)
                page_count = len(page_images)
                
            except ImportError as e:
                logger.warning(
                    "Marker not available, falling back to pdfplumber",
                    error=str(e),
                    error_type=type(e).__name__,
                    import_error_details=getattr(e, 'name', 'unknown'),
                )
                markdown_text = None
            except Exception as e:
                logger.error(
                    "Marker import succeeded but execution failed",
                    error=str(e),
                    error_type=type(e).__name__,
                    exc_info=True,
                )
                markdown_text = None
            
            # Extract tables with TATR if available
            try:
                from tatr import TableTransformer
                
                logger.info("Extracting tables with TATR", file_path=file_path)
                table_transformer = TableTransformer()
                tables = table_transformer.extract_tables(file_path)
                
                # Add table text to content
                for table in tables:
                    if isinstance(table, dict) and "text" in table:
                        text_content += "\n\n" + table["text"]
            
            except ImportError:
                logger.debug("TATR not available, skipping table extraction")
            
            # Fallback to pdfplumber if Marker failed or for simple PDFs
            if not text_content or page_count == 0:
                logger.info("Using pdfplumber fallback", file_path=file_path)
                with pdfplumber.open(file_path) as pdf:
                    page_count = len(pdf.pages)
                    text_parts = []
                    
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text_parts.append(page_text)
                    
                    text_content = "\n".join(text_parts)
                    
                    # Extract page images if not already done
                    if not page_images:
                        page_images = self._extract_pdf_page_images(file_path)
            
            # Detect scanned PDFs (no extractable text)
            if not text_content.strip() and page_count > 0:
                logger.warning("No text extracted, PDF may be scanned - OCR required", file_path=file_path)
                # Trigger OCR processing (implemented separately)
                text_content = self._ocr_pdf(file_path)
            
            return ExtractionResult(
                text=text_content,
                markdown=markdown_text,
                page_images=page_images,
                page_count=page_count,
                tables=tables,
                metadata={"extraction_method": "marker" if markdown_text else "pdfplumber"},
            )
        
        except Exception as e:
            logger.error("PDF extraction failed", file_path=file_path, error=str(e), exc_info=True)
            raise
    
    def _extract_pdf_page_images(self, file_path: str) -> List[str]:
        """Extract page images from PDF for ColPali."""
        try:
            from pdf2image import convert_from_path
            
            logger.info("Extracting PDF page images", file_path=file_path)
            images = convert_from_path(file_path, dpi=150)
            
            # Save images to temp directory
            page_images = []
            base_name = Path(file_path).stem
            
            for i, image in enumerate(images):
                image_path = os.path.join(self.temp_dir, f"{base_name}_page_{i+1:03d}.png")
                image.save(image_path, "PNG")
                page_images.append(image_path)
            
            logger.info("Extracted page images", count=len(page_images), file_path=file_path)
            return page_images
        
        except ImportError:
            logger.warning("pdf2image not available, skipping page image extraction")
            return []
        except Exception as e:
            logger.error("Page image extraction failed", file_path=file_path, error=str(e))
            return []
    
    def _ocr_pdf(self, file_path: str) -> str:
        """Perform OCR on scanned PDF."""
        try:
            from pdf2image import convert_from_path
            import pytesseract
            
            logger.info("Performing OCR on scanned PDF", file_path=file_path)
            images = convert_from_path(file_path, dpi=300)
            
            text_parts = []
            for image in images:
                text = pytesseract.image_to_string(image)
                text_parts.append(text)
            
            return "\n".join(text_parts)
        
        except ImportError:
            logger.warning("OCR dependencies not available (pdf2image, pytesseract)")
            return ""
        except Exception as e:
            logger.error("OCR failed", file_path=file_path, error=str(e))
            return ""
    
    def _extract_docx(self, file_path: str) -> ExtractionResult:
        """Extract text from DOCX file."""
        try:
            doc = Document(file_path)
            text_parts = []
            
            for paragraph in doc.paragraphs:
                if paragraph.text.strip():
                    text_parts.append(paragraph.text)
            
            # Extract tables
            tables = []
            for table in doc.tables:
                table_data = []
                for row in table.rows:
                    row_data = [cell.text for cell in row.cells]
                    table_data.append(row_data)
                tables.append({"data": table_data})
            
            text_content = "\n".join(text_parts)
            
            return ExtractionResult(
                text=text_content,
                page_count=len(doc.paragraphs) // 20,  # Estimate pages
                tables=tables,
                metadata={"extraction_method": "python-docx"},
            )
        
        except Exception as e:
            logger.error("DOCX extraction failed", file_path=file_path, error=str(e), exc_info=True)
            raise
    
    def _extract_txt(self, file_path: str) -> ExtractionResult:
        """Extract text from TXT file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            
            return ExtractionResult(
                text=text,
                page_count=len(text.split("\n")) // 50,  # Estimate pages
                metadata={"extraction_method": "direct_read"},
            )
        
        except Exception as e:
            logger.error("TXT extraction failed", file_path=file_path, error=str(e), exc_info=True)
            raise
    
    def _extract_html(self, file_path: str) -> ExtractionResult:
        """Extract text from HTML file."""
        try:
            from bs4 import BeautifulSoup
            
            with open(file_path, "r", encoding="utf-8") as f:
                html = f.read()
            
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator="\n", strip=True)
            
            return ExtractionResult(
                text=text,
                page_count=1,
                metadata={"extraction_method": "beautifulsoup"},
            )
        
        except ImportError:
            # Fallback: basic regex extraction
            import re
            with open(file_path, "r", encoding="utf-8") as f:
                html = f.read()
            text = re.sub(r"<[^>]+>", "", html)
            
            return ExtractionResult(
                text=text,
                page_count=1,
                metadata={"extraction_method": "regex_fallback"},
            )
    
    def _extract_markdown(self, file_path: str) -> ExtractionResult:
        """Extract text from Markdown file."""
        return self._extract_txt(file_path)  # Markdown is plain text
    
    def _extract_csv(self, file_path: str) -> ExtractionResult:
        """Extract text from CSV file."""
        try:
            import csv
            
            text_parts = []
            with open(file_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    text_parts.append(" | ".join(row))
            
            text = "\n".join(text_parts)
            
            return ExtractionResult(
                text=text,
                page_count=1,
                metadata={"extraction_method": "csv"},
            )
        
        except Exception as e:
            logger.error("CSV extraction failed", file_path=file_path, error=str(e), exc_info=True)
            raise
    
    def _extract_json(self, file_path: str) -> ExtractionResult:
        """Extract text from JSON file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Convert JSON to readable text
            text = json.dumps(data, indent=2)
            
            return ExtractionResult(
                text=text,
                page_count=1,
                metadata={"extraction_method": "json"},
            )
        
        except Exception as e:
            logger.error("JSON extraction failed", file_path=file_path, error=str(e), exc_info=True)
            raise
