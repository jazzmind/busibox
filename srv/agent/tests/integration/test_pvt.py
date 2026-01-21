"""
Post-Deployment Validation Tests (PVT) for Agent Service.

These tests run after deployment to verify the service is functioning correctly.
They are designed to be fast (<30 seconds total) and catch critical issues:
- Service health
- Database connectivity (PostgreSQL)
- Auth integration (AuthZ token exchange AND authenticated access)
- LiteLLM connectivity
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
# Agent API runs on port 8000 by default
# For container testing: SERVICE_URL defaults to localhost:8000
# For local testing: Set AGENT_API_URL to the remote container's URL
SERVICE_PORT = os.getenv("PORT", "8000")
SERVICE_URL = os.getenv("AGENT_API_URL", f"http://localhost:{SERVICE_PORT}")

# AuthZ configuration - REQUIRED for token exchange
# Use bootstrap client (ai-portal) - the standard OAuth client for all services
AUTH_JWKS_URL = os.getenv("AUTH_JWKS_URL", "")
AUTHZ_BOOTSTRAP_CLIENT_ID = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
AUTHZ_BOOTSTRAP_CLIENT_SECRET = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")

# Dependencies - REQUIRED
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")


def require_env(var_name: str, value: str) -> str:
    """Fail if environment variable is not set."""
    if not value:
        pytest.fail(f"Required environment variable {var_name} is not set. Check .env file.")
    return value


def get_authz_base_url() -> str:
    """Extract AuthZ base URL from JWKS URL."""
    jwks = require_env("AUTH_JWKS_URL", AUTH_JWKS_URL)
    return jwks.replace("/.well-known/jwks.json", "")


@pytest.fixture(scope="module")
def access_token():
    """Get an access token for the agent API using bootstrap client credentials."""
    import httpx
    
    client_id = require_env("AUTHZ_BOOTSTRAP_CLIENT_ID", AUTHZ_BOOTSTRAP_CLIENT_ID)
    client_secret = require_env("AUTHZ_BOOTSTRAP_CLIENT_SECRET", AUTHZ_BOOTSTRAP_CLIENT_SECRET)
    authz_url = get_authz_base_url()
    
    # Token exchange using client credentials grant
    with httpx.Client() as client:
        resp = client.post(
            f"{authz_url}/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "audience": "agent-api",
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
            data = resp.json()
            assert data.get("status") == "ok"


@pytest.mark.pvt
class TestPVTDatabase:
    """Database connectivity tests."""
    
    @pytest.mark.asyncio
    async def test_postgres_connection(self):
        """PostgreSQL is reachable via DATABASE_URL."""
        db_url = require_env("DATABASE_URL", DATABASE_URL)
        
        # asyncpg needs the standard postgresql:// URL, not postgresql+asyncpg://
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        
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
        
        # asyncpg needs the standard postgresql:// URL, not postgresql+asyncpg://
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        
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
    async def test_agents_endpoint_rejects_unauthenticated(self):
        """Protected endpoints reject unauthenticated requests."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/agents", timeout=5.0)
            # 401/403 for auth rejection, 422 if validation happens before auth
            assert resp.status_code in [401, 403, 422], f"Expected 401/403/422, got {resp.status_code}"
    
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
    
    @pytest.mark.asyncio
    async def test_token_exchange_works(self, access_token):
        """Can obtain access token from AuthZ service."""
        assert access_token is not None
        assert len(access_token) > 0
    
    @pytest.mark.asyncio
    async def test_authenticated_request_succeeds(self, auth_headers):
        """Authenticated requests to /agents endpoint are accepted (not rejected as unauthorized)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SERVICE_URL}/agents",
                headers=auth_headers,
                timeout=5.0,
            )
            # PVT: We only care that auth passed - downstream errors are OK
            # Auth failures would return 401 or 403
            assert resp.status_code not in [401, 403], f"Auth rejected: {resp.status_code} - {resp.text}"


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
            # 200 for health, 401 if health endpoint requires auth (still reachable)
            assert resp.status_code in [200, 401], f"LiteLLM unreachable: {resp.status_code}"


@pytest.mark.pvt
class TestPVTAPI:
    """API tests - verify core endpoints work with authentication."""
    
    @pytest.mark.asyncio
    async def test_list_agents_with_auth(self, auth_headers):
        """Can list agents with valid authentication."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SERVICE_URL}/agents",
                headers=auth_headers,
                timeout=5.0,
            )
            assert resp.status_code == 200, f"List agents failed: {resp.status_code}"
            data = resp.json()
            assert isinstance(data, list)
    
    @pytest.mark.asyncio
    async def test_builtin_agents_available(self, auth_headers):
        """Built-in agents are available via the API (dynamically loaded from code)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SERVICE_URL}/agents",
                headers=auth_headers,
                timeout=5.0,
            )
            assert resp.status_code == 200, f"List agents failed: {resp.status_code}"
            agents = resp.json()
            
            # Check that we have built-in agents (loaded from app/agents/)
            builtin_agents = [a for a in agents if a.get("is_builtin", False)]
            assert len(builtin_agents) >= 1, f"No built-in agents found. Got {len(agents)} total agents."
            
            # Verify expected built-in agents exist
            agent_names = [a["name"] for a in builtin_agents]
            assert "chat-agent" in agent_names, f"Expected 'chat-agent' not found. Available: {agent_names}"
