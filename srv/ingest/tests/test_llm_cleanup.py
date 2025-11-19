"""
Tests for LLM cleanup processor.

Tests the LLM-based text cleanup functionality that fixes:
- Smashed words
- Missing spaces
- Incorrect line breaks
- Poor paragraph formatting
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from processors.llm_cleanup import LLMCleanup


@pytest.fixture
def mock_config():
    """Mock configuration for testing."""
    return {
        "llm_cleanup_enabled": True,
        "litellm_base_url": "http://localhost:4000",
        "litellm_api_key": "test-key",
    }


@pytest.fixture
def mock_config_disabled():
    """Mock configuration with cleanup disabled."""
    return {
        "llm_cleanup_enabled": False,
        "litellm_base_url": "http://localhost:4000",
    }


@pytest.fixture
def mock_registry():
    """Mock model registry."""
    registry = Mock()
    registry.get_model.return_value = "qwen3-30b-instruct"
    registry.get_config.return_value = {
        "temperature": 0.1,
        "max_tokens": 32768,
    }
    return registry


@pytest.fixture
def cleanup_processor(mock_config, mock_registry):
    """Create cleanup processor with mocked dependencies."""
    with patch('processors.llm_cleanup.get_registry', return_value=mock_registry):
        processor = LLMCleanup(mock_config)
        return processor


@pytest.fixture
def cleanup_processor_disabled(mock_config_disabled, mock_registry):
    """Create cleanup processor with cleanup disabled."""
    with patch('processors.llm_cleanup.get_registry', return_value=mock_registry):
        processor = LLMCleanup(mock_config_disabled)
        return processor


class TestLLMCleanupInit:
    """Test LLM cleanup initialization."""
    
    def test_init_enabled(self, cleanup_processor):
        """Test initialization with cleanup enabled."""
        assert cleanup_processor.enabled is True
        assert cleanup_processor.model == "qwen3-30b-instruct"
        assert cleanup_processor.litellm_base_url == "http://localhost:4000"
    
    def test_init_disabled(self, cleanup_processor_disabled):
        """Test initialization with cleanup disabled."""
        assert cleanup_processor_disabled.enabled is False
    
    def test_init_with_fallback_model(self, mock_config):
        """Test initialization falls back to default model if registry fails."""
        with patch('processors.llm_cleanup.get_registry', side_effect=Exception("Registry error")):
            processor = LLMCleanup(mock_config)
            assert processor.model == "qwen3-30b-instruct"


class TestNeedsCleanup:
    """Test detection of text that needs cleanup."""
    
    def test_needs_cleanup_long_words(self, cleanup_processor):
        """Test detection of smashed words (long words)."""
        text = "This is some text with actuallyunderstoodverylongwordthatissmashed together."
        assert cleanup_processor._needs_cleanup(text) is True
    
    def test_no_cleanup_needed(self, cleanup_processor):
        """Test clean text doesn't trigger cleanup."""
        text = "This is clean text with proper spacing and formatting."
        assert cleanup_processor._needs_cleanup(text) is False
    
    def test_needs_cleanup_multiple_long_words(self, cleanup_processor):
        """Test detection of multiple smashed words."""
        text = "Firstlongwordsmashedtogether and anotherlongwordsmashedtogether in the same text."
        assert cleanup_processor._needs_cleanup(text) is True


class TestCleanupChunk:
    """Test single chunk cleanup."""
    
    @pytest.mark.asyncio
    async def test_cleanup_disabled(self, cleanup_processor_disabled):
        """Test cleanup returns original text when disabled."""
        text = "Some text with actuallyunderstoodsmashed words."
        result = await cleanup_processor_disabled.cleanup_chunk(text)
        assert result == text
    
    @pytest.mark.asyncio
    async def test_cleanup_empty_text(self, cleanup_processor):
        """Test cleanup handles empty text."""
        result = await cleanup_processor.cleanup_chunk("")
        assert result == ""
    
    @pytest.mark.asyncio
    async def test_cleanup_short_text(self, cleanup_processor):
        """Test cleanup skips very short text."""
        text = "Short"
        result = await cleanup_processor.cleanup_chunk(text)
        assert result == text
    
    @pytest.mark.asyncio
    async def test_cleanup_clean_text(self, cleanup_processor):
        """Test cleanup skips text that doesn't need cleaning."""
        text = "This is clean text with proper spacing."
        result = await cleanup_processor.cleanup_chunk(text)
        assert result == text
    
    @pytest.mark.asyncio
    async def test_cleanup_success(self, cleanup_processor):
        """Test successful cleanup of smashed words."""
        original_text = "This text has actuallyunderstoodsmashed words that need fixing."
        cleaned_text = "This text has actually understood smashed words that need fixing."
        
        # Mock the HTTP response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": cleaned_text
                    }
                }
            ]
        }
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await cleanup_processor.cleanup_chunk(original_text)
            assert result == cleaned_text
    
    @pytest.mark.asyncio
    async def test_cleanup_http_error(self, cleanup_processor):
        """Test cleanup handles HTTP errors gracefully."""
        text = "Text with actuallyunderstoodsmashed words."
        
        # Mock HTTP error
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await cleanup_processor.cleanup_chunk(text)
            # Should return original text on error
            assert result == text
    
    @pytest.mark.asyncio
    async def test_cleanup_timeout(self, cleanup_processor):
        """Test cleanup handles timeouts gracefully."""
        text = "Text with actuallyunderstoodsmashed words."
        
        # Mock timeout
        import httpx
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.TimeoutException("Timeout")
            )
            result = await cleanup_processor.cleanup_chunk(text)
            # Should return original text on timeout
            assert result == text
    
    @pytest.mark.asyncio
    async def test_cleanup_empty_response(self, cleanup_processor):
        """Test cleanup handles empty LLM response."""
        text = "Text with actuallyunderstoodsmashed words."
        
        # Mock empty response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": ""
                    }
                }
            ]
        }
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await cleanup_processor.cleanup_chunk(text)
            # Should return original text if LLM returns empty
            assert result == text
    
    @pytest.mark.asyncio
    async def test_cleanup_suspicious_length(self, cleanup_processor):
        """Test cleanup rejects responses with suspicious length changes."""
        original_text = "This is a reasonable length text with actuallyunderstoodsmashed words."
        # Response is way too short (less than 50% of original)
        suspicious_text = "Short."
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": suspicious_text
                    }
                }
            ]
        }
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await cleanup_processor.cleanup_chunk(original_text)
            # Should return original text if length is suspicious
            assert result == original_text


