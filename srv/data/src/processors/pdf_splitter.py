"""
PDF Splitter Utility

Splits large PDFs into smaller chunks (default: 5 pages) for processing.
This helps prevent memory issues and timeouts when processing very large PDFs.

Uses PyPDF2 for splitting since it's lightweight and already in requirements.
"""

import os
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import structlog

logger = structlog.get_logger()

# Default number of pages per split chunk
DEFAULT_PAGES_PER_SPLIT = 5


class PDFSplitter:
    """Split large PDFs into smaller chunks for processing."""
    
    def __init__(
        self,
        pages_per_split: int = DEFAULT_PAGES_PER_SPLIT,
        temp_dir: Optional[str] = None,
    ):
        """
        Initialize PDF splitter.
        
        Args:
            pages_per_split: Number of pages per split chunk (default: 5)
            temp_dir: Directory for temporary split files
        """
        self.pages_per_split = pages_per_split
        self.temp_dir = temp_dir or tempfile.gettempdir()
        os.makedirs(self.temp_dir, exist_ok=True)
    
    def get_page_count(self, pdf_path: str) -> int:
        """
        Get the number of pages in a PDF.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Number of pages
        """
        try:
            # Try pypdf first (modern library), fall back to PyPDF2 (deprecated)
            try:
                from pypdf import PdfReader
            except ImportError:
                from PyPDF2 import PdfReader
            reader = PdfReader(pdf_path)
            return len(reader.pages)
        except Exception as e:
            logger.error("Failed to get PDF page count", pdf_path=pdf_path, error=str(e))
            raise
    
    def needs_splitting(self, pdf_path: str) -> bool:
        """
        Check if a PDF needs to be split (more pages than pages_per_split).
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            True if PDF should be split, False otherwise
        """
        try:
            page_count = self.get_page_count(pdf_path)
            return page_count > self.pages_per_split
        except Exception:
            # If we can't read the PDF, don't split
            return False
    
    def split(self, pdf_path: str) -> List[Tuple[str, int, int]]:
        """
        Split a PDF into chunks of pages_per_split pages each.
        
        Args:
            pdf_path: Path to PDF file to split
            
        Returns:
            List of tuples: (split_file_path, start_page, end_page)
            Page numbers are 1-indexed for clarity in logging.
            
        Note:
            Caller is responsible for cleaning up the split files after use.
        """
        # Try pypdf first (modern library), fall back to PyPDF2 (deprecated)
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError:
            from PyPDF2 import PdfReader, PdfWriter
        
        logger.info(
            "Splitting PDF",
            pdf_path=pdf_path,
            pages_per_split=self.pages_per_split,
        )
        
        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)
        
        if total_pages <= self.pages_per_split:
            # No splitting needed - return original file
            logger.debug("PDF does not need splitting", total_pages=total_pages)
            return [(pdf_path, 1, total_pages)]
        
        splits = []
        base_name = Path(pdf_path).stem
        
        # Calculate number of splits
        num_splits = (total_pages + self.pages_per_split - 1) // self.pages_per_split
        
        for i in range(num_splits):
            start_page = i * self.pages_per_split  # 0-indexed
            end_page = min(start_page + self.pages_per_split, total_pages)  # exclusive
            
            # Create a new PDF with just these pages
            writer = PdfWriter()
            for page_idx in range(start_page, end_page):
                writer.add_page(reader.pages[page_idx])
            
            # Write to temp file
            split_filename = f"{base_name}_split_{i+1:03d}_pages_{start_page+1}-{end_page}.pdf"
            split_path = os.path.join(self.temp_dir, split_filename)
            
            with open(split_path, "wb") as f:
                writer.write(f)
            
            # Store 1-indexed page numbers for clarity
            splits.append((split_path, start_page + 1, end_page))
            
            logger.debug(
                "Created PDF split",
                split_path=split_path,
                pages=f"{start_page + 1}-{end_page}",
                page_count=end_page - start_page,
            )
        
        logger.info(
            "PDF split complete",
            pdf_path=pdf_path,
            total_pages=total_pages,
            num_splits=len(splits),
            pages_per_split=self.pages_per_split,
        )
        
        return splits
    
    def cleanup_splits(self, splits: List[Tuple[str, int, int]], original_path: str):
        """
        Clean up temporary split files.
        
        Args:
            splits: List of (split_file_path, start_page, end_page) tuples
            original_path: Original PDF path (won't be deleted)
        """
        for split_path, _, _ in splits:
            # Don't delete the original file
            if split_path == original_path:
                continue
            
            try:
                if os.path.exists(split_path):
                    os.remove(split_path)
                    logger.debug("Removed split file", path=split_path)
            except Exception as e:
                logger.warning(
                    "Failed to remove split file",
                    path=split_path,
                    error=str(e),
                )


class PDFSplitContext:
    """
    Context manager for PDF splitting that handles cleanup automatically.
    
    Usage:
        with PDFSplitContext(pdf_path, pages_per_split=5) as splits:
            for split_path, start_page, end_page in splits:
                # Process each split
                pass
        # Cleanup happens automatically
    """
    
    def __init__(
        self,
        pdf_path: str,
        pages_per_split: int = DEFAULT_PAGES_PER_SPLIT,
        temp_dir: Optional[str] = None,
    ):
        self.pdf_path = pdf_path
        self.splitter = PDFSplitter(pages_per_split=pages_per_split, temp_dir=temp_dir)
        self.splits: List[Tuple[str, int, int]] = []
    
    def __enter__(self) -> List[Tuple[str, int, int]]:
        self.splits = self.splitter.split(self.pdf_path)
        return self.splits
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.splitter.cleanup_splits(self.splits, self.pdf_path)
        return False  # Don't suppress exceptions

