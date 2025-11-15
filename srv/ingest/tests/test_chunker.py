"""
Tests for semantic-aware chunking.

Tests cover:
- Basic paragraph chunking
- Heading detection and preservation
- List handling (numbered and bulleted)
- Section boundary detection
- Token limit enforcement
- Milvus varchar limit enforcement (65535 chars)
- Overlap between chunks
- Markdown conversion
- Edge cases (empty text, single paragraph, very long paragraphs)
"""

import pytest
from processors.chunker import Chunker


@pytest.fixture
def chunker():
    """Create chunker with standard config."""
    config = {
        "chunk_size_min": 100,
        "chunk_size_max": 400,
        "chunk_overlap_pct": 0.15,
    }
    return Chunker(config)


@pytest.fixture
def small_chunker():
    """Create chunker with small chunks for testing."""
    config = {
        "chunk_size_min": 50,
        "chunk_size_max": 100,
        "chunk_overlap_pct": 0.2,
    }
    return Chunker(config)


class TestBasicChunking:
    """Test basic chunking functionality."""
    
    def test_simple_paragraphs(self, chunker):
        """Test chunking of simple paragraphs."""
        text = """
        This is the first paragraph. It contains several sentences. Each sentence adds to the content.
        
        This is the second paragraph. It also has multiple sentences. The content continues here.
        
        This is the third paragraph. More content follows. The text keeps going.
        """
        
        chunks = chunker.chunk(text)
        
        assert len(chunks) > 0
        assert all(chunk.text for chunk in chunks)
        assert all(chunk.token_count > 0 for chunk in chunks)
        assert all(len(chunk.text) < 65535 for chunk in chunks)  # Milvus limit
    
    def test_single_paragraph(self, chunker):
        """Test chunking of a single short paragraph."""
        text = "This is a single short paragraph with just a few sentences."
        
        chunks = chunker.chunk(text)
        
        assert len(chunks) == 1
        assert chunks[0].text == text.strip()
    
    def test_empty_text(self, chunker):
        """Test chunking of empty text."""
        chunks = chunker.chunk("")
        assert len(chunks) == 0
        
        chunks = chunker.chunk("   \n\n   ")
        assert len(chunks) == 0
    
    def test_chunk_indices(self, chunker):
        """Test that chunk indices are sequential."""
        text = """
        First paragraph with some content.
        
        Second paragraph with more content.
        
        Third paragraph with even more content.
        
        Fourth paragraph to ensure multiple chunks.
        
        Fifth paragraph for good measure.
        """
        
        chunks = chunker.chunk(text)
        
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i


class TestHeadingDetection:
    """Test heading detection and markdown conversion."""
    
    def test_all_caps_heading(self, chunker):
        """Test detection of ALL CAPS headings."""
        text = """
        INTRODUCTION
        
        This is the introduction paragraph. It explains the topic.
        
        METHODOLOGY
        
        This paragraph describes the methodology used in the study.
        """
        
        chunks = chunker.chunk(text)
        
        # Check that headings are converted to markdown
        combined_text = "\n\n".join(c.text for c in chunks)
        assert "# INTRODUCTION" in combined_text or "## INTRODUCTION" in combined_text
        assert "# METHODOLOGY" in combined_text or "## METHODOLOGY" in combined_text
    
    def test_chapter_heading(self, chunker):
        """Test detection of chapter/section headings."""
        text = """
        Chapter 1: Getting Started
        
        This chapter introduces the basic concepts.
        
        Section 2: Advanced Topics
        
        This section covers more advanced material.
        """
        
        chunks = chunker.chunk(text)
        
        combined_text = "\n\n".join(c.text for c in chunks)
        assert "Chapter 1" in combined_text
        assert "Section 2" in combined_text
    
    def test_section_heading_extraction(self, chunker):
        """Test that section headings are extracted to chunk metadata."""
        text = """
        BACKGROUND
        
        This is background information about the topic.
        """
        
        chunks = chunker.chunk(text)
        
        # At least one chunk should have a section heading
        assert any(chunk.section_heading for chunk in chunks)


