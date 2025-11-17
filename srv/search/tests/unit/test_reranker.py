"""
Unit tests for reranking service.
"""

import pytest
from unittest.mock import Mock, patch
from services.reranker import RerankingService


@pytest.mark.unit
class TestRerankingService:
    """Test RerankingService class."""
    
    def test_init(self, mock_config):
        """Test service initialization."""
        with patch('services.reranker.CrossEncoder') as mock_cross_encoder:
            service = RerankingService(mock_config)
            
            assert service.model_name == "BAAI/bge-reranker-v2-m3"
            assert service.device == "cpu"
            assert service.enabled is True
            mock_cross_encoder.assert_called_once()
    
    def test_rerank_sorts_by_score(self, mock_config):
        """Test that reranking sorts results by score."""
        with patch('services.reranker.CrossEncoder') as mock_cross_encoder:
            mock_model = Mock()
            mock_model.predict = Mock(return_value=[0.9, 0.7, 0.95])
            mock_cross_encoder.return_value = mock_model
            
            service = RerankingService(mock_config)
            
            results = [
                {"text": "text1", "score": 0.8},
                {"text": "text2", "score": 0.9},
                {"text": "text3", "score": 0.7},
            ]
            
            reranked = service.rerank(
                query="test query",
                results=results,
                top_k=None,
            )
            
            # Should be sorted by rerank_score
            assert reranked[0]["rerank_score"] == 0.95
            assert reranked[1]["rerank_score"] == 0.9
            assert reranked[2]["rerank_score"] == 0.7
    
    def test_rerank_top_k(self, mock_config):
        """Test reranking with top_k limit."""
        with patch('services.reranker.CrossEncoder') as mock_cross_encoder:
            mock_model = Mock()
            mock_model.predict = Mock(return_value=[0.9, 0.7, 0.95, 0.6, 0.8])
            mock_cross_encoder.return_value = mock_model
            
            service = RerankingService(mock_config)
            
            results = [
                {"text": f"text{i}", "score": 0.5} for i in range(5)
            ]
            
            reranked = service.rerank(
                query="test query",
                results=results,
                top_k=3,
            )
            
            # Should only return top 3
            assert len(reranked) == 3
            assert reranked[0]["rerank_score"] >= reranked[1]["rerank_score"]
            assert reranked[1]["rerank_score"] >= reranked[2]["rerank_score"]
    
    def test_rerank_empty_results(self, mock_config):
        """Test reranking with empty results."""
        with patch('services.reranker.CrossEncoder') as mock_cross_encoder:
            service = RerankingService(mock_config)
            
            results = []
            reranked = service.rerank(query="test", results=results)
            
            assert len(reranked) == 0
    
    def test_rerank_disabled(self, mock_config):
        """Test reranking when disabled."""
        mock_config["enable_reranking"] = False
        service = RerankingService(mock_config)
        service.enabled = False
        
        results = [{"text": "text1", "score": 0.8}]
        reranked = service.rerank(query="test", results=results)
        
        # Should return original results unchanged
        assert reranked == results
    
    def test_compute_pairwise_scores(self, mock_config):
        """Test computing pairwise scores."""
        with patch('services.reranker.CrossEncoder') as mock_cross_encoder:
            mock_model = Mock()
            mock_model.predict = Mock(return_value=[0.9, 0.8, 0.7])
            mock_cross_encoder.return_value = mock_model
            
            service = RerankingService(mock_config)
            
            query = "test query"
            documents = ["doc1", "doc2", "doc3"]
            
            scores = service.compute_pairwise_scores(query, documents)
            
            assert len(scores) == 3
            assert all(isinstance(s, float) for s in scores)
            mock_model.predict.assert_called_once()
    
    def test_explain_score(self, mock_config):
        """Test explaining a reranking score."""
        with patch('services.reranker.CrossEncoder') as mock_cross_encoder:
            mock_model = Mock()
            mock_model.predict = Mock(return_value=[0.85])
            mock_cross_encoder.return_value = mock_model
            
            service = RerankingService(mock_config)
            
            explanation = service.explain_score(
                query="machine learning",
                document="Machine learning is amazing",
            )
            
            assert "score" in explanation
            assert explanation["score"] == 0.85
            assert "model" in explanation
            assert "explanation" in explanation
    
    def test_health_check(self, mock_config):
        """Test health check."""
        with patch('services.reranker.CrossEncoder') as mock_cross_encoder:
            mock_model = Mock()
            mock_model.predict = Mock(return_value=[0.5])
            mock_cross_encoder.return_value = mock_model
            
            service = RerankingService(mock_config)
            
            healthy = service.health_check()
            
            assert healthy is True
            mock_model.predict.assert_called_once()

