"""
Unit tests for highlighting service.
"""

import pytest
from services.highlighter import HighlightingService


@pytest.mark.unit
class TestHighlightingService:
    """Test HighlightingService class."""
    
    def test_init(self, mock_config):
        """Test service initialization."""
        service = HighlightingService(mock_config)
        
        assert service.pre_tag == "<mark>"
        assert service.post_tag == "</mark>"
        assert service.fragment_size == 200
        assert service.num_fragments == 3
    
    def test_highlight_exact_match(self, mock_config):
        """Test highlighting with exact matches."""
        service = HighlightingService(mock_config)
        
        query = "machine learning"
        text = "Machine learning is a subset of artificial intelligence."
        
        fragments = service.highlight(query, text, fragment_size=100, num_fragments=1)
        
        assert len(fragments) > 0
        assert "<mark>" in fragments[0]["fragment"]
        assert "</mark>" in fragments[0]["fragment"]
        assert fragments[0]["score"] > 0
    
    def test_highlight_stemming(self, mock_config):
        """Test highlighting with stemming (run/running)."""
        service = HighlightingService(mock_config)
        
        query = "run"
        text = "Running is a great form of exercise. Many people run daily."
        
        fragments = service.highlight(query, text, fragment_size=100, num_fragments=1)
        
        # Should match both "Running" and "run"
        assert len(fragments) > 0
        fragment_text = fragments[0]["fragment"]
        assert "<mark>" in fragment_text.lower()
    
    def test_highlight_multiple_terms(self, mock_config):
        """Test highlighting multiple query terms."""
        service = HighlightingService(mock_config)
        
        query = "machine learning algorithms"
        text = "Machine learning uses various algorithms to process data."
        
        fragments = service.highlight(query, text, fragment_size=100, num_fragments=1)
        
        assert len(fragments) > 0
        fragment_text = fragments[0]["fragment"]
        # Should highlight both "machine learning" and "algorithms"
        assert fragment_text.count("<mark>") >= 2
    
    def test_highlight_no_matches(self, mock_config):
        """Test highlighting when no matches found."""
        service = HighlightingService(mock_config)
        
        query = "quantum computing"
        text = "Machine learning is a subset of artificial intelligence."
        
        fragments = service.highlight(query, text, fragment_size=100, num_fragments=1)
        
        # Should return first fragment without highlighting
        assert len(fragments) > 0
        assert fragments[0]["score"] == 0.0
        assert "<mark>" not in fragments[0]["fragment"]
    
    def test_fragment_extraction(self, mock_config):
        """Test fragment extraction around matches."""
        service = HighlightingService(mock_config)
        
        query = "important"
        text = "This is some text before. " + "This is the important section we want to find. " + "This is some text after."
        
        fragments = service.highlight(query, text, fragment_size=50, num_fragments=1)
        
        assert len(fragments) > 0
        # Fragment should be centered around "important"
        assert "important" in fragments[0]["fragment"].lower()
        assert "<mark>" in fragments[0]["fragment"]
    
    def test_multiple_fragments(self, mock_config):
        """Test extracting multiple fragments."""
        service = HighlightingService(mock_config)
        
        query = "test"
        text = "This is a test sentence. " * 10 + "Another test here. " * 10
        
        fragments = service.highlight(query, text, fragment_size=50, num_fragments=3)
        
        # Should get multiple fragments
        assert len(fragments) <= 3
        assert all("test" in f["fragment"].lower() for f in fragments)
    
    def test_tokenize_and_stem(self, mock_config):
        """Test tokenization and stemming."""
        service = HighlightingService(mock_config)
        
        text = "Running quickly through algorithms"
        tokens = service._tokenize_and_stem(text)
        
        # Should be stemmed (Porter stemmer produces stems, not always root words)
        assert "run" in tokens  # running -> run
        assert "quickli" in tokens  # quickly -> quickli (Porter stemmer)
        assert "algorithm" in tokens  # algorithms -> algorithm
    
    def test_find_matches(self, mock_config):
        """Test finding matches in text."""
        service = HighlightingService(mock_config)
        
        query_tokens = ["machin", "learn"]  # Stemmed versions
        text = "Machine learning is amazing. Learning machines are cool."
        
        matches = service._find_matches(query_tokens, text)
        
        # Should find "Machine" and multiple "learning"/"Learning"
        assert len(matches) > 0
        assert any(m[2].lower() == "machine" for m in matches)
        assert any(m[2].lower() == "learning" for m in matches)
    
    def test_edit_distance(self, mock_config):
        """Test edit distance calculation."""
        service = HighlightingService(mock_config)
        
        assert service._edit_distance("cat", "cat") == 0
        assert service._edit_distance("cat", "bat") == 1
        assert service._edit_distance("cat", "cats") == 1
        assert service._edit_distance("cat", "dog") == 3
    
    def test_cluster_matches(self, mock_config):
        """Test clustering nearby matches."""
        service = HighlightingService(mock_config)
        
        # Matches that are close together
        matches = [
            (10, 15, "word1", 1.0),
            (20, 25, "word2", 1.0),
            (200, 205, "word3", 1.0),  # Far away
        ]
        
        clusters = service._cluster_matches(matches, fragment_size=100)
        
        # Should create 2 clusters (first two together, last one separate)
        assert len(clusters) == 2
    
    def test_apply_highlights(self, mock_config):
        """Test applying HTML tags."""
        service = HighlightingService(mock_config)
        
        text = "This is a test sentence."
        matches = [
            (10, 14, "test", 1.0),  # "test"
        ]
        
        highlighted = service._apply_highlights(text, matches)
        
        assert "<mark>test</mark>" in highlighted
        assert highlighted == "This is a <mark>test</mark> sentence."

