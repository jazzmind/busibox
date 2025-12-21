"""
Post-Deployment Validation Tests (PVT) for Ingest Service.

These tests run after deployment to verify the service is functioning correctly.
They are designed to be fast (<30 seconds total) and catch critical issues:
- Service health
- Database connectivity (PostgreSQL)
- Storage connectivity (MinIO)
- Auth integration (AuthZ JWKS)
- Encryption keystore connectivity
- Core API endpoints

Run with: pytest tests/test_pvt.py -v
Or: pytest -m pvt -v

IMPORTANT: These tests require REAL services - no mocks allowed.
IMPORTANT: All tests MUST pass - skipped tests indicate deployment issues.
"""

import os
import pytest
import httpx

# Read from environment (set by .env file)
API_PORT = os.getenv("API_PORT", "8002")
SERVICE_URL = f"http://localhost:{API_PORT}"

# Dependencies - REQUIRED
AUTHZ_JWKS_URL = os.getenv("AUTHZ_JWKS_URL", "")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
# MINIO_ENDPOINT format: host:port
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "")


def require_env(var_name: str, value: str) -> str:
    """Fail if environment variable is not set."""
    if not value:
        pytest.fail(f"Required environment variable {var_name} is not set. Check .env file.")
    return value


@pytest.mark.pvt
class TestPVTHealth:
    """Health check tests - verify service is running."""
    
    @pytest.mark.asyncio
    async def test_health_live(self):
        """Service responds to liveness probe."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health", timeout=5.0)
            assert resp.status_code == 200
            data = resp.json()
            # Accept healthy, ok, or degraded (service is up but optional dep might be down)
            assert data.get("status") in ["ok", "healthy", "degraded"]
    
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
    async def test_protected_endpoint_requires_auth(self):
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
        jwks_url = require_env("AUTHZ_JWKS_URL", AUTHZ_JWKS_URL)
        base_url = jwks_url.replace("/.well-known/jwks.json", "")
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}/health/live", timeout=5.0)
            assert resp.status_code == 200, f"AuthZ health check failed: {resp.status_code}"


@pytest.mark.pvt
class TestPVTAPI:
    """API tests - verify core endpoints work."""
    
    @pytest.mark.asyncio
    async def test_docs_endpoint_available(self):
        """API documentation is accessible at /docs."""
        async with httpx.AsyncClient() as client:
            # /docs is typically public even when API requires auth
            resp = await client.get(f"{SERVICE_URL}/docs", timeout=5.0, follow_redirects=True)
            # Should return HTML docs or redirect to docs
            assert resp.status_code in [200, 307], f"Docs endpoint failed: {resp.status_code}"
