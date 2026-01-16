"""
Pytest configuration and fixtures for search API tests.

ALL tests use real tokens from authz service - NO MOCKS.
This ensures tests validate actual authentication and authorization behavior.

Required environment variables:
- AUTHZ_JWKS_URL: URL to authz JWKS endpoint
- AUTHZ_ADMIN_TOKEN: Admin token for role/scope management
- AUTHZ_BOOTSTRAP_CLIENT_ID: OAuth client ID (default: ai-portal)
- AUTHZ_BOOTSTRAP_CLIENT_SECRET: OAuth client secret
- TEST_USER_ID: UUID of test user (should have NO roles by default)
"""

import os
import sys
import pytest
import asyncio
from pathlib import Path
from typing import Dict, List
from unittest.mock import Mock, AsyncMock

# Add shared testing library to path
# When deployed: /opt/search/test_utils/testing/
# When local: ../../test_utils/testing/
_test_utils_paths = [
    os.path.join(os.path.dirname(__file__), "..", "test_utils"),  # Deployed: /opt/search/test_utils
    os.path.join(os.path.dirname(__file__), "..", "..", "test_utils"),  # Local: srv/test_utils
]
for _path in _test_utils_paths:
    if os.path.exists(_path) and _path not in sys.path:
        sys.path.insert(0, _path)

# Import shared utilities
from testing.auth import AuthTestClient, auth_client, clean_test_user
from testing.fixtures import require_env, get_authz_base_url
from testing.environment import create_service_auth_fixture, load_env_files

# Load environment files before other imports
load_env_files(Path(__file__).parent.parent)


# =============================================================================
# Re-export shared fixtures
# =============================================================================

# These are imported from shared.testing.auth and made available to tests
__all__ = ["auth_client", "clean_test_user"]


# =============================================================================
# Environment setup - using shared service auth fixture factory
# =============================================================================

# Creates an autouse fixture that sets AUTHZ_AUDIENCE=search-api
set_auth_env = create_service_auth_fixture("search")


# =============================================================================
# Auth fixtures using shared library
# =============================================================================

@pytest.fixture
def sample_user_id():
    """Get the test user ID - fails if not configured."""
    return require_env("TEST_USER_ID")


@pytest.fixture
def auth_header(auth_client: AuthTestClient):
    """
    Get an Authorization header with a valid token.
    
    The test user should have NO roles by default.
    Use auth_client.add_role_to_user() to add roles for specific tests.
    
    Includes X-Test-Mode header to route API requests to test database.
    """
    token = auth_client.get_token(audience="search-api")
    return {
        "Authorization": f"Bearer {token}",
        "X-Test-Mode": "true",
    }


@pytest.fixture(scope="module")
def real_access_token():
    """Get a real access token for module-scoped tests."""
    client = AuthTestClient()
    return client.get_token(audience="search-api")


@pytest.fixture(scope="module")
def real_auth_header(real_access_token):
    """Returns an Authorization header with a real token.
    
    Includes X-Test-Mode header to route API requests to test database.
    """
    return {
        "Authorization": f"Bearer {real_access_token}",
        "X-Test-Mode": "true",
    }


# =============================================================================
# Common fixtures
# =============================================================================

# Event loop fixture is provided by testing.database module
from testing.database import event_loop  # noqa: F401


@pytest.fixture
def mock_config():
    """Mock configuration for tests."""
    return {
        "milvus_host": "localhost",
        "milvus_port": 19530,
        "milvus_collection": "test_collection",
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "test_db",
        "postgres_user": "test_user",
        "postgres_password": "test_pass",
        "embedding_service_url": "http://localhost:8002",
        "embedding_model": "bge-large-en-v1.5",
        "embedding_dim": 1024,
        "reranker_model": "BAAI/bge-reranker-v2-m3",
        "reranker_device": "cpu",
        "enable_reranking": True,
        "redis_host": None,
        "enable_caching": False,
        "highlight_fragment_size": 200,
        "highlight_num_fragments": 3,
        "highlight_pre_tag": "<mark>",
        "highlight_post_tag": "</mark>",
    }


@pytest.fixture
def sample_query():
    """Sample search query."""
    return "machine learning best practices"


@pytest.fixture
def sample_document_text():
    """Sample document text for testing."""
    return """
    Machine learning is a subset of artificial intelligence that focuses on developing
    algorithms and statistical models that enable computers to learn from data. Best
    practices in machine learning include proper data preprocessing, feature engineering,
    model selection, and validation techniques. It's important to avoid overfitting by
    using regularization methods and cross-validation. Deep learning, a subfield of
    machine learning, has shown remarkable results in image recognition and natural
    language processing tasks.
    """


