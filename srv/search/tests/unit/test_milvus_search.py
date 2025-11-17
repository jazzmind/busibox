"""
Unit tests for Milvus search service.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from services.milvus_search import MilvusSearchService


@pytest.mark.unit
class TestMilvusSearchService:
    """Test MilvusSearchService class."""
    
    def test_init(self, mock_config):
        """Test service initialization."""
        service = MilvusSearchService(mock_config)
        
        assert service.host == "localhost"
        assert service.port == 19530
        assert service.collection_name == "test_collection"
        assert not service.connected
    
    @patch('services.milvus_search.connections')
    @patch('services.milvus_search.Collection')
    def test_connect(self, mock_collection_class, mock_connections, mock_config):
        """Test connecting to Milvus."""
        service = MilvusSearchService(mock_config)
        mock_collection = Mock()
        mock_collection_class.return_value = mock_collection
        
        service.connect()
        
        assert service.connected
        mock_connections.connect.assert_called_once()
        mock_collection.load.assert_called_once()
    
    def test_keyword_search(self, mock_config):
        """Test BM25 keyword search."""
        service = MilvusSearchService(mock_config)
        service.connected = True
        
        # Mock collection
        mock_collection = Mock()
        mock_hits = Mock()
        mock_hits.entity.get = Mock(side_effect=lambda key: {
            "file_id": "file-123",
            "chunk_index": 0,
            "page_number": 1,
            "text": "test text",
            "metadata": {},
        }.get(key))
        mock_hits.score = 0.95
        
        mock_collection.search = Mock(return_value=[[mock_hits]])
        service.collection = mock_collection
        
        results = service.keyword_search(
            query="test query",
            user_id="user-123",
            top_k=10,
        )
        
        assert len(results) == 1
        assert results[0]["file_id"] == "file-123"
        assert results[0]["score"] == 0.95
        mock_collection.search.assert_called_once()
    
    def test_semantic_search(self, mock_config, sample_embedding):
        """Test dense vector semantic search."""
        service = MilvusSearchService(mock_config)
        service.connected = True
        
        # Mock collection
        mock_collection = Mock()
        mock_hits = Mock()
        mock_hits.entity.get = Mock(side_effect=lambda key: {
            "file_id": "file-456",
            "chunk_index": 2,
            "page_number": 3,
            "text": "semantic test",
            "metadata": {},
        }.get(key))
        mock_hits.score = 0.89
        
        mock_collection.search = Mock(return_value=[[mock_hits]])
        service.collection = mock_collection
        
        results = service.semantic_search(
            query_embedding=sample_embedding,
            user_id="user-123",
            top_k=10,
        )
        
        assert len(results) == 1
        assert results[0]["file_id"] == "file-456"
        assert results[0]["score"] == 0.89
    
    def test_hybrid_search(self, mock_config, sample_embedding):
        """Test hybrid search with RRF fusion."""
        service = MilvusSearchService(mock_config)
        service.connected = True
        
        # Mock semantic and keyword searches
        dense_results = [
            {"file_id": "file-1", "chunk_index": 0, "text": "text1", "score": 0.9, "page_number": 1, "metadata": {}},
            {"file_id": "file-2", "chunk_index": 0, "text": "text2", "score": 0.8, "page_number": 1, "metadata": {}},
        ]
        
        sparse_results = [
            {"file_id": "file-2", "chunk_index": 0, "text": "text2", "score": 0.95, "page_number": 1, "metadata": {}},
            {"file_id": "file-3", "chunk_index": 0, "text": "text3", "score": 0.7, "page_number": 1, "metadata": {}},
        ]
        
        service.semantic_search = Mock(return_value=dense_results)
        service.keyword_search = Mock(return_value=sparse_results)
        
        results = service.hybrid_search(
            query_embedding=sample_embedding,
            query_text="test query",
            user_id="user-123",
            top_k=3,
            rerank_k=10,
        )
        
        # Should fuse and return top 3
        assert len(results) <= 3
        assert all("score" in r for r in results)
        
        # file-2 should rank high (appears in both)
        file_2_scores = [r for r in results if r["file_id"] == "file-2"]
        assert len(file_2_scores) > 0
    
    def test_rrf_fusion(self, mock_config):
        """Test Reciprocal Rank Fusion algorithm."""
        service = MilvusSearchService(mock_config)
        
        dense_results = [
            {"file_id": "doc1", "chunk_index": 0, "text": "a", "score": 0.9, "dense_score": 0.9, "page_number": 1, "metadata": {}},
            {"file_id": "doc2", "chunk_index": 0, "text": "b", "score": 0.8, "dense_score": 0.8, "page_number": 1, "metadata": {}},
        ]
        
        sparse_results = [
            {"file_id": "doc2", "chunk_index": 0, "text": "b", "score": 0.95, "sparse_score": 0.95, "page_number": 1, "metadata": {}},
            {"file_id": "doc3", "chunk_index": 0, "text": "c", "score": 0.7, "sparse_score": 0.7, "page_number": 1, "metadata": {}},
        ]
        
        fused = service._fuse_results_rrf(
            dense_results=dense_results,
            sparse_results=sparse_results,
            dense_weight=0.7,
            sparse_weight=0.3,
            k=60,
        )
        
        # doc2 should rank first (appears in both)
        assert fused[0]["file_id"] == "doc2"
        assert "dense_score" in fused[0]
        assert "sparse_score" in fused[0]
    
    def test_get_document(self, mock_config):
        """Test retrieving a specific document."""
        service = MilvusSearchService(mock_config)
        service.connected = True
        
        mock_collection = Mock()
        mock_collection.query = Mock(return_value=[{
            "file_id": "file-123",
            "chunk_index": 5,
            "text": "document text",
            "text_dense": [0.1] * 1536,
        }])
        service.collection = mock_collection
        
        result = service.get_document(
            file_id="file-123",
            chunk_index=5,
            user_id="user-123",
        )
        
        assert result is not None
        assert result["file_id"] == "file-123"
        assert result["chunk_index"] == 5
    
    def test_health_check(self, mock_config):
        """Test health check."""
        service = MilvusSearchService(mock_config)
        service.connected = True
        
        mock_collection = Mock()
        mock_collection.query = Mock(return_value=[])
        service.collection = mock_collection
        
        healthy = service.health_check()
        
        assert healthy is True
        mock_collection.query.assert_called_once()