class TestListHandling:
    """Test handling of lists (numbered and bulleted)."""
    
    def test_numbered_list(self, chunker):
        """Test handling of numbered lists."""
        text = """
        Here are the steps:
        
        1. First step in the process
        2. Second step follows
        3. Third step completes it
        
        This is a concluding paragraph.
        """
        
        chunks = chunker.chunk(text)
        
        combined_text = "\n\n".join(c.text for c in chunks)
        assert "1." in combined_text
        assert "2." in combined_text
        assert "3." in combined_text
    
    def test_bulleted_list(self, chunker):
        """Test handling of bulleted lists."""
        text = """
        Key points include:
        
        • First important point
        • Second important point
        • Third important point
        
        Summary paragraph follows.
        """
        
        chunks = chunker.chunk(text)
        
        combined_text = "\n\n".join(c.text for c in chunks)
        # Bullets should be converted to markdown format
        assert combined_text.count("-") >= 3  # At least 3 list items


class TestTokenLimits:
    """Test enforcement of token limits."""
    
    def test_respects_max_tokens(self, small_chunker):
        """Test that chunks don't exceed max token limit."""
        # Create text that will definitely need multiple chunks
        text = " ".join(["This is sentence number {}.".format(i) for i in range(100)])
        
        chunks = small_chunker.chunk(text)
        
        assert len(chunks) > 1  # Should create multiple chunks
        for chunk in chunks:
            assert chunk.token_count <= small_chunker.max_tokens
    
    def test_respects_min_tokens(self, small_chunker):
        """Test that chunks meet minimum token requirement."""
        text = " ".join(["Sentence {}.".format(i) for i in range(50)])
        
        chunks = small_chunker.chunk(text)
        
        # All chunks except possibly the last should meet min tokens
        for chunk in chunks[:-1]:
            assert chunk.token_count >= small_chunker.min_tokens


class TestMilvusLimit:
    """Test enforcement of Milvus varchar limit (65535 characters)."""
    
    def test_very_long_paragraph(self, chunker):
        """Test handling of paragraph exceeding Milvus limit."""
        # Create a paragraph with 100,000 characters
        long_text = "This is a very long sentence. " * 3000
        
        chunks = chunker.chunk(long_text)
        
        # All chunks must be under Milvus limit
        for chunk in chunks:
            assert len(chunk.text) <= 65535, f"Chunk length {len(chunk.text)} exceeds Milvus limit"
    
    def test_multiple_long_paragraphs(self, chunker):
        """Test handling of multiple long paragraphs."""
        # Create multiple paragraphs, each quite long
        paragraphs = []
        for i in range(5):
            para = f"Paragraph {i}. " + ("Long content here. " * 500)
            paragraphs.append(para)
        
        text = "\n\n".join(paragraphs)
        chunks = chunker.chunk(text)
        
        # All chunks must be under Milvus limit
        for chunk in chunks:
            assert len(chunk.text) <= 65535
            assert chunk.token_count > 0


class TestChunkOverlap:
    """Test overlap between consecutive chunks."""
    
    def test_has_overlap(self, small_chunker):
        """Test that consecutive chunks have overlapping content."""
        text = """
        First paragraph with unique content about topic A.
        
        Second paragraph with unique content about topic B.
        
        Third paragraph with unique content about topic C.
        
        Fourth paragraph with unique content about topic D.
        """
        
        chunks = small_chunker.chunk(text)
        
        if len(chunks) > 1:
            # Check for overlap between consecutive chunks
            for i in range(len(chunks) - 1):
                chunk1_words = set(chunks[i].text.split())
                chunk2_words = set(chunks[i + 1].text.split())
                overlap = chunk1_words & chunk2_words
                
                # Should have some overlapping words
                assert len(overlap) > 0, "Consecutive chunks should have overlap"


class TestMarkdownConversion:
    """Test conversion of semantic structures to markdown."""
    
    def test_heading_to_markdown(self, chunker):
        """Test conversion of headings to markdown format."""
        text = """
        SECTION ONE
        
        Content under section one.
        """
        
        chunks = chunker.chunk(text)
        combined = "\n".join(c.text for c in chunks)
        
        # Should have markdown heading
        assert "#" in combined
    
    def test_preserves_structure(self, chunker):
        """Test that document structure is preserved."""
        text = """
        INTRODUCTION
        
        This is the introduction.
        
        1. First point
        2. Second point
        3. Third point
        
        CONCLUSION
        
        This is the conclusion.
        """
        
        chunks = chunker.chunk(text)
        combined = "\n\n".join(c.text for c in chunks)
        
        # Check structure is preserved
        intro_pos = combined.find("INTRODUCTION")
        conclusion_pos = combined.find("CONCLUSION")
        
        assert intro_pos < conclusion_pos, "Document order should be preserved"


