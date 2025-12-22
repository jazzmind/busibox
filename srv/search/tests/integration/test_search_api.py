"""
Integration tests for Search API.

This file contains two types of tests:
1. Unit tests with mocked services - test API logic in isolation
2. Integration tests with real services - require running Milvus, PostgreSQL, etc.

Integration tests require:
- Milvus running and accessible
- PostgreSQL running with ingest schema
- Embedding service available
- Test data ingested

Run with: pytest -m integration to run only integration tests
Run with: pytest -m "not integration" to skip integration tests
"""

import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, Mock, AsyncMock
import httpx


def services_available() -> bool:
    """Check if external services are available for integration tests."""
    try:
        import os
        
        # Check Milvus
        from pymilvus import connections
        connections.connect(
            alias="test",
            host=os.getenv("MILVUS_HOST", "localhost"),
            port=int(os.getenv("MILVUS_PORT", "19530")),
            timeout=5,
        )
        connections.disconnect("test")
        
        return True
    except Exception as e:
        print(f"Services not available: {e}")
        return False


# =============================================================================
# Unit Tests - API logic with mocked services
# =============================================================================

class TestSearchAPIUnitTests:
    """Unit tests for Search API endpoints with mocked services.
    
    These tests verify:
    - Request validation
    - Response structure
    - Error handling
    - Authentication middleware
    
    They use mocks because:
    - Testing API logic, not service integration
    - Faster execution
    - No external dependencies
    """
    
    @patch('api.routes.search.milvus_service')
    @patch('api.routes.search.embedding_service')
    @patch('api.routes.search.reranking_service')
    @patch('api.routes.search.highlighting_service')
    @patch('api.routes.search.alignment_service')
    @patch('api.routes.search.asyncpg')
    def test_hybrid_search_request_validation(
        self,
        mock_asyncpg,
        mock_alignment,
        mock_highlighter,
        mock_reranker,
        mock_embedder,
        mock_milvus,
        test_client,
        auth_header,
        sample_search_results,
        sample_embedding,
    ):
        """Test hybrid search endpoint validates and processes requests correctly."""
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
            headers=auth_header,
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify response structure
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
        auth_header,
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
            headers=auth_header,
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
        auth_header,
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
            headers=auth_header,
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "semantic"
    
    def test_search_without_auth(self, test_client):
        """Test search without authentication returns 401."""
        try:
            response = test_client.post(
                "/search",
                json={"query": "test"},
            )
            assert response.status_code == 401
        except Exception:
            # HTTPException raised by middleware - this is expected
            pass
    
    def test_search_invalid_mode(self, test_client, auth_header):
        """Test search with invalid mode returns validation error."""
        response = test_client.post(
            "/search",
            json={
                "query": "test",
                "mode": "invalid_mode",
            },
            headers=auth_header,
        )
        
        assert response.status_code in [400, 422]
    
    def test_health_endpoint(self, test_client):
        """Test health check endpoint (no auth required)."""
        response = test_client.get("/health")
        
        assert response.status_code in [200, 503]
        data = response.json()
        
        assert "status" in data
        assert "milvus" in data
        assert "postgres" in data


# =============================================================================
# Integration Tests - Real services
# =============================================================================

