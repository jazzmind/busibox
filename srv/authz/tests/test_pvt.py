"""
Post-Deployment Validation Tests (PVT) for AuthZ Service.

These tests run after deployment to verify the service is functioning correctly.
They are designed to be fast (<30 seconds total) and catch critical issues:
- Service health
- Database connectivity (PostgreSQL)
- JWT signing/validation infrastructure
- Token exchange flow
- Keystore (encryption with master key)

Run with: pytest tests/test_pvt.py -v
Or: pytest -m pvt -v

IMPORTANT: These tests require REAL services - no mocks allowed.
IMPORTANT: All tests MUST pass - skipped tests indicate deployment issues.
"""

import os
import pytest
import httpx

# Read from environment (set by .env file)
SERVICE_PORT = os.getenv("SERVICE_PORT", "8010")
SERVICE_URL = f"http://localhost:{SERVICE_PORT}"
ADMIN_TOKEN = os.getenv("AUTHZ_ADMIN_TOKEN", "")

# Database config - REQUIRED
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "busibox")
POSTGRES_USER = os.getenv("POSTGRES_USER", "")
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
            resp = await client.get(f"{SERVICE_URL}/health/live", timeout=5.0)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
    
    @pytest.mark.asyncio
    async def test_health_ready(self):
        """Service responds to readiness probe (includes DB check)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health/ready", timeout=10.0)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"


@pytest.mark.pvt
class TestPVTDatabase:
    """Database connectivity tests - verify PostgreSQL is accessible."""
    
    @pytest.mark.asyncio
    async def test_postgres_direct_connection(self):
        """Can connect directly to PostgreSQL."""
        host = require_env("POSTGRES_HOST", POSTGRES_HOST)
        password = require_env("POSTGRES_PASSWORD", POSTGRES_PASSWORD)
        user = require_env("POSTGRES_USER", POSTGRES_USER)
        
        import asyncpg
        
        conn = await asyncpg.connect(
            host=host,
            port=int(POSTGRES_PORT),
            database=POSTGRES_DB,
            user=user,
            password=password,
            timeout=5.0,
        )
        try:
            result = await conn.fetchval("SELECT 1")
            assert result == 1
        finally:
            await conn.close()
    
    @pytest.mark.asyncio
    async def test_authz_tables_exist(self):
        """AuthZ tables exist in database."""
        host = require_env("POSTGRES_HOST", POSTGRES_HOST)
        password = require_env("POSTGRES_PASSWORD", POSTGRES_PASSWORD)
        user = require_env("POSTGRES_USER", POSTGRES_USER)
        
        import asyncpg
        
        conn = await asyncpg.connect(
            host=host,
            port=int(POSTGRES_PORT),
            database=POSTGRES_DB,
            user=user,
            password=password,
            timeout=5.0,
        )
        try:
            # Check for essential tables
            tables = await conn.fetch("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name LIKE 'authz_%'
            """)
            table_names = [t["table_name"] for t in tables]
            assert "authz_roles" in table_names, "authz_roles table missing"
            assert "authz_oauth_clients" in table_names, "authz_oauth_clients table missing"
        finally:
            await conn.close()


@pytest.mark.pvt
class TestPVTAuth:
    """Authentication infrastructure tests - verify JWT works."""
    
    @pytest.mark.asyncio
    async def test_jwks_endpoint_available(self):
        """JWKS endpoint returns valid key set."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/.well-known/jwks.json", timeout=5.0)
            assert resp.status_code == 200
            data = resp.json()
            assert "keys" in data
            assert len(data["keys"]) >= 1
            # Verify JWK structure
            jwk = data["keys"][0]
            assert "kty" in jwk
            assert "kid" in jwk
            assert jwk["kty"] == "RSA", f"Expected RSA key, got {jwk['kty']}"
    
    @pytest.mark.asyncio
    async def test_admin_endpoint_requires_auth(self):
        """Admin endpoints reject unauthenticated requests."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/admin/roles", timeout=5.0)
            assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


@pytest.mark.pvt
class TestPVTKeystore:
    """Keystore tests - verify encryption infrastructure works."""
    
    @pytest.mark.asyncio
    async def test_keystore_kek_creation(self):
        """Can create a KEK for a role (tests AUTHZ_MASTER_KEY is configured)."""
        admin_token = require_env("AUTHZ_ADMIN_TOKEN", ADMIN_TOKEN)
        
        import uuid
        test_role_id = str(uuid.uuid4())
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERVICE_URL}/keystore/kek/ensure-for-role/{test_role_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
                timeout=10.0,
            )
            # This will fail with 500 if AUTHZ_MASTER_KEY is not set
            assert resp.status_code == 200, f"KEK creation failed: {resp.text}"
            data = resp.json()
            assert "kek_id" in data


@pytest.mark.pvt
class TestPVTTokenExchange:
    """Token exchange tests - verify OAuth2 flow works."""
    
    @pytest.mark.asyncio
    async def test_token_endpoint_exists(self):
        """Token endpoint responds (even if request is invalid)."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERVICE_URL}/oauth/token",
                data={"grant_type": "invalid"},
                timeout=5.0,
            )
            # Should get 400 (bad request) not 404 or 500
            assert resp.status_code in [400, 401, 403], f"Expected 400/401/403, got {resp.status_code}"
    
    @pytest.mark.asyncio
    async def test_bootstrap_client_exists(self):
        """Bootstrap OAuth client (ai-portal) exists."""
        admin_token = require_env("AUTHZ_ADMIN_TOKEN", ADMIN_TOKEN)
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SERVICE_URL}/admin/oauth-clients",
                headers={"Authorization": f"Bearer {admin_token}"},
                timeout=5.0,
            )
            assert resp.status_code == 200, f"Failed to list clients: {resp.text}"
            data = resp.json()
            client_ids = [c.get("client_id") for c in data]
            assert "ai-portal" in client_ids, "Bootstrap client 'ai-portal' not found"
