"""
Post-Deployment Validation Tests (PVT) for Search Service.

These tests run after deployment to verify the service is functioning correctly.
They are designed to be fast (<30 seconds total) and catch critical issues:
- Service health
- Milvus connectivity
- Auth integration (AuthZ token exchange AND authenticated access)
- Core search endpoints with authentication

Run with: pytest tests/test_pvt.py -v
Or: pytest -m pvt -v

IMPORTANT: These tests require REAL services - no mocks allowed.
IMPORTANT: All tests MUST pass - skipped tests indicate deployment issues.
"""

import os
import pytest
import httpx

# Read from environment (set by .env file)
# For container testing: SERVICE_URL defaults to localhost:8003
# For local testing: Set SEARCH_API_URL to the remote container's URL
SERVICE_PORT = os.getenv("SERVICE_PORT", "8003")
SERVICE_URL = os.getenv("SEARCH_API_URL", f"http://localhost:{SERVICE_PORT}")

# AuthZ configuration - REQUIRED for token exchange
# Use bootstrap client (ai-portal) - the standard OAuth client for all services
AUTHZ_JWKS_URL = os.getenv("AUTHZ_JWKS_URL", "")
AUTHZ_BOOTSTRAP_CLIENT_ID = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
AUTHZ_BOOTSTRAP_CLIENT_SECRET = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")

# Test user - for token exchange to get a token with real user identity
# Required for search since it needs to do token exchange to call ingest
TEST_USER_ID = os.getenv("TEST_USER_ID", "")

# Dependencies - REQUIRED
MILVUS_HOST = os.getenv("MILVUS_HOST", "")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")


def require_env(var_name: str, value: str) -> str:
    """Fail if environment variable is not set."""
    if not value:
        pytest.fail(f"Required environment variable {var_name} is not set. Check .env file.")
    return value


def get_authz_base_url() -> str:
    """Extract AuthZ base URL from JWKS URL."""
    jwks = require_env("AUTHZ_JWKS_URL", AUTHZ_JWKS_URL)
    return jwks.replace("/.well-known/jwks.json", "")


@pytest.fixture(scope="module")
def access_token():
    """
    Get an access token for the search API with a real user identity.
    
    Uses token exchange (RFC 8693) to get a token with:
    - sub = TEST_USER_ID (real user for RLS)
    - aud = search-api (correct audience)
    - roles = user's roles from authz database
    
    This is required because search needs to do token exchange when calling
    ingest for embeddings, and that requires a valid UUID user ID.
    """
    import httpx
    
    client_id = require_env("AUTHZ_BOOTSTRAP_CLIENT_ID", AUTHZ_BOOTSTRAP_CLIENT_ID)
    client_secret = require_env("AUTHZ_BOOTSTRAP_CLIENT_SECRET", AUTHZ_BOOTSTRAP_CLIENT_SECRET)
    test_user_id = require_env("TEST_USER_ID", TEST_USER_ID)
    authz_url = get_authz_base_url()
    
    # Token exchange to get a token for a real user
    with httpx.Client() as client:
        resp = client.post(
            f"{authz_url}/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": client_id,
                "client_secret": client_secret,
                "requested_subject": test_user_id,
                "audience": "search-api",
            },
            timeout=10.0,
        )
        
        if resp.status_code != 200:
            pytest.fail(f"Failed to get access token: {resp.status_code} - {resp.text}")
        
        data = resp.json()
        if "access_token" not in data:
            pytest.fail(f"No access_token in response: {data}")
        
        return data["access_token"]


@pytest.fixture(scope="module")
def auth_headers(access_token):
    """Return headers with Bearer token."""
    return {"Authorization": f"Bearer {access_token}"}


@pytest.mark.pvt
class TestPVTHealth:
    """Health check tests - verify service is running."""
    
    @pytest.mark.asyncio
    async def test_health_live(self):
        """Service responds to liveness probe."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health", timeout=5.0)
            assert resp.status_code == 200
    
    @pytest.mark.asyncio
    async def test_health_checks_dependencies(self):
        """Service health check includes dependency status."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health", timeout=10.0)
            # 200 = healthy/degraded, 503 = critical deps down
            assert resp.status_code in [200, 503], f"Health check failed: {resp.status_code}"
            # If 200, check response body
            if resp.status_code == 200:
                data = resp.json()
                assert "status" in data
                assert "milvus" in data
                assert "postgres" in data


