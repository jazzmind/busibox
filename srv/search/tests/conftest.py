"""
Pytest configuration and fixtures for search API tests.

ALL tests use real tokens from authz service - NO MOCKS.
This ensures tests validate actual authentication behavior.

Required environment variables:
- AUTHZ_JWKS_URL: URL to authz JWKS endpoint
- AUTHZ_BOOTSTRAP_CLIENT_ID: OAuth client ID (default: ai-portal)
- AUTHZ_BOOTSTRAP_CLIENT_SECRET: OAuth client secret
- TEST_USER_ID: UUID of test user with roles assigned
"""

import os
import pytest
import asyncio
from typing import Dict, List
from unittest.mock import Mock, AsyncMock

import httpx


# =============================================================================
# Environment variables for auth
# =============================================================================

AUTHZ_JWKS_URL = os.getenv("AUTHZ_JWKS_URL", "")
AUTHZ_BOOTSTRAP_CLIENT_ID = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
AUTHZ_BOOTSTRAP_CLIENT_SECRET = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")
TEST_USER_ID = os.getenv("TEST_USER_ID", "")


def require_env(name: str) -> str:
    """Require an environment variable, fail if not set."""
    value = os.getenv(name, "")
    if not value:
        pytest.fail(f"Required environment variable {name} is not set. Check .env file.")
    return value


def get_authz_base_url() -> str:
    """Extract AuthZ base URL from JWKS URL."""
    if not AUTHZ_JWKS_URL:
        return ""
    return AUTHZ_JWKS_URL.replace("/.well-known/jwks.json", "")


# =============================================================================
# Auth fixtures - ALL use real tokens from authz
# =============================================================================

@pytest.fixture(autouse=True)
def set_auth_env(monkeypatch):
    """Set auth environment variables for tests."""
    monkeypatch.setenv("AUTHZ_ISSUER", "busibox-authz")
    monkeypatch.setenv("AUTHZ_AUDIENCE", "search-api")
    monkeypatch.setenv("JWT_ALGORITHMS", "RS256")
    if AUTHZ_JWKS_URL:
        monkeypatch.setenv("AUTHZ_JWKS_URL", AUTHZ_JWKS_URL)
    yield


@pytest.fixture
def sample_user_id():
    """Get the test user ID - fails if not configured."""
    return require_env("TEST_USER_ID")


def get_real_token(user_id: str, audience: str = "search-api") -> str:
    """
    Get a real access token from authz via token exchange.
    
    Args:
        user_id: The user ID to get a token for
        audience: The audience for the token (default: search-api)
    
    Returns:
        Access token string
    """
    authz_url = get_authz_base_url()
    if not authz_url:
        pytest.fail("AUTHZ_JWKS_URL not configured")
    
    client_secret = require_env("AUTHZ_BOOTSTRAP_CLIENT_SECRET")
    
    with httpx.Client() as client:
        resp = client.post(
            f"{authz_url}/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": AUTHZ_BOOTSTRAP_CLIENT_ID,
                "client_secret": client_secret,
                "requested_subject": user_id,
                "audience": audience,
            },
            timeout=10.0,
        )
        
        if resp.status_code != 200:
            pytest.fail(f"Failed to get access token: {resp.status_code} - {resp.text}")
        
        data = resp.json()
        if "access_token" not in data:
            pytest.fail(f"No access_token in response: {data}")
        
        return data["access_token"]


@pytest.fixture
def auth_header(sample_user_id: str):
    """
    Returns an Authorization header with a REAL token from authz.
    
    The token will have:
    - sub = TEST_USER_ID
    - aud = search-api
    - roles = user's actual roles from authz database
    - scope = user's actual scopes
    
    NO MOCKS - this validates real authentication behavior.
    """
    token = get_real_token(sample_user_id, "search-api")
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# Integration test fixtures (real auth) - same as unit tests, no mocks
# =============================================================================

@pytest.fixture(scope="module")
def real_access_token():
    """
    Get a REAL access token from authz.
    
    Uses token exchange (RFC 8693) to get a token with:
    - sub = TEST_USER_ID (real user for RLS)
    - aud = search-api (correct audience)
    - roles = user's roles from authz database
    """
    user_id = require_env("TEST_USER_ID")
    return get_real_token(user_id, "search-api")


@pytest.fixture(scope="module")
def real_auth_header(real_access_token):
    """Returns an Authorization header with a REAL token."""
    return {"Authorization": f"Bearer {real_access_token}"}


# =============================================================================
# Common fixtures
# =============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


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