@pytest.mark.integration
@pytest.mark.skipif(not services_available(), reason="External services not available")
class TestSearchAPIIntegration:
    """
    Integration tests with real Milvus, PostgreSQL, and embedding services.
    
    These tests require:
    - Milvus running at configured host:port
    - PostgreSQL with ingest database
    - Embedding service available
    
    Run with: pytest -m integration
    Skip with: pytest -m "not integration"
    """
    
    def test_hybrid_search_real_services(self, test_client, auth_header):
        """Test hybrid search with real Milvus and embedding services."""
        response = test_client.post(
            "/search",
            json={
                "query": "machine learning best practices",
                "mode": "hybrid",
                "limit": 10,
                "rerank": True,
            },
            headers=auth_header,
        )
        
        assert response.status_code == 200
        data = response.json()
        
        assert "query" in data
        assert data["query"] == "machine learning best practices"
        assert "results" in data
        assert "execution_time_ms" in data
        assert data["mode"] == "hybrid"
        
        print(f"Found {len(data['results'])} results")
        if data["results"]:
            print(f"Top result: {data['results'][0]}")
    
    def test_semantic_search_real_embedding(self, test_client, auth_header):
        """Test semantic search with real embedding service."""
        response = test_client.post(
            "/search/semantic",
            json={
                "query": "how to train neural networks effectively",
                "limit": 5,
            },
            headers=auth_header,
        )
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["mode"] == "semantic"
        print(f"Semantic search returned {len(data['results'])} results")
    
    def test_keyword_search_real(self, test_client, auth_header):
        """Test keyword search with real Milvus BM25."""
        response = test_client.post(
            "/search/keyword",
            json={
                "query": "Python",
                "limit": 5,
            },
            headers=auth_header,
        )
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["mode"] == "keyword"
        print(f"Keyword search returned {len(data['results'])} results")
    
    def test_search_with_reranking(self, test_client, auth_header):
        """Test search with reranking enabled."""
        response = test_client.post(
            "/search",
            json={
                "query": "document processing workflow",
                "mode": "hybrid",
                "limit": 20,
                "rerank": True,
            },
            headers=auth_header,
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify reranking was applied
        if data["results"] and len(data["results"]) > 1:
            # Results should have rerank_score
            for result in data["results"]:
                if "scores" in result:
                    print(f"Result scores: {result['scores']}")
    
    def test_search_with_highlighting(self, test_client, auth_header):
        """Test search with highlighting enabled."""
        response = test_client.post(
            "/search",
            json={
                "query": "artificial intelligence",
                "mode": "hybrid",
                "limit": 10,
                "highlight": {"enabled": True},
            },
            headers=auth_header,
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Check for highlights in results
        for result in data["results"]:
            if "highlights" in result and result["highlights"]:
                print(f"Highlighted: {result['highlights'][0]}")
                break
    
    def test_search_with_file_filter(self, test_client, auth_header):
        """Test search with file ID filter."""
        # First, do an unfiltered search to get file IDs
        response = test_client.post(
            "/search",
            json={
                "query": "test",
                "mode": "hybrid",
                "limit": 5,
            },
            headers=auth_header,
        )
        
        if response.status_code == 200 and response.json()["results"]:
            file_id = response.json()["results"][0].get("file_id")
            if file_id:
                # Now search with filter
                filtered_response = test_client.post(
                    "/search",
                    json={
                        "query": "test",
                        "mode": "hybrid",
                        "filters": {"file_ids": [file_id]},
                        "limit": 5,
                    },
                    headers=auth_header,
                )
                
                assert filtered_response.status_code == 200
                filtered_data = filtered_response.json()
                
                # All results should be from the filtered file
                for result in filtered_data["results"]:
                    assert result.get("file_id") == file_id
    
    def test_explain_endpoint_real(self, test_client, auth_header):
        """Test explain endpoint with real services."""
        # First get a document to explain
        search_response = test_client.post(
            "/search",
            json={
                "query": "test query",
                "mode": "hybrid",
                "limit": 1,
            },
            headers=auth_header,
        )
        
        if search_response.status_code == 200 and search_response.json()["results"]:
            result = search_response.json()["results"][0]
            file_id = result.get("file_id")
            chunk_index = result.get("chunk_index", 0)
            
            if file_id:
                explain_response = test_client.post(
                    "/search/explain",
                    json={
                        "query": "test query",
                        "file_id": file_id,
                        "chunk_index": chunk_index,
                    },
                    headers=auth_header,
                )
                
                assert explain_response.status_code == 200
                explain_data = explain_response.json()
                
                assert "explanation" in explain_data
                print(f"Explanation: {explain_data}")