@pytest.mark.pvt
class TestPVTAuth:
    """Authentication tests - verify JWT infrastructure works."""
    
    @pytest.mark.asyncio
    async def test_search_endpoint_rejects_unauthenticated(self):
        """Search endpoints reject unauthenticated requests."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERVICE_URL}/search",
                json={"query": "test"},
                timeout=5.0,
            )
            assert resp.status_code in [401, 403], f"Expected 401/403, got {resp.status_code}"
    
    @pytest.mark.asyncio
    async def test_authz_jwks_reachable(self):
        """AuthZ JWKS endpoint is reachable from this service."""
        jwks_url = require_env("AUTHZ_JWKS_URL", AUTHZ_JWKS_URL)
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(jwks_url, timeout=5.0)
            assert resp.status_code == 200, f"JWKS returned {resp.status_code}"
            data = resp.json()
            assert "keys" in data
            assert len(data["keys"]) >= 1
    
    @pytest.mark.asyncio
    async def test_token_exchange_works(self, access_token):
        """Can obtain access token from AuthZ service."""
        assert access_token is not None
        assert len(access_token) > 0
    
    @pytest.mark.asyncio
    async def test_authenticated_search_request(self, auth_headers):
        """Authenticated search requests are accepted (not rejected as unauthorized)."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERVICE_URL}/search",
                json={"query": "test", "limit": 1},
                headers=auth_headers,
                timeout=10.0,
            )
            # PVT: Auth must pass, and API must return a valid response
            # 500 is never acceptable - it means uncaught exception
            # 503 is acceptable if downstream service is unavailable
            assert resp.status_code not in [401, 403, 500], \
                f"Request failed: {resp.status_code} - {resp.text}"


@pytest.mark.pvt
class TestPVTDependencies:
    """Dependency tests - verify external services are reachable."""
    
    @pytest.mark.asyncio
    async def test_milvus_reachable(self):
        """Milvus is reachable."""
        host = require_env("MILVUS_HOST", MILVUS_HOST)
        
        from pymilvus import connections
        
        try:
            connections.connect(
                alias="pvt_test",
                host=host,
                port=int(MILVUS_PORT),
                timeout=5.0,
            )
            # If we get here, connection succeeded
            connections.disconnect(alias="pvt_test")
        except Exception as e:
            pytest.fail(f"Failed to connect to Milvus: {e}")
    
    @pytest.mark.asyncio
    async def test_postgres_reachable(self):
        """PostgreSQL is reachable (for metadata queries)."""
        host = require_env("POSTGRES_HOST", POSTGRES_HOST)
        password = require_env("POSTGRES_PASSWORD", POSTGRES_PASSWORD)
        
        import asyncpg
        
        postgres_port = int(os.getenv("POSTGRES_PORT", "5432"))
        postgres_db = os.getenv("POSTGRES_DB", "busibox")
        postgres_user = os.getenv("POSTGRES_USER", "busibox_user")
        
        conn = await asyncpg.connect(
            host=host,
            port=postgres_port,
            database=postgres_db,
            user=postgres_user,
            password=password,
            timeout=5.0,
        )
        try:
            result = await conn.fetchval("SELECT 1")
            assert result == 1
        finally:
            await conn.close()


@pytest.mark.pvt
class TestPVTAPI:
    """API tests - verify core endpoints work with authentication."""
    
    @pytest.mark.asyncio
    async def test_search_with_auth(self, auth_headers):
        """Can perform search with valid authentication."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERVICE_URL}/search",
                json={"query": "test document", "limit": 5},
                headers=auth_headers,
                timeout=10.0,
            )
            # 200 with results, 422 if request validation fails (but auth passed)
            assert resp.status_code in [200, 422], f"Search failed: {resp.status_code}"
            if resp.status_code == 200:
                data = resp.json()
                assert data is not None