class TestCleanupChunks:
    """Test batch chunk cleanup."""
    
    @pytest.mark.asyncio
    async def test_cleanup_chunks_disabled(self, cleanup_processor_disabled):
        """Test batch cleanup returns original chunks when disabled."""
        from processors.chunker import Chunk
        
        chunks = [
            Chunk(text="Chunk 1 with actuallyunderstoodsmashed", chunk_index=0, token_count=10, char_offset=0),
            Chunk(text="Chunk 2 with moresmashedwords", chunk_index=1, token_count=10, char_offset=50),
        ]
        
        result = await cleanup_processor_disabled.cleanup_chunks(chunks)
        assert len(result) == 2
        assert result[0].text == chunks[0].text
        assert result[1].text == chunks[1].text
    
    @pytest.mark.asyncio
    async def test_cleanup_chunks_success(self, cleanup_processor):
        """Test successful batch cleanup."""
        from processors.chunker import Chunk
        
        chunks = [
            Chunk(text="Chunk 1 with actuallyunderstoodsmashed words", chunk_index=0, token_count=10, char_offset=0),
            Chunk(text="Chunk 2 is clean", chunk_index=1, token_count=10, char_offset=50),
        ]
        
        # Mock cleanup_chunk to return cleaned text for first chunk
        async def mock_cleanup(text):
            if "actuallyunderstoodsmashed" in text:
                return text.replace("actuallyunderstoodsmashed", "actually understood smashed")
            return text
        
        cleanup_processor.cleanup_chunk = mock_cleanup
        
        result = await cleanup_processor.cleanup_chunks(chunks)
        assert len(result) == 2
        assert "actually understood smashed" in result[0].text
        assert result[1].text == "Chunk 2 is clean"
    
    @pytest.mark.asyncio
    async def test_cleanup_chunks_empty_list(self, cleanup_processor):
        """Test cleanup handles empty chunk list."""
        result = await cleanup_processor.cleanup_chunks([])
        assert result == []


class TestCleanupIntegration:
    """Integration tests for cleanup processor."""
    
    @pytest.mark.asyncio
    async def test_cleanup_preserves_markdown(self, cleanup_processor):
        """Test cleanup preserves markdown formatting."""
        text = """# Heading

This is a paragraph with actuallyunderstoodsmashed words.

## Subheading

- List item 1
- List item 2 with moresmashedwords

**Bold text** and *italic text*."""
        
        cleaned_text = """# Heading

This is a paragraph with actually understood smashed words.

## Subheading

- List item 1
- List item 2 with more smashed words

**Bold text** and *italic text*."""
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": cleaned_text
                    }
                }
            ]
        }
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await cleanup_processor.cleanup_chunk(text)
            
            # Verify markdown is preserved
            assert "# Heading" in result
            assert "## Subheading" in result
            assert "**Bold text**" in result
            assert "*italic text*" in result
            assert "- List item" in result
    
    @pytest.mark.asyncio
    async def test_cleanup_fixes_spacing(self, cleanup_processor):
        """Test cleanup fixes spacing issues."""
        text = "Word.Another sentence.Yetanother one."
        cleaned_text = "Word. Another sentence. Yet another one."
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": cleaned_text
                    }
                }
            ]
        }
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            result = await cleanup_processor.cleanup_chunk(text)
            assert result == cleaned_text

