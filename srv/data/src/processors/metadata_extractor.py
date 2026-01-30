"""
Metadata extraction from documents.

Extracts title, author, date, keywords from various file formats.
"""

import re
from datetime import datetime
from typing import Dict, List, Optional

import structlog
from docx import Document

logger = structlog.get_logger()


class MetadataExtractor:
    """Extract metadata from documents."""
    
    def __init__(self, config: dict):
        """Initialize metadata extractor."""
        self.config = config
    
    def extract(
        self,
        file_path: str,
        mime_type: str,
        text: str,
    ) -> Dict:
        """
        Extract metadata from document.
        
        Args:
            file_path: Path to file
            mime_type: MIME type
            text: Extracted text content
        
        Returns:
            Dictionary with extracted metadata:
            - title: Extracted title
            - author: Extracted author
            - date: Extracted date
            - keywords: Extracted keywords
        """
        metadata = {
            "title": None,
            "author": None,
            "date": None,
            "keywords": [],
        }
        
        if mime_type == "application/pdf":
            metadata.update(self._extract_pdf_metadata(file_path, text))
        elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            metadata.update(self._extract_docx_metadata(file_path, text))
        else:
            # Use heuristics for other formats
            metadata.update(self._extract_heuristic_metadata(text))
        
        return metadata
    
    def _extract_pdf_metadata(self, file_path: str, text: str) -> Dict:
        """Extract metadata from PDF."""
        metadata = {}
        
        try:
            import PyPDF2
            
            with open(file_path, "rb") as f:
                pdf_reader = PyPDF2.PdfReader(f)
                pdf_metadata = pdf_reader.metadata
            
            if pdf_metadata:
                metadata["title"] = pdf_metadata.get("/Title")
                metadata["author"] = pdf_metadata.get("/Author")
                
                # Extract date
                date_str = pdf_metadata.get("/CreationDate") or pdf_metadata.get("/ModDate")
                if date_str:
                    metadata["date"] = self._parse_pdf_date(date_str)
        except Exception as e:
            logger.debug("PDF metadata extraction failed", error=str(e))
        
        # Fallback to heuristics if embedded metadata not available
        if not metadata.get("title"):
            heuristic = self._extract_heuristic_metadata(text)
            metadata.update(heuristic)
        
        return metadata
    
    def _extract_docx_metadata(self, file_path: str, text: str) -> Dict:
        """Extract metadata from DOCX."""
        metadata = {}
        
        try:
            doc = Document(file_path)
            core_props = doc.core_properties
            
            if core_props.title:
                metadata["title"] = core_props.title
            if core_props.author:
                metadata["author"] = core_props.author
            if core_props.created:
                metadata["date"] = core_props.created.date()
        except Exception as e:
            logger.debug("DOCX metadata extraction failed", error=str(e))
        
        # Fallback to heuristics
        if not metadata.get("title"):
            heuristic = self._extract_heuristic_metadata(text)
            metadata.update(heuristic)
        
        return metadata
    
    def _extract_heuristic_metadata(self, text: str) -> Dict:
        """Extract metadata using heuristics."""
        metadata = {}
        
        # Extract title (first heading or first line)
        lines = text.split("\n")
        for line in lines[:20]:  # Check first 20 lines
            line_stripped = line.strip()
            if len(line_stripped) > 10 and len(line_stripped) < 200:
                # Check if it looks like a title
                if not line_stripped.endswith(".") and line_stripped[0].isupper():
                    metadata["title"] = line_stripped
                    break
        
        # Extract author (look for "by" or "author:" patterns)
        author_patterns = [
            r"author[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            r"by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            r"written\s+by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        ]
        
        for pattern in author_patterns:
            match = re.search(pattern, text[:500], re.IGNORECASE)
            if match:
                metadata["author"] = match.group(1)
                break
        
        # Extract date
        date_patterns = [
            r"\b(\d{4}-\d{2}-\d{2})\b",  # YYYY-MM-DD
            r"\b(\d{1,2}/\d{1,2}/\d{4})\b",  # MM/DD/YYYY
            r"\b(\w+\s+\d{1,2},?\s+\d{4})\b",  # Month DD, YYYY
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, text[:500])
            if match:
                try:
                    date_str = match.group(1)
                    metadata["date"] = self._parse_date(date_str)
                    break
                except:
                    pass
        
        # Extract keywords (simple frequency-based)
        keywords = self._extract_keywords(text)
        metadata["keywords"] = keywords
        
        return metadata
    
    def _extract_keywords(self, text: str, top_n: int = 10) -> List[str]:
        """Extract keywords using simple frequency analysis."""
        # Remove common stop words
        stop_words = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
            "been", "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "can", "this",
            "that", "these", "those", "it", "its", "they", "them", "their",
        }
        
        # Extract words (simple tokenization)
        words = re.findall(r"\b[a-z]{3,}\b", text.lower())
        
        # Filter stop words and count
        word_counts = {}
        for word in words:
            if word not in stop_words:
                word_counts[word] = word_counts.get(word, 0) + 1
        
        # Get top keywords
        sorted_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
        return [word for word, count in sorted_words[:top_n]]
    
    def _parse_pdf_date(self, date_str: str) -> Optional[datetime]:
        """Parse PDF date format (D:YYYYMMDDHHmmSS)."""
        try:
            # PDF dates: D:YYYYMMDDHHmmSS
            if date_str.startswith("D:"):
                date_str = date_str[2:]
            
            if len(date_str) >= 8:
                year = int(date_str[0:4])
                month = int(date_str[4:6])
                day = int(date_str[6:8])
                return datetime(year, month, day).date()
        except:
            pass
        return None
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse various date formats."""
        formats = [
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%d/%m/%Y",
            "%B %d, %Y",
            "%b %d, %Y",
            "%d %B %Y",
            "%d %b %Y",
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).date()
            except:
                continue
        
        return None