class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_only_whitespace(self, chunker):
        """Test handling of whitespace-only text."""
        text = "   \n\n\t\t   \n   "
        chunks = chunker.chunk(text)
        assert len(chunks) == 0
    
    def test_single_word(self, chunker):
        """Test handling of single word."""
        chunks = chunker.chunk("Hello")
        assert len(chunks) == 1
        assert chunks[0].text == "Hello"
    
    def test_unicode_content(self, chunker):
        """Test handling of unicode characters."""
        text = """
        This paragraph contains unicode: café, naïve, 日本語, emoji 🎉
        
        More content with special characters: ñ, ü, ö, é
        """
        
        chunks = chunker.chunk(text)
        assert len(chunks) > 0
        combined = "".join(c.text for c in chunks)
        assert "café" in combined
        assert "🎉" in combined
    
    def test_code_blocks(self, chunker):
        """Test handling of code-like content."""
        text = """
        Here is some code:
        
        def hello():
            print("Hello, world!")
            return True
        
        The function above prints a greeting.
        """
        
        chunks = chunker.chunk(text)
        assert len(chunks) > 0
        combined = "\n".join(c.text for c in chunks)
        assert "def hello" in combined


class TestRealWorldDocuments:
    """Test with realistic document structures."""
    
    def test_research_paper_structure(self, chunker):
        """Test chunking of research paper-like structure."""
        text = """
        ABSTRACT
        
        This paper presents a novel approach to semantic chunking. We demonstrate
        improved performance over baseline methods.
        
        INTRODUCTION
        
        Document processing is a critical task. Previous work has shown various
        approaches. Our method builds on these foundations.
        
        METHODOLOGY
        
        We use the following approach:
        
        1. Parse document structure
        2. Identify semantic boundaries
        3. Create overlapping chunks
        4. Preserve markdown formatting
        
        RESULTS
        
        Our experiments show significant improvements. The results are statistically
        significant with p < 0.05.
        
        CONCLUSION
        
        We have demonstrated an effective chunking approach. Future work will
        explore additional optimizations.
        """
        
        chunks = chunker.chunk(text)
        
        assert len(chunks) > 0
        # All chunks should be valid
        for chunk in chunks:
            assert len(chunk.text) > 0
            assert len(chunk.text) <= 65535
            assert chunk.token_count > 0
    
    def test_technical_documentation(self, chunker):
        """Test chunking of technical documentation."""
        text = """
        Getting Started
        
        Follow these steps to install:
        
        1. Download the package
        2. Run the installer
        3. Configure settings
        
        Configuration Options
        
        The following options are available:
        
        • timeout: Request timeout in seconds
        • retries: Number of retry attempts
        • verbose: Enable verbose logging
        
        Advanced Usage
        
        For advanced use cases, refer to the API documentation.
        """
        
        chunks = chunker.chunk(text)
        
        assert len(chunks) > 0
        combined = "\n\n".join(c.text for c in chunks)
        
        # Check structure preservation
        assert "Getting Started" in combined
        assert "Configuration Options" in combined
        assert "Advanced Usage" in combined


class TestPerformance:
    """Test performance with large documents."""
    
    def test_large_document(self, chunker):
        """Test chunking of large document (simulating 50-page PDF)."""
        # Simulate a 50-page document (~50,000 words)
        paragraphs = []
        for page in range(50):
            for para in range(20):
                text = f"Page {page} paragraph {para}. " + ("Content here. " * 20)
                paragraphs.append(text)
        
        full_text = "\n\n".join(paragraphs)
        
        chunks = chunker.chunk(full_text)
        
        # Should create many chunks
        assert len(chunks) > 10
        
        # All chunks must respect limits
        for chunk in chunks:
            assert len(chunk.text) <= 65535
            assert chunk.token_count > 0
            assert chunk.token_count <= chunker.max_tokens


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

