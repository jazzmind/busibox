"""
Post-Deployment Validation Tests (PVT) for Ingest Service.

These tests run after deployment to verify the service is functioning correctly.
They are designed to be fast (<30 seconds total) and catch critical issues:
- Service health
- Database connectivity (PostgreSQL)
- Storage connectivity (MinIO)
- Auth integration (AuthZ token exchange AND authenticated access)
- Encryption keystore connectivity
- Core API endpoints with authentication

Run with: pytest tests/test_pvt.py -v
Or: pytest -m pvt -v

IMPORTANT: These tests require REAL services - no mocks allowed.
IMPORTANT: All tests MUST pass - skipped tests indicate deployment issues.
"""

import os
import pytest
import httpx

# Read from environment (set by .env file)
# For container testing: SERVICE_URL defaults to localhost:8002
# For local testing: Set INGEST_API_URL to the remote container's URL
API_PORT = os.getenv("API_PORT", "8002")
SERVICE_URL = os.getenv("INGEST_API_URL", f"http://localhost:{API_PORT}")

# AuthZ configuration - REQUIRED for token exchange
# Use bootstrap client (ai-portal) - the standard OAuth client for all services
AUTHZ_JWKS_URL = os.getenv("AUTHZ_JWKS_URL", "")
AUTHZ_BOOTSTRAP_CLIENT_ID = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
AUTHZ_BOOTSTRAP_CLIENT_SECRET = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")

# Dependencies - REQUIRED
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "")


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
def auth_client():
    """Get an AuthTestClient for user-scoped token exchange (Zero Trust)."""
    from testing import AuthTestClient
    
    client = AuthTestClient()
    # Note: In Zero Trust, test user must already exist (created by bootstrap-test-credentials)
    # We don't call ensure_test_user_exists() because that requires admin token
    yield client
    client.cleanup()


@pytest.fixture(scope="module")
def access_token(auth_client):
    """Get an access token for the ingest API using user-scoped token exchange."""
    return auth_client.get_token(audience="ingest-api")


@pytest.fixture(scope="module")
def auth_headers(auth_client):
    """Return headers with Bearer token and X-Test-Mode."""
    return auth_client.get_auth_header(audience="ingest-api")


@pytest.mark.pvt
class TestPVTHealth:
    """Health check tests - verify service is running."""
    
    @pytest.mark.asyncio
    async def test_health_live(self):
        """Service responds to liveness probe."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health", timeout=5.0)
            # Accept 200 (healthy) or 503 (degraded - service up but optional dep might be down)
            assert resp.status_code in [200, 503], f"Health check failed: {resp.status_code}"
            data = resp.json()
            # Service should return status even when degraded
            assert "status" in data
    
    @pytest.mark.asyncio
    async def test_health_checks_dependencies(self):
        """Service health check includes dependency status."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health", timeout=10.0)
            # 200 = healthy/degraded, 503 = critical deps down
            assert resp.status_code in [200, 503], f"Health check failed: {resp.status_code}"
            data = resp.json()
            # Should report status and have checks for dependencies
            assert "status" in data
            assert "checks" in data
            # Critical dependencies should be checked
            assert "postgres" in data["checks"]
            assert "minio" in data["checks"]


@pytest.mark.pvt
class TestPVTAuth:
    """Authentication tests - verify JWT infrastructure works."""
    
    @pytest.mark.asyncio
    async def test_protected_endpoint_rejects_unauthenticated(self):
        """Protected endpoints reject unauthenticated requests."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/files", timeout=5.0)
            assert resp.status_code in [401, 403], f"Expected 401/403, got {resp.status_code}"
    
    @pytest.mark.asyncio
    async def test_authz_jwks_reachable(self):
        """AuthZ JWKS endpoint is reachable from this service."""
        jwks_url = require_env("AUTHZ_JWKS_URL", AUTHZ_JWKS_URL)
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(jwks_url, timeout=5.0)
            assert resp.status_code == 200, f"JWKS endpoint returned {resp.status_code}"
            data = resp.json()
            assert "keys" in data, "JWKS response missing 'keys'"
            assert len(data["keys"]) >= 1, "No keys in JWKS"
    
    @pytest.mark.asyncio
    async def test_token_exchange_works(self, access_token):
        """Can obtain access token from AuthZ service."""
        # If we got here, the access_token fixture succeeded
        assert access_token is not None
        assert len(access_token) > 0
    
    @pytest.mark.asyncio
    async def test_authenticated_request_succeeds(self, auth_headers):
        """Authenticated requests to /files endpoint succeed."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SERVICE_URL}/files",
                headers=auth_headers,
                timeout=5.0,
            )
            # Should get 200 (success) or 404 (no files) - not 401/403
            assert resp.status_code in [200, 404], f"Auth failed: {resp.status_code} - {resp.text}"


@pytest.mark.pvt
class TestPVTDependencies:
    """Dependency tests - verify external services are reachable."""
    
    @pytest.mark.asyncio
    async def test_postgres_reachable(self):
        """PostgreSQL is reachable."""
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
    
    @pytest.mark.asyncio
    async def test_minio_reachable(self):
        """MinIO is reachable."""
        endpoint = require_env("MINIO_ENDPOINT", MINIO_ENDPOINT)
        
        # Just check the MinIO health endpoint
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://{endpoint}/minio/health/live",
                timeout=5.0,
            )
            assert resp.status_code == 200, f"MinIO health check failed: {resp.status_code}"
    
    @pytest.mark.asyncio
    async def test_authz_service_reachable(self):
        """AuthZ service is reachable (for encryption keystore)."""
        authz_url = get_authz_base_url()
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{authz_url}/health/live", timeout=5.0)
            assert resp.status_code == 200, f"AuthZ health check failed: {resp.status_code}"


@pytest.mark.pvt
class TestPVTAPI:
    """API tests - verify core endpoints work with authentication."""
    
    @pytest.mark.asyncio
    async def test_files_list_with_auth(self, auth_headers):
        """Can list files with valid authentication."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SERVICE_URL}/files",
                headers=auth_headers,
                timeout=5.0,
            )
            # 200 with files or empty list, 404 if endpoint returns that for empty
            assert resp.status_code in [200, 404], f"Files list failed: {resp.status_code}"
            if resp.status_code == 200:
                # Should return JSON (list or object with files)
                data = resp.json()
                assert data is not None
