"""
HTML Renderer Module

Converts markdown to clean, safe HTML with table of contents generation.
"""

import re
from typing import List, Dict, Tuple
import markdown
from markdown.extensions import tables, fenced_code, codehilite
import bleach
import structlog

logger = structlog.get_logger()


class HTMLRenderer:
    """
    Renders markdown to HTML with TOC generation and sanitization.
    """

    def __init__(self, base_image_url: str = ""):
        """
        Initialize HTML renderer.

        Args:
            base_image_url: Base URL for image references (e.g., "/api/files/{fileId}/images/")
        """
        self.base_image_url = base_image_url
        
        # Allowed HTML tags and attributes for sanitization
        self.allowed_tags = [
            'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'p', 'br', 'span', 'div',
            'strong', 'em', 'u', 'strike', 'code', 'pre',
            'ul', 'ol', 'li',
            'table', 'thead', 'tbody', 'tr', 'th', 'td',
            'a', 'img',
            'blockquote', 'hr'
        ]
        
        self.allowed_attributes = {
            '*': ['class', 'id'],
            'a': ['href', 'title', 'rel'],
            'img': ['src', 'alt', 'title', 'width', 'height'],
            'code': ['class'],  # For syntax highlighting
        }

    def render(self, markdown_content: str, file_id: str = None) -> Tuple[str, List[Dict]]:
        """
        Render markdown to HTML with TOC.

        Args:
            markdown_content: Markdown text to render
            file_id: Optional file ID for constructing image URLs

        Returns:
            Tuple of (html_content, toc_items)
            toc_items is a list of dicts with: level, title, id, children
        """
        try:
            # Extract and process headings for TOC before rendering
            toc = self._extract_toc(markdown_content)
            
            # Add IDs to headings
            markdown_with_ids = self._add_heading_ids(markdown_content)
            
            # Replace image references with proper URLs
            if file_id:
                markdown_with_ids = self._resolve_image_urls(markdown_with_ids, file_id)
            
            # Convert markdown to HTML
            md = markdown.Markdown(extensions=[
                'tables',
                'fenced_code',
                'codehilite',
                'nl2br',  # Convert newlines to <br>
            ])
            html = md.convert(markdown_with_ids)
            
            # Sanitize HTML
            html = self._sanitize_html(html)
            
            # Add responsive styling classes
            html = self._add_styling_classes(html)
            
            logger.info(
                "Rendered markdown to HTML",
                markdown_length=len(markdown_content),
                html_length=len(html),
                toc_items=len(toc)
            )
            
            return html, toc

        except Exception as e:
            logger.error("Failed to render HTML", error=str(e), exc_info=True)
            raise

    def _extract_toc(self, markdown_content: str) -> List[Dict]:
        """
        Extract table of contents from markdown headings.

        Returns:
            List of TOC items with level, title, id
        """
        toc = []
        heading_pattern = r'^(#{1,6})\s+(.+)$'
        
        for match in re.finditer(heading_pattern, markdown_content, re.MULTILINE):
            level = len(match.group(1))  # Number of # characters
            title = match.group(2).strip()
            heading_id = self._slugify(title)
            
            toc.append({
                'level': level,
                'title': title,
                'id': heading_id
            })
        
        # Build nested structure
        nested_toc = self._build_nested_toc(toc)
        
        return nested_toc

    def _build_nested_toc(self, toc: List[Dict]) -> List[Dict]:
        """
        Build a nested TOC structure based on heading levels.

        Args:
            toc: Flat list of TOC items

        Returns:
            Nested TOC structure with children
        """
        if not toc:
            return []
        
        # For simplicity, return flat structure
        # Frontend can build hierarchy based on levels
        return toc

    def _add_heading_ids(self, markdown_content: str) -> str:
        """
        Add ID attributes to markdown headings for anchor links.

        Args:
            markdown_content: Original markdown

        Returns:
            Markdown with heading IDs
        """
        def replace_heading(match):
            hashes = match.group(1)
            title = match.group(2).strip()
            heading_id = self._slugify(title)
            # Markdown doesn't support IDs directly, so we'll add HTML
            level = len(hashes)
            return f'<h{level} id="{heading_id}">{title}</h{level}>'
        
        heading_pattern = r'^(#{1,6})\s+(.+)$'
        result = re.sub(heading_pattern, replace_heading, markdown_content, flags=re.MULTILINE)
        
        return result

    def _slugify(self, text: str) -> str:
        """
        Convert heading text to URL-safe slug for IDs.

        Args:
            text: Heading text

        Returns:
            Slugified ID
        """
        # Remove special characters
        slug = re.sub(r'[^\w\s-]', '', text.lower())
        # Replace whitespace with hyphens
        slug = re.sub(r'[-\s]+', '-', slug)
        # Remove leading/trailing hyphens
        slug = slug.strip('-')
        return slug

    def _resolve_image_urls(self, markdown_content: str, file_id: str) -> str:
        """
        Replace relative image paths with full API URLs.

        Args:
            markdown_content: Markdown with image references
            file_id: File ID for constructing URLs

        Returns:
            Markdown with resolved image URLs
        """
        def replace_image(match):
            alt_text = match.group(1)
            image_path = match.group(2)
            
            # Extract image index from path (e.g., "images/image_0.png" -> "0")
            image_match = re.search(r'image_(\d+)\.\w+', image_path)
            if image_match:
                image_index = image_match.group(1)
                # Construct API URL
                api_url = f"/api/files/{file_id}/images/{image_index}"
                return f'![{alt_text}]({api_url})'
            
            return match.group(0)  # Return unchanged if pattern doesn't match
        
        image_pattern = r'!\[([^\]]*)\]\(([^\)]+)\)'
        result = re.sub(image_pattern, replace_image, markdown_content)
        
        return result

    def _sanitize_html(self, html: str) -> str:
        """
        Sanitize HTML to prevent XSS attacks.

        Args:
            html: Raw HTML

        Returns:
            Sanitized HTML
        """
        clean_html = bleach.clean(
            html,
            tags=self.allowed_tags,
            attributes=self.allowed_attributes,
            strip=True
        )
        
        return clean_html

    def _add_styling_classes(self, html: str) -> str:
        """
        Add CSS classes for better styling.

        Args:
            html: HTML content

        Returns:
            HTML with styling classes
        """
        # Add responsive image class
        html = re.sub(
            r'<img\s',
            '<img class="doc-image" style="max-width: 100%; height: auto;" ',
            html
        )
        
        # Add table classes
        html = re.sub(
            r'<table>',
            '<table class="doc-table" style="border-collapse: collapse; width: 100%;">',
            html
        )
        
        # Add code block classes
        html = re.sub(
            r'<pre>',
            '<pre class="doc-code-block" style="background: #f5f5f5; padding: 1em; border-radius: 4px; overflow-x: auto;">',
            html
        )
        
        return html

    def render_toc_html(self, toc: List[Dict]) -> str:
        """
        Render table of contents as HTML.

        Args:
            toc: TOC items

        Returns:
            HTML string for TOC
        """
        if not toc:
            return ""
        
        html_parts = ['<nav class="toc">']
        html_parts.append('<h2>Table of Contents</h2>')
        html_parts.append('<ul>')
        
        for item in toc:
            indent = '  ' * (item['level'] - 1)
            html_parts.append(
                f'{indent}<li><a href="#{item["id"]}">{item["title"]}</a></li>'
            )
        
        html_parts.append('</ul>')
        html_parts.append('</nav>')
        
        return '\n'.join(html_parts)


