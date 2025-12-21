"""
Post-Deployment Validation Tests (PVT) for Search Service.

These tests run after deployment to verify the service is functioning correctly.
They are designed to be fast (<30 seconds total) and catch critical issues:
- Service health
- Milvus connectivity
- Auth integration (AuthZ JWKS)
- Core search endpoints

Run with: pytest tests/test_pvt.py -v
Or: pytest -m pvt -v

IMPORTANT: These tests require REAL services - no mocks allowed.
IMPORTANT: All tests MUST pass - skipped tests indicate deployment issues.
"""

import os
import pytest
import httpx

# Read from environment (set by .env file)
SERVICE_PORT = os.getenv("SERVICE_PORT", "8003")
SERVICE_URL = f"http://localhost:{SERVICE_PORT}"

# Dependencies - REQUIRED
AUTHZ_JWKS_URL = os.getenv("AUTHZ_JWKS_URL", "")
MILVUS_HOST = os.getenv("MILVUS_HOST", "")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")


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
    async def test_search_endpoint_requires_auth(self):
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
    """API tests - verify core endpoints work."""
    
    @pytest.mark.asyncio
    async def test_openapi_schema_available(self):
        """OpenAPI schema is accessible."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/openapi.json", timeout=5.0)
            assert resp.status_code == 200
            data = resp.json()
            assert "openapi" in data
            assert "paths" in data
            # Verify search endpoint is documented
            assert "/search" in data["paths"]
