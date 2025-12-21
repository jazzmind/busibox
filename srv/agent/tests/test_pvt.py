"""
Post-Deployment Validation Tests (PVT) for Agent Service.

These tests run after deployment to verify the service is functioning correctly.
They are designed to be fast (<30 seconds total) and catch critical issues:
- Service health
- Database connectivity (PostgreSQL)
- Auth integration (AuthZ JWKS)
- LiteLLM connectivity
- Core API endpoints

Run with: pytest tests/test_pvt.py -v
Or: pytest -m pvt -v

IMPORTANT: These tests require REAL services - no mocks allowed.
"""

import os
import pytest
import httpx

# Read from environment (set by .env file)
SERVICE_PORT = os.getenv("PORT", "8080")
SERVICE_URL = os.getenv("AGENT_API_URL", f"http://localhost:{SERVICE_PORT}")

# Dependencies
AUTH_JWKS_URL = os.getenv("AUTH_JWKS_URL", "")
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")


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
class TestPVTDatabase:
    """Database connectivity tests."""
    
    @pytest.mark.asyncio
    async def test_postgres_connection(self):
        """PostgreSQL is reachable via DATABASE_URL."""
        if not DATABASE_URL:
            pytest.skip("DATABASE_URL not set")
        
        import asyncpg
        
        # Parse DATABASE_URL (format: postgresql://user:pass@host:port/db)
        conn = await asyncpg.connect(DATABASE_URL, timeout=5.0)
        try:
            result = await conn.fetchval("SELECT 1")
            assert result == 1
        finally:
            await conn.close()
    
    @pytest.mark.asyncio
    async def test_agent_tables_exist(self):
        """Agent service tables exist in database."""
        if not DATABASE_URL:
            pytest.skip("DATABASE_URL not set")
        
        import asyncpg
        
        conn = await asyncpg.connect(DATABASE_URL, timeout=5.0)
        try:
            # Check for essential tables
            tables = await conn.fetch("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name IN ('agent_definitions', 'conversations', 'runs')
            """)
            table_names = [t["table_name"] for t in tables]
            assert "agent_definitions" in table_names, "agent_definitions table missing"
            assert "conversations" in table_names, "conversations table missing"
        finally:
            await conn.close()


@pytest.mark.pvt
class TestPVTAuth:
    """Authentication tests - verify JWT infrastructure works."""
    
    @pytest.mark.asyncio
    async def test_protected_endpoint_requires_auth(self):
        """Protected endpoints reject unauthenticated requests."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/agents", timeout=5.0)
            assert resp.status_code in [401, 403], f"Expected 401/403, got {resp.status_code}"
    
    @pytest.mark.asyncio
    async def test_authz_jwks_reachable(self):
        """AuthZ JWKS endpoint is reachable from this service."""
        if not AUTH_JWKS_URL:
            pytest.skip("AUTH_JWKS_URL not set")
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(AUTH_JWKS_URL, timeout=5.0)
            assert resp.status_code == 200, f"JWKS returned {resp.status_code}"
            data = resp.json()
            assert "keys" in data
            assert len(data["keys"]) >= 1


@pytest.mark.pvt
class TestPVTDependencies:
    """Dependency tests - verify external services are reachable."""
    
    @pytest.mark.asyncio
    async def test_litellm_reachable(self):
        """LiteLLM is reachable for LLM calls."""
        if not LITELLM_BASE_URL:
            pytest.skip("LITELLM_BASE_URL not set")
        
        # LiteLLM health endpoint
        base = LITELLM_BASE_URL.rstrip("/v1").rstrip("/")
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/health", timeout=5.0)
            assert resp.status_code == 200, f"LiteLLM health check failed: {resp.status_code}"


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
    
    @pytest.mark.asyncio
    async def test_builtin_agents_available(self):
        """Built-in agents are seeded in the database."""
        # This endpoint requires auth, so we'll check via the database
        if not DATABASE_URL:
            pytest.skip("DATABASE_URL not set")
        
        import asyncpg
        
        conn = await asyncpg.connect(DATABASE_URL, timeout=5.0)
        try:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM agent_definitions WHERE is_builtin = true"
            )
            assert count >= 1, "No built-in agents found"
        finally:
            await conn.close()
