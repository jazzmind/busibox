"""
Integration tests for Search API.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, Mock, AsyncMock


@pytest.mark.integration
class TestSearchAPI:
    """Test Search API endpoints."""
    
    @patch('api.routes.search.milvus_service')
    @patch('api.routes.search.embedding_service')
    @patch('api.routes.search.reranking_service')
    @patch('api.routes.search.highlighting_service')
    @patch('api.routes.search.alignment_service')
    @patch('api.routes.search.asyncpg')
    def test_hybrid_search_endpoint(
        self,
        mock_asyncpg,
        mock_alignment,
        mock_highlighter,
        mock_reranker,
        mock_embedder,
        mock_milvus,
        test_client,
        sample_search_results,
        sample_embedding,
    ):
        """Test hybrid search endpoint."""
        # Setup mocks
        mock_embedder.embed_query = AsyncMock(return_value=sample_embedding)
        mock_milvus.hybrid_search = Mock(return_value=sample_search_results)
        mock_reranker.rerank = Mock(return_value=sample_search_results)
        mock_highlighter.highlight = Mock(return_value=[{
            "fragment": "<mark>test</mark>",
            "score": 0.9,
            "start_offset": 0,
            "end_offset": 50,
        }])
        mock_alignment.compute_alignment = Mock(return_value={
            "query_tokens": ["test"],
            "matched_spans": [],
        })
        
        # Mock database connection
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"file_id": "file-123", "filename": "test.pdf"},
        ])
        mock_conn.close = AsyncMock()
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        
        # Make request
        response = test_client.post(
            "/search",
            json={
                "query": "test query",
                "mode": "hybrid",
                "limit": 10,
                "rerank": True,
            },
            headers={"X-User-Id": "test-user"},
        )
        
        assert response.status_code == 200
        data = response.json()
        
        assert "query" in data
        assert "results" in data
        assert "execution_time_ms" in data
        assert data["mode"] == "hybrid"
    
    @patch('api.routes.search.milvus_service')
    @patch('api.routes.search.embedding_service')
    def test_keyword_search_endpoint(
        self,
        mock_embedder,
        mock_milvus,
        test_client,
        sample_search_results,
    ):
        """Test keyword-only search endpoint."""
        mock_milvus.keyword_search = Mock(return_value=sample_search_results[:1])
        
        response = test_client.post(
            "/search/keyword",
            json={
                "query": "specific term",
                "limit": 5,
            },
            headers={"X-User-Id": "test-user"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "keyword"
    
    @patch('api.routes.search.milvus_service')
    @patch('api.routes.search.embedding_service')
    def test_semantic_search_endpoint(
        self,
        mock_embedder,
        mock_milvus,
        test_client,
        sample_search_results,
        sample_embedding,
    ):
        """Test semantic-only search endpoint."""
        mock_embedder.embed_query = AsyncMock(return_value=sample_embedding)
        mock_milvus.semantic_search = Mock(return_value=sample_search_results[:1])
        
        response = test_client.post(
            "/search/semantic",
            json={
                "query": "conceptual question",
                "limit": 5,
            },
            headers={"X-User-Id": "test-user"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "semantic"
    
    def test_search_without_auth(self, test_client):
        """Test search without authentication."""
        response = test_client.post(
            "/search",
            json={"query": "test"},
        )
        
        assert response.status_code == 401
    
    def test_search_invalid_mode(self, test_client):
        """Test search with invalid mode."""
        response = test_client.post(
            "/search",
            json={
                "query": "test",
                "mode": "invalid_mode",
            },
            headers={"X-User-Id": "test-user"},
        )
        
        assert response.status_code in [400, 422]
    
    @patch('api.routes.search.milvus_service')
    @patch('api.routes.search.reranking_service')
    def test_explain_endpoint(
        self,
        mock_reranker,
        mock_milvus,
        test_client,
    ):
        """Test explain endpoint."""
        mock_milvus.get_document = Mock(return_value={
            "file_id": "file-123",
            "chunk_index": 0,
            "text": "test document",
            "text_dense": [0.1] * 1536,
        })
        mock_reranker.explain_score = Mock(return_value={
            "score": 0.9,
            "explanation": "High relevance",
        })
        
        response = test_client.post(
            "/search/explain",
            json={
                "query": "test",
                "file_id": "file-123",
                "chunk_index": 0,
            },
            headers={"X-User-Id": "test-user"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "explanation" in data
    
    def test_health_endpoint(self, test_client):
        """Test health check endpoint."""
        response = test_client.get("/health")
        
        # Health endpoint doesn't require auth
        assert response.status_code in [200, 503]
        data = response.json()
        
        assert "status" in data
        assert "milvus" in data
        assert "postgres" in data


@pytest.mark.integration
@pytest.mark.slow
class TestSearchFlow:
    """Test end-to-end search flow."""
    
    @patch('api.routes.search.milvus_service')
    @patch('api.routes.search.embedding_service')
    @patch('api.routes.search.reranking_service')
    @patch('api.routes.search.highlighting_service')
    @patch('api.routes.search.asyncpg')
    def test_complete_search_flow(
        self,
        mock_asyncpg,
        mock_highlighter,
        mock_reranker,
        mock_embedder,
        mock_milvus,
        test_client,
        sample_search_results,
        sample_embedding,
    ):
        """Test complete search flow from query to highlighted results."""
        # Setup complete pipeline
        mock_embedder.embed_query = AsyncMock(return_value=sample_embedding)
        mock_milvus.hybrid_search = Mock(return_value=sample_search_results)
        
        # Reranked results
        reranked = sample_search_results.copy()
        for i, r in enumerate(reranked):
            r["rerank_score"] = 0.95 - (i * 0.05)
        mock_reranker.rerank = Mock(return_value=reranked)
        
        # Highlights
        mock_highlighter.highlight = Mock(return_value=[{
            "fragment": "best practices for <mark>machine learning</mark>",
            "score": 0.9,
            "start_offset": 0,
            "end_offset": 50,
        }])
        
        # Database
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"file_id": "file-123", "filename": "ml-guide.pdf"},
            {"file_id": "file-456", "filename": "ai-book.pdf"},
        ])
        mock_conn.close = AsyncMock()
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        
        # Execute search
        response = test_client.post(
            "/search",
            json={
                "query": "machine learning best practices",
                "mode": "hybrid",
                "limit": 10,
                "rerank": True,
                "highlight": {"enabled": True},
            },
            headers={"X-User-Id": "test-user"},
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify all stages executed
        mock_embedder.embed_query.assert_called_once()
        mock_milvus.hybrid_search.assert_called_once()
        mock_reranker.rerank.assert_called_once()
        
        # Verify response structure
        assert len(data["results"]) > 0
        result = data["results"][0]
        assert "filename" in result
        assert "score" in result
        assert "scores" in result
        assert "highlights" in result
        
        # Verify highlights
        if result["highlights"]:
            assert "<mark>" in result["highlights"][0]["fragment"]


@pytest.mark.integration
class TestSearchFiltering:
    """Test search filtering capabilities."""
    
    @patch('api.routes.search.milvus_service')
    @patch('api.routes.search.embedding_service')
    @patch('api.routes.search.asyncpg')
    def test_file_id_filter(
        self,
        mock_asyncpg,
        mock_embedder,
        mock_milvus,
        test_client,
        sample_embedding,
    ):
        """Test filtering by file IDs."""
        mock_embedder.embed_query = AsyncMock(return_value=sample_embedding)
        mock_milvus.hybrid_search = Mock(return_value=[{
            "file_id": "file-123",
            "chunk_index": 0,
            "text": "filtered result",
            "score": 0.9,
            "page_number": 1,
            "metadata": {},
        }])
        
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"file_id": "file-123", "filename": "doc.pdf"},
        ])
        mock_conn.close = AsyncMock()
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        
        response = test_client.post(
            "/search",
            json={
                "query": "test",
                "mode": "hybrid",
                "filters": {
                    "file_ids": ["file-123"],
                },
            },
            headers={"X-User-Id": "test-user"},
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify filter was passed to Milvus
        call_args = mock_milvus.hybrid_search.call_args
        assert call_args[1]["filters"] == {"file_ids": ["file-123"]}

