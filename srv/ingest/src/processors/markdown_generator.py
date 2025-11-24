"""
Markdown Generator Module

Converts extracted text to clean, formatted markdown with proper structure.
Handles headings, tables, lists, and image references.
"""

import re
from typing import List, Optional, Tuple
import structlog

logger = structlog.get_logger()


class MarkdownGenerator:
    """
    Generates clean markdown from extracted text.
    Preserves document structure and formatting.
    """

    def __init__(self):
        self.image_references: List[str] = []

    def generate(
        self, 
        text: str, 
        extraction_method: str = "simple",
        images: Optional[List[dict]] = None
    ) -> Tuple[str, dict]:
        """
        Generate markdown from extracted text.

        Args:
            text: Extracted text content
            extraction_method: Method used for extraction (simple, marker)
            images: List of extracted images with metadata

        Returns:
            Tuple of (markdown_content, metadata)
            metadata includes: title, heading_count, image_count, etc.
        """
        try:
            # Store image references
            if images:
                self.image_references = [img.get('path', '') for img in images]

            # Different processing based on extraction method
            if extraction_method == "marker":
                markdown = self._process_marker_output(text)
            else:
                markdown = self._process_simple_text(text)

            # Insert image references
            if images:
                markdown = self._insert_image_references(markdown, images)

            # Extract metadata
            metadata = self._extract_metadata(markdown)

            logger.info(
                "Generated markdown",
                extraction_method=extraction_method,
                text_length=len(text),
                markdown_length=len(markdown),
                image_count=len(images) if images else 0,
                heading_count=metadata.get('heading_count', 0)
            )

            return markdown, metadata

        except Exception as e:
            logger.error("Failed to generate markdown", error=str(e), exc_info=True)
            raise

    def _process_marker_output(self, text: str) -> str:
        """
        Process text that was extracted using Marker.
        Marker already provides markdown-like formatting.
        """
        # Marker output is already in markdown format, but may need cleanup
        markdown = text

        # Clean up excessive whitespace
        markdown = re.sub(r'\n{3,}', '\n\n', markdown)

        # Ensure proper heading formatting
        markdown = self._normalize_headings(markdown)

        # Clean up table formatting
        markdown = self._normalize_tables(markdown)

        return markdown.strip()

    def _process_simple_text(self, text: str) -> str:
        """
        Process plain text extraction and infer structure.
        Attempts to identify headings, paragraphs, and lists.
        """
        lines = text.split('\n')
        markdown_lines = []

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                markdown_lines.append('')
                continue

            # Detect potential headings (ALL CAPS or title case, short lines)
            if self._is_potential_heading(line, lines, i):
                # Determine heading level based on context
                level = self._infer_heading_level(line)
                markdown_lines.append(f"{'#' * level} {line}")
            # Detect bullet points
            elif line.startswith(('•', '-', '*', '▪')) or re.match(r'^\d+\.', line):
                # Convert to markdown list
                cleaned = re.sub(r'^[•\-*▪]\s*', '- ', line)
                cleaned = re.sub(r'^\d+\.\s*', '1. ', cleaned)
                markdown_lines.append(cleaned)
            else:
                # Regular paragraph
                markdown_lines.append(line)

        markdown = '\n'.join(markdown_lines)
        
        # Clean up excessive whitespace
        markdown = re.sub(r'\n{3,}', '\n\n', markdown)

        return markdown.strip()

    def _is_potential_heading(self, line: str, all_lines: List[str], index: int) -> bool:
        """
        Determine if a line is likely a heading.
        """
        # Skip very long lines
        if len(line) > 100:
            return False

        # Check if line is all uppercase
        if line.isupper() and len(line.split()) <= 10:
            return True

        # Check if line is title case and short
        if line.istitle() and len(line.split()) <= 10:
            # Check if followed by regular text (not another heading)
            if index + 1 < len(all_lines):
                next_line = all_lines[index + 1].strip()
                if next_line and not next_line.isupper() and not next_line.istitle():
                    return True

        return False

    def _infer_heading_level(self, line: str) -> int:
        """
        Infer heading level based on line characteristics.
        """
        # Very short (1-2 words), all caps -> H1
        if line.isupper() and len(line.split()) <= 2:
            return 1
        # All caps -> H2
        elif line.isupper():
            return 2
        # Title case -> H3
        elif line.istitle():
            return 3
        else:
            return 2

    def _normalize_headings(self, markdown: str) -> str:
        """
        Ensure consistent heading formatting.
        """
        lines = []
        for line in markdown.split('\n'):
            # Fix headings without space after #
            line = re.sub(r'^(#{1,6})([^ #])', r'\1 \2', line)
            lines.append(line)
        return '\n'.join(lines)

    def _normalize_tables(self, markdown: str) -> str:
        """
        Ensure markdown tables are properly formatted.
        """
        # Tables in markdown use | separators
        # This is a placeholder for more sophisticated table normalization
        return markdown

    def _insert_image_references(self, markdown: str, images: List[dict]) -> str:
        """
        Insert image references into markdown at appropriate positions.
        """
        if not images:
            return markdown

        # For now, append images at the end or insert them at logical breaks
        # More sophisticated placement can be added later
        image_section = "\n\n## Images\n\n"
        for i, img in enumerate(images):
            image_path = img.get('path', f'image_{i}.png')
            caption = img.get('caption', f'Image {i+1}')
            image_section += f"![{caption}]({image_path})\n\n"

        return markdown + image_section

    def _extract_metadata(self, markdown: str) -> dict:
        """
        Extract metadata from generated markdown.
        """
        metadata = {}

        # Count headings by level
        headings = re.findall(r'^(#{1,6})\s+(.+)$', markdown, re.MULTILINE)
        metadata['heading_count'] = len(headings)
        metadata['has_headings'] = len(headings) > 0

        # Extract title (first H1)
        h1_match = re.search(r'^#\s+(.+)$', markdown, re.MULTILINE)
        if h1_match:
            metadata['title'] = h1_match.group(1).strip()
        else:
            # Try to get title from first line
            first_line = markdown.split('\n')[0] if markdown else ''
            metadata['title'] = first_line[:100] if first_line else 'Untitled'

        # Count images
        image_refs = re.findall(r'!\[([^\]]*)\]\(([^\)]+)\)', markdown)
        metadata['image_references'] = len(image_refs)

        # Count tables
        table_rows = re.findall(r'^\|.+\|$', markdown, re.MULTILINE)
        metadata['table_rows'] = len(table_rows)
        metadata['has_tables'] = len(table_rows) > 0

        # Count code blocks
        code_blocks = re.findall(r'```[\s\S]*?```', markdown)
        metadata['code_blocks'] = len(code_blocks)

        # Word count
        words = len(re.findall(r'\b\w+\b', markdown))
        metadata['word_count'] = words

        return metadata

    def sanitize_markdown(self, markdown: str) -> str:
        """
        Remove or escape potentially dangerous content.
        """
        # Remove HTML script tags
        markdown = re.sub(r'<script[\s\S]*?</script>', '', markdown, flags=re.IGNORECASE)
        
        # Remove HTML on* event handlers
        markdown = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', '', markdown, flags=re.IGNORECASE)
        
        # Remove iframe tags
        markdown = re.sub(r'<iframe[\s\S]*?</iframe>', '', markdown, flags=re.IGNORECASE)

        return markdown


