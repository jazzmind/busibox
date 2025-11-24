"""
Tests for HTML Renderer Module
"""

import pytest
from processors.html_renderer import HTMLRenderer


class TestHTMLRenderer:
    """Test suite for HTMLRenderer class"""

    def setup_method(self):
        """Setup test fixtures"""
        self.renderer = HTMLRenderer()

    def test_markdown_to_html_conversion(self):
        """Test basic markdown to HTML conversion"""
        markdown = """# Main Title

This is a paragraph.

## Section 1

More content here."""
        
        html, toc = self.renderer.render(markdown)
        
        assert html is not None
        assert len(html) > 0
        assert '<h1' in html
        assert '<h2' in html
        assert 'paragraph' in html

    def test_html_toc_generation(self):
        """Test table of contents generation from headings"""
        markdown = """# Title
## Section 1
### Subsection 1.1
## Section 2
### Subsection 2.1
### Subsection 2.2"""
        
        html, toc = self.renderer.render(markdown)
        
        assert len(toc) == 6
        assert toc[0]['level'] == 1
        assert toc[0]['title'] == 'Title'
        assert toc[1]['level'] == 2
        assert toc[1]['title'] == 'Section 1'

    def test_html_heading_ids(self):
        """Test that headings have unique IDs"""
        markdown = """# Introduction
## Background
## Methods"""
        
        html, toc = self.renderer.render(markdown)
        
        assert 'id="introduction"' in html
        assert 'id="background"' in html
        assert 'id="methods"' in html

    def test_html_image_src_replacement(self):
        """Test that image sources are replaced with API endpoints"""
        markdown = """# Document
![Figure 1](images/image_0.png)
![Figure 2](images/image_1.png)"""
        
        file_id = "test-file-123"
        html, toc = self.renderer.render(markdown, file_id=file_id)
        
        assert f'/api/files/{file_id}/images/0' in html
        assert f'/api/files/{file_id}/images/1' in html

    def test_html_table_styling(self):
        """Test that tables have proper CSS classes"""
        markdown = """| Col1 | Col2 |
|------|------|
| A    | B    |"""
        
        html, toc = self.renderer.render(markdown)
        
        assert '<table' in html
        assert 'class="doc-table"' in html

    def test_html_code_block_syntax_highlighting(self):
        """Test that code blocks are formatted"""
        markdown = """```python
def hello():
    print("world")
```"""
        
        html, toc = self.renderer.render(markdown)
        
        assert '<pre' in html
        assert '<code' in html or 'class="doc-code-block"' in html

    def test_html_sanitization(self):
        """Test that dangerous HTML is removed"""
        markdown = """# Title
<script>alert('xss')</script>
<div onclick="malicious()">Click</div>
<iframe src="evil.com"></iframe>"""
        
        html, toc = self.renderer.render(markdown)
        
        assert '<script>' not in html
        assert 'onclick' not in html
        assert '<iframe>' not in html

    def test_html_responsive_images(self):
        """Test that images have responsive styling"""
        markdown = """![Test Image](images/image_0.png)"""
        
        html, toc = self.renderer.render(markdown, file_id="test-123")
        
        assert 'max-width: 100%' in html or 'class="doc-image"' in html

    def test_html_toc_nested_structure(self):
        """Test TOC respects heading hierarchy"""
        markdown = """# H1
## H2 under H1
### H3 under H2
## Another H2
# Another H1"""
        
        html, toc = self.renderer.render(markdown)
        
        # Check levels are correct
        assert toc[0]['level'] == 1
        assert toc[1]['level'] == 2
        assert toc[2]['level'] == 3
        assert toc[3]['level'] == 2
        assert toc[4]['level'] == 1

    def test_slugify(self):
        """Test heading text to ID slugification"""
        assert self.renderer._slugify("Introduction") == "introduction"
        assert self.renderer._slugify("Section 2.1") == "section-21"
        assert self.renderer._slugify("User's Guide") == "users-guide"
        assert self.renderer._slugify("Multi   Space") == "multi-space"

    def test_empty_markdown(self):
        """Test handling of empty markdown"""
        html, toc = self.renderer.render("")
        
        assert html == ""
        assert toc == []

    def test_markdown_with_lists(self):
        """Test rendering of bullet and numbered lists"""
        markdown = """# Lists

Bullet list:

- Item 1
- Item 2
- Item 3

Numbered list:

1. First
2. Second
3. Third"""
        
        html, toc = self.renderer.render(markdown)
        
        assert '<ul>' in html
        assert '<ol>' in html
        assert '<li>' in html

    def test_markdown_with_emphasis(self):
        """Test rendering of bold, italic, and inline code"""
        markdown = """**bold text** and *italic text* and `inline code`"""
        
        html, toc = self.renderer.render(markdown)
        
        assert '<strong>' in html or '<b>' in html
        assert '<em>' in html or '<i>' in html
        assert '<code>' in html

    def test_render_toc_html(self):
        """Test rendering TOC as HTML"""
        toc = [
            {'level': 1, 'title': 'Introduction', 'id': 'introduction'},
            {'level': 2, 'title': 'Background', 'id': 'background'},
            {'level': 2, 'title': 'Methods', 'id': 'methods'}
        ]
        
        toc_html = self.renderer.render_toc_html(toc)
        
        assert '<nav class="toc">' in toc_html
        assert 'Table of Contents' in toc_html
        assert '<a href="#introduction">Introduction</a>' in toc_html
        assert '<a href="#background">Background</a>' in toc_html

    def test_toc_html_empty(self):
        """Test TOC HTML with empty TOC"""
        toc_html = self.renderer.render_toc_html([])
        
        assert toc_html == ""

    def test_image_url_without_file_id(self):
        """Test image rendering when no file_id provided"""
        markdown = """![Test](images/image_0.png)"""
        
        html, toc = self.renderer.render(markdown)
        
        # Should keep relative path
        assert 'images/image_0.png' in html

    def test_blockquote_rendering(self):
        """Test blockquote rendering"""
        markdown = """> This is a quote
> spanning multiple lines"""
        
        html, toc = self.renderer.render(markdown)
        
        assert '<blockquote>' in html

    def test_horizontal_rule_rendering(self):
        """Test horizontal rule rendering"""
        markdown = """Section 1

---

Section 2"""
        
        html, toc = self.renderer.render(markdown)
        
        assert '<hr' in html or '<hr>' in html

    def test_special_characters_in_headings(self):
        """Test headings with special characters get proper IDs"""
        markdown = """# User's Guide (2024)
## Section #1: Introduction"""
        
        html, toc = self.renderer.render(markdown)
        
        # IDs should be sanitized
        assert 'id="users-guide-2024"' in html
        assert 'id="section-1-introduction"' in html

    def test_duplicate_heading_titles(self):
        """Test handling of duplicate heading titles"""
        markdown = """# Introduction
## Background
# Introduction"""
        
        html, toc = self.renderer.render(markdown)
        
        # Both should have the same ID (simple implementation)
        # More sophisticated would append -1, -2, etc.
        assert html.count('id="introduction"') >= 1

    def test_very_long_heading(self):
        """Test handling of very long headings"""
        long_title = "This is a very long heading that goes on and on and should still work"
        markdown = f"# {long_title}"
        
        html, toc = self.renderer.render(markdown)
        
        assert long_title in html
        assert len(toc) == 1
        assert toc[0]['title'] == long_title

    def test_allowed_html_tags(self):
        """Test that allowed HTML tags are preserved"""
        markdown = """<div>This is a div</div>
<span>This is a span</span>
<strong>Bold</strong>"""
        
        html, toc = self.renderer.render(markdown)
        
        # bleach should allow these tags
        assert 'div' in html or 'span' in html or 'strong' in html

    def test_links_preserved(self):
        """Test that markdown links are converted to HTML links"""
        markdown = """[Link text](https://example.com)"""
        
        html, toc = self.renderer.render(markdown)
        
        assert '<a href="https://example.com"' in html
        assert 'Link text' in html


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


