"""
Tests for Markdown Generator Module
"""

import pytest
from processors.markdown_generator import MarkdownGenerator


class TestMarkdownGenerator:
    """Test suite for MarkdownGenerator class"""

    def setup_method(self):
        """Setup test fixtures"""
        self.generator = MarkdownGenerator()

    def test_generate_markdown_from_simple_text(self):
        """Test basic markdown generation from plain text"""
        text = "This is a simple document.\nIt has multiple paragraphs.\n\nThis is the second paragraph."
        
        markdown, metadata = self.generator.generate(text, extraction_method="simple")
        
        assert markdown is not None
        assert len(markdown) > 0
        assert "simple document" in markdown
        assert metadata['word_count'] > 0

    def test_generate_markdown_with_headings(self):
        """Test markdown generation preserves heading structure"""
        text = """INTRODUCTION
This is the introduction section.

METHODS
This section describes the methods."""
        
        markdown, metadata = self.generator.generate(text, extraction_method="simple")
        
        # Should detect uppercase lines as headings
        assert "# INTRODUCTION" in markdown or "## INTRODUCTION" in markdown
        assert "# METHODS" in markdown or "## METHODS" in markdown
        assert metadata['has_headings'] is True
        assert metadata['heading_count'] >= 2

    def test_generate_markdown_with_tables(self):
        """Test markdown generation preserves table formatting"""
        text_with_table = """Data Results

| Name | Value |
|------|-------|
| Test1 | 100  |
| Test2 | 200  |
"""
        
        markdown, metadata = self.generator.generate(text_with_table, extraction_method="marker")
        
        # Tables should be preserved from marker output
        assert "|" in markdown
        assert metadata['has_tables'] is True

    def test_generate_markdown_with_lists(self):
        """Test markdown generation preserves list formatting"""
        text = """Shopping List:
• Item one
• Item two
• Item three"""
        
        markdown, metadata = self.generator.generate(text, extraction_method="simple")
        
        # Should convert bullet points to markdown list format
        assert "- Item one" in markdown
        assert "- Item two" in markdown

    def test_generate_markdown_with_images(self):
        """Test markdown generation with image references"""
        text = "Document with images"
        images = [
            {"path": "images/image_0.png", "caption": "Figure 1"},
            {"path": "images/image_1.png", "caption": "Figure 2"}
        ]
        
        markdown, metadata = self.generator.generate(text, extraction_method="simple", images=images)
        
        assert "![Figure 1](images/image_0.png)" in markdown
        assert "![Figure 2](images/image_1.png)" in markdown
        assert metadata['image_references'] == 2

    def test_markdown_sanitization(self):
        """Test markdown sanitization removes dangerous content"""
        dangerous_markdown = """# Document
<script>alert('xss')</script>
<div onclick="malicious()">Click</div>
<iframe src="evil.com"></iframe>"""
        
        sanitized = self.generator.sanitize_markdown(dangerous_markdown)
        
        assert "<script>" not in sanitized
        assert "onclick" not in sanitized
        assert "<iframe>" not in sanitized

    def test_markdown_from_marker_output(self):
        """Test handling of Marker-specific formatting"""
        marker_text = """# Document Title

## Introduction

This is text from Marker extraction.

### Subsection

More content here.

| Col1 | Col2 |
|------|------|
| A    | B    |
"""
        
        markdown, metadata = self.generator.generate(marker_text, extraction_method="marker")
        
        # Should preserve marker's markdown formatting
        assert "# Document Title" in markdown
        assert "## Introduction" in markdown
        assert metadata['heading_count'] >= 3
        assert metadata['title'] == "Document Title"

    def test_markdown_metadata_extraction(self):
        """Test metadata extraction from markdown"""
        text = """# Main Title

## Section 1
Content here.

## Section 2
More content.

```python
code block
```
"""
        
        markdown, metadata = self.generator.generate(text, extraction_method="marker")
        
        assert metadata['title'] == "Main Title"
        assert metadata['heading_count'] >= 3
        assert metadata['code_blocks'] >= 1
        assert metadata['word_count'] > 0

    def test_empty_text_handling(self):
        """Test handling of empty text input"""
        markdown, metadata = self.generator.generate("", extraction_method="simple")
        
        assert markdown == ""
        assert metadata['word_count'] == 0

    def test_normalize_headings(self):
        """Test heading normalization"""
        markdown_with_bad_headings = """#NoSpace
## Good Heading
###AlsoNoSpace"""
        
        normalized = self.generator._normalize_headings(markdown_with_bad_headings)
        
        assert "# NoSpace" in normalized
        assert "## Good Heading" in normalized
        assert "### AlsoNoSpace" in normalized

    def test_infer_heading_level(self):
        """Test heading level inference"""
        # Short uppercase -> H1
        assert self.generator._infer_heading_level("TITLE") == 1
        
        # Longer uppercase -> H2
        assert self.generator._infer_heading_level("LONGER SECTION TITLE") == 2
        
        # Title case -> H3
        assert self.generator._infer_heading_level("Title Case Heading") == 3

    def test_is_potential_heading(self):
        """Test heading detection logic"""
        lines = [
            "INTRODUCTION",  # Should be heading
            "This is regular text following the heading.",
            "Some More Text",  # Title case, might be heading
            "continued text"
        ]
        
        assert self.generator._is_potential_heading(lines[0], lines, 0) is True
        assert self.generator._is_potential_heading(lines[1], lines, 1) is False

    def test_excessive_whitespace_cleanup(self):
        """Test cleanup of excessive whitespace"""
        text_with_gaps = "Line 1\n\n\n\n\nLine 2\n\n\n\nLine 3"
        
        markdown, _ = self.generator.generate(text_with_gaps, extraction_method="simple")
        
        # Should reduce to max 2 newlines
        assert "\n\n\n" not in markdown

    def test_very_long_lines_not_headings(self):
        """Test that very long lines are not mistaken for headings"""
        long_line = "THIS IS A VERY LONG LINE THAT SHOULD NOT BE CONSIDERED A HEADING BECAUSE IT HAS TOO MANY WORDS AND EXCEEDS THE REASONABLE LENGTH FOR A TITLE OR SECTION HEADING IN A DOCUMENT"
        lines = [long_line, "following text"]
        
        assert self.generator._is_potential_heading(long_line, lines, 0) is False

    def test_numbered_lists_conversion(self):
        """Test conversion of numbered lists to markdown format"""
        text = """Steps:
1. First step
2. Second step
3. Third step"""
        
        markdown, _ = self.generator.generate(text, extraction_method="simple")
        
        # Should convert to markdown numbered list
        assert "1. First step" in markdown or "1. " in markdown

    def test_image_insertion_with_no_images(self):
        """Test that documents without images work correctly"""
        text = "Document without any images"
        
        markdown, metadata = self.generator.generate(text, extraction_method="simple", images=None)
        
        assert "![" not in markdown
        assert metadata['image_references'] == 0

    def test_metadata_without_title(self):
        """Test metadata extraction when no clear title exists"""
        text = "just some text without a proper heading structure"
        
        markdown, metadata = self.generator.generate(text, extraction_method="simple")
        
        # Should still extract some title from first line
        assert 'title' in metadata
        assert len(metadata['title']) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


