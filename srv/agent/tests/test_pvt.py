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
IMPORTANT: All tests MUST pass - skipped tests indicate deployment issues.
"""

import os
import pytest
import httpx

# Read from environment (set by .env file)
SERVICE_PORT = os.getenv("PORT", "8080")
SERVICE_URL = f"http://localhost:{SERVICE_PORT}"

# Dependencies - REQUIRED
AUTH_JWKS_URL = os.getenv("AUTH_JWKS_URL", "")
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")


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
            assert data.get("status") == "ok"


@pytest.mark.pvt
class TestPVTDatabase:
    """Database connectivity tests."""
    
    @pytest.mark.asyncio
    async def test_postgres_connection(self):
        """PostgreSQL is reachable via DATABASE_URL."""
        db_url = require_env("DATABASE_URL", DATABASE_URL)
        
        import asyncpg
        
        conn = await asyncpg.connect(db_url, timeout=5.0)
        try:
            result = await conn.fetchval("SELECT 1")
            assert result == 1
        finally:
            await conn.close()
    
    @pytest.mark.asyncio
    async def test_agent_tables_exist(self):
        """Agent service tables exist in database."""
        db_url = require_env("DATABASE_URL", DATABASE_URL)
        
        import asyncpg
        
        conn = await asyncpg.connect(db_url, timeout=5.0)
        try:
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
        jwks_url = require_env("AUTH_JWKS_URL", AUTH_JWKS_URL)
        
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
    async def test_litellm_reachable(self):
        """LiteLLM is reachable for LLM calls."""
        litellm_url = require_env("LITELLM_BASE_URL", LITELLM_BASE_URL)
        
        # LiteLLM health endpoint - strip /v1 if present
        base = litellm_url.rstrip("/v1").rstrip("/")
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
        db_url = require_env("DATABASE_URL", DATABASE_URL)
        
        import asyncpg
        
        conn = await asyncpg.connect(db_url, timeout=5.0)
        try:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM agent_definitions WHERE is_builtin = true"
            )
            assert count >= 1, "No built-in agents found"
        finally:
            await conn.close()
