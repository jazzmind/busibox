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
"""

import os
import pytest
import httpx

# Test against the local service (deployed on this container)
SERVICE_URL = os.getenv("SEARCH_API_URL", "http://localhost:8003")
AUTHZ_JWKS_URL = os.getenv("AUTHZ_JWKS_URL", "")


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
    async def test_health_ready(self):
        """Service responds to readiness probe."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health/ready", timeout=10.0)
            assert resp.status_code == 200


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
            assert resp.status_code in [401, 403]
    
    @pytest.mark.asyncio
    async def test_authz_jwks_reachable(self):
        """AuthZ JWKS endpoint is reachable from this service."""
        if not AUTHZ_JWKS_URL:
            pytest.skip("AUTHZ_JWKS_URL not set")
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(AUTHZ_JWKS_URL, timeout=5.0)
            assert resp.status_code == 200
            data = resp.json()
            assert "keys" in data


@pytest.mark.pvt
class TestPVTDependencies:
    """Dependency tests - verify external services are reachable."""
    
    @pytest.mark.asyncio
    async def test_milvus_connection(self):
        """Milvus is reachable (via health check)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health/ready", timeout=10.0)
            assert resp.status_code == 200
            data = resp.json()
            # Health check should indicate Milvus is connected
            milvus_status = data.get("milvus") or data.get("vector_db")
            if milvus_status:
                assert milvus_status in ["connected", "ok", "healthy", True]


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

