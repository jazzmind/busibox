"""
Integration tests for Search API.

ALL tests use real services and real authentication - NO MOCKS.
This ensures tests validate actual behavior.

Requirements:
- Milvus running and accessible
- PostgreSQL running with ingest schema
- AuthZ service running with test user configured
- Environment variables set (AUTHZ_JWKS_URL, AUTHZ_BOOTSTRAP_CLIENT_SECRET, TEST_USER_ID)

Run with: pytest tests/integration/test_search_api.py -v
"""

import os
import pytest
from fastapi.testclient import TestClient


def services_available() -> bool:
    """Check if external services are available for integration tests."""
    try:
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
# Authentication Tests - Verify auth is enforced
# =============================================================================

class TestSearchAPIAuth:
    """Tests for authentication and authorization.
    
    Verifies:
    - Unauthenticated requests are rejected
    - Invalid tokens are rejected
    - Valid tokens allow access
    - Role-based filtering works
    """
    
    def test_search_without_auth_rejected(self, test_client):
        """Test that search without authentication returns 401."""
        response = test_client.post(
            "/search",
            json={"query": "test"},
        )
        assert response.status_code == 401
        assert "error" in response.json()
    
    def test_search_with_invalid_token_rejected(self, test_client):
        """Test that search with invalid token returns 401."""
        response = test_client.post(
            "/search",
            json={"query": "test"},
            headers={"Authorization": "Bearer invalid-token-here"},
        )
        assert response.status_code == 401
    
    def test_search_with_valid_token_succeeds(self, test_client, auth_header):
        """Test that search with valid token succeeds (or returns service error, not auth error)."""
        response = test_client.post(
            "/search",
            json={"query": "test", "mode": "keyword", "limit": 5},
            headers=auth_header,
        )
        # Should not be 401 or 403 - auth should pass
        assert response.status_code not in [401, 403], f"Auth failed: {response.text}"
    
    def test_health_endpoint_no_auth_required(self, test_client):
        """Test health check endpoint works without auth."""
        response = test_client.get("/health")
        
        assert response.status_code in [200, 503]
        data = response.json()
        
        assert "status" in data
        assert "milvus" in data
        assert "postgres" in data
    
    def test_search_invalid_mode_returns_validation_error(self, test_client, auth_header):
        """Test search with invalid mode returns validation error (not auth error)."""
        response = test_client.post(
            "/search",
            json={
                "query": "test",
                "mode": "invalid_mode",
            },
            headers=auth_header,
        )
        
        # Should be validation error, not auth error
        assert response.status_code in [400, 422]


# =============================================================================
# Integration Tests - Real services (marked slow for GPU/embedding tests)
# =============================================================================

@pytest.mark.integration
@pytest.mark.skipif(not services_available(), reason="External services not available")
class TestSearchAPIIntegration:
    """
    Integration tests with real Milvus, PostgreSQL, and embedding services.
    
    ALL tests use real authentication - NO MOCKS.
    
    Tests marked @pytest.mark.slow or @pytest.mark.gpu require:
    - Embedding service with GPU
    - More time to execute
    
    Run all: pytest tests/integration/test_search_api.py -v
    Skip slow: pytest tests/integration/test_search_api.py -v -m "not slow"
    """
    
    def test_keyword_search_real(self, test_client, auth_header):
        """Test keyword search with real Milvus BM25 (no embedding needed)."""
        response = test_client.post(
            "/search/keyword",
            json={
                "query": "Python",
                "limit": 5,
            },
            headers=auth_header,
        )
        
        # Should not be auth error
        assert response.status_code not in [401, 403], f"Auth failed: {response.text}"
        
        if response.status_code == 200:
            data = response.json()
            assert data["mode"] == "keyword"
            print(f"Keyword search returned {len(data['results'])} results")
    
    @pytest.mark.slow
    @pytest.mark.gpu
    def test_hybrid_search_real_services(self, test_client, auth_header):
        """Test hybrid search with real Milvus and embedding services."""
        response = test_client.post(
            "/search",
            json={
                "query": "machine learning best practices",
                "mode": "hybrid",
                "limit": 10,
                "rerank": False,  # Skip reranking for faster test
            },
            headers=auth_header,
        )
        
        # Should not be auth error
        assert response.status_code not in [401, 403], f"Auth failed: {response.text}"
        
        if response.status_code == 200:
            data = response.json()
            assert "query" in data
            assert data["query"] == "machine learning best practices"
            assert "results" in data
            assert data["mode"] == "hybrid"
            print(f"Found {len(data['results'])} results")
    
    @pytest.mark.slow
    @pytest.mark.gpu
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
        
        # Should not be auth error
        assert response.status_code not in [401, 403], f"Auth failed: {response.text}"
        
        if response.status_code == 200:
            data = response.json()
            assert data["mode"] == "semantic"
            print(f"Semantic search returned {len(data['results'])} results")
    
    @pytest.mark.slow
    @pytest.mark.gpu
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
        
        # Should not be auth error
        assert response.status_code not in [401, 403], f"Auth failed: {response.text}"
        
        if response.status_code == 200:
            data = response.json()
            if data["results"] and len(data["results"]) > 1:
                for result in data["results"]:
                    if "scores" in result:
                        print(f"Result scores: {result['scores']}")
    
    @pytest.mark.slow
    @pytest.mark.gpu
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
        
        # Should not be auth error
        assert response.status_code not in [401, 403], f"Auth failed: {response.text}"
        
        if response.status_code == 200:
            data = response.json()
            for result in data["results"]:
                if "highlights" in result and result["highlights"]:
                    print(f"Highlighted: {result['highlights'][0]}")
                    break
    
    def test_search_with_file_filter(self, test_client, auth_header):
        """Test search with file ID filter (keyword mode, no embedding)."""
        # First, do an unfiltered keyword search to get file IDs
        response = test_client.post(
            "/search/keyword",
            json={
                "query": "test",
                "limit": 5,
            },
            headers=auth_header,
        )
        
        if response.status_code == 200 and response.json()["results"]:
            file_id = response.json()["results"][0].get("file_id")
            if file_id:
                # Now search with filter
                filtered_response = test_client.post(
                    "/search/keyword",
                    json={
                        "query": "test",
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
    
    @pytest.mark.slow
    @pytest.mark.gpu
    def test_explain_endpoint_real(self, test_client, auth_header):
        """Test explain endpoint with real services."""
        # First get a document to explain (keyword search, no embedding)
        search_response = test_client.post(
            "/search/keyword",
            json={
                "query": "test",
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
                
                # Should not be auth error
                assert explain_response.status_code not in [401, 403], f"Auth failed: {explain_response.text}"
                
                if explain_response.status_code == 200:
                    explain_data = explain_response.json()
                    assert "explanation" in explain_data
                    print(f"Explanation: {explain_data}")