@pytest.fixture
def sample_embedding():
    """Sample embedding vector (bge-large-en-v1.5 dimension)."""
    import random
    random.seed(42)
    return [random.random() for _ in range(1024)]


@pytest.fixture
def sample_search_results():
    """Sample search results from Milvus."""
    return [
        {
            "file_id": "file-123",
            "chunk_index": 0,
            "page_number": 1,
            "text": "Machine learning best practices include data preprocessing.",
            "score": 0.95,
            "metadata": {"document_type": "pdf"},
        },
        {
            "file_id": "file-123",
            "chunk_index": 1,
            "page_number": 1,
            "text": "Feature engineering is crucial for machine learning success.",
            "score": 0.89,
            "metadata": {"document_type": "pdf"},
        },
        {
            "file_id": "file-456",
            "chunk_index": 0,
            "page_number": 2,
            "text": "Deep learning models require large amounts of training data.",
            "score": 0.82,
            "metadata": {"document_type": "pdf"},
        },
    ]


@pytest.fixture
def mock_milvus_service(sample_search_results):
    """Mock Milvus service."""
    service = Mock()
    service.connect = Mock()
    service.connected = True
    service.keyword_search = Mock(return_value=sample_search_results)
    service.semantic_search = Mock(return_value=sample_search_results)
    service.hybrid_search = Mock(return_value=sample_search_results)
    service.get_document = Mock(return_value={
        "file_id": "file-123",
        "chunk_index": 0,
        "text": "Sample document text",
        "text_dense": [0.1] * 1024,
    })
    service.health_check = Mock(return_value=True)
    return service


@pytest.fixture
def mock_embedder(sample_embedding):
    """Mock embedding service."""
    embedder = Mock()
    embedder.embed_query = AsyncMock(return_value=sample_embedding)
    embedder.embed_batch = AsyncMock(return_value=[sample_embedding] * 3)
    embedder.health_check = AsyncMock(return_value=True)
    return embedder


@pytest.fixture
def mock_reranker(sample_search_results):
    """Mock reranking service."""
    reranker = Mock()
    
    # Rerank should return results sorted by rerank_score
    reranked = sample_search_results.copy()
    for i, result in enumerate(reranked):
        result["rerank_score"] = 0.9 - (i * 0.05)
    
    reranker.rerank = Mock(return_value=reranked)
    reranker.compute_pairwise_scores = Mock(return_value=[0.9, 0.85, 0.8])
    reranker.explain_score = Mock(return_value={
        "score": 0.9,
        "model": "BAAI/bge-reranker-v2-m3",
        "explanation": "High relevance",
    })
    reranker.health_check = Mock(return_value=True)
    return reranker


@pytest.fixture
def mock_highlighter():
    """Mock highlighting service."""
    highlighter = Mock()
    highlighter.highlight = Mock(return_value=[
        {
            "fragment": "...best practices for <mark>machine learning</mark>...",
            "score": 0.95,
            "start_offset": 50,
            "end_offset": 250,
        }
    ])
    return highlighter


@pytest.fixture
def mock_alignment_service():
    """Mock semantic alignment service."""
    service = Mock()
    service.compute_alignment = Mock(return_value={
        "query_tokens": ["machine", "learning", "best", "practices"],
        "document_tokens": ["machine", "learning", "algorithms", "data"],
        "alignment_matrix": [[0.9, 0.95, 0.6, 0.4]],
        "matched_spans": [
            {
                "query_token": "machine",
                "doc_span": "machine",
                "score": 0.9,
                "start": 0,
                "end": 7,
            },
            {
                "query_token": "learning",
                "doc_span": "learning",
                "score": 0.95,
                "start": 8,
                "end": 16,
            },
        ],
    })
    return service


@pytest.fixture
async def mock_postgres_conn():
    """Mock PostgreSQL connection."""
    conn = AsyncMock()
    
    # Mock fetch for file metadata
    conn.fetch = AsyncMock(return_value=[
        {"file_id": "file-123", "filename": "document1.pdf"},
        {"file_id": "file-456", "filename": "document2.pdf"},
    ])
    
    conn.close = AsyncMock()
    
    return conn


@pytest.fixture
def test_client():
    """FastAPI test client."""
    from fastapi.testclient import TestClient
    from api.main import app
    
    return TestClient(app)
