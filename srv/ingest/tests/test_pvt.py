"""
Post-Deployment Validation Tests (PVT) for Ingest Service.

These tests run after deployment to verify the service is functioning correctly.
They are designed to be fast (<30 seconds total) and catch critical issues:
- Service health
- Database connectivity (PostgreSQL)
- Storage connectivity (MinIO)
- Auth integration (AuthZ JWKS)
- Core API endpoints

Run with: pytest tests/test_pvt.py -v
Or: pytest -m pvt -v
"""

import os
import pytest
import httpx

# Test against the local service (deployed on this container)
SERVICE_URL = os.getenv("INGEST_API_URL", "http://localhost:8000")
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
            data = resp.json()
            assert data.get("status") == "healthy" or data.get("healthy") is True


@pytest.mark.pvt
class TestPVTAuth:
    """Authentication tests - verify JWT infrastructure works."""
    
    @pytest.mark.asyncio
    async def test_protected_endpoint_requires_auth(self):
        """Protected endpoints reject unauthenticated requests."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/files", timeout=5.0)
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
    async def test_postgres_connection(self):
        """PostgreSQL is reachable (via health check)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health/ready", timeout=10.0)
            assert resp.status_code == 200
            data = resp.json()
            # Health check should indicate DB is connected
            db_status = data.get("database") or data.get("postgres") or data.get("db")
            if db_status:
                assert db_status in ["connected", "ok", "healthy", True]
    
    @pytest.mark.asyncio
    async def test_minio_connection(self):
        """MinIO is reachable (via health check)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health/ready", timeout=10.0)
            assert resp.status_code == 200
            data = resp.json()
            # Health check should indicate storage is connected
            storage_status = data.get("storage") or data.get("minio") or data.get("s3")
            if storage_status:
                assert storage_status in ["connected", "ok", "healthy", True]


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

