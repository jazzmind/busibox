"""
Post-Deployment Validation Tests (PVT) for AuthZ Service.

These tests run after deployment to verify the service is functioning correctly.
They are designed to be fast (<30 seconds total) and catch critical issues:
- Service health
- Database connectivity (PostgreSQL)
- JWT signing/validation infrastructure
- Token exchange flow
- Audit logging

Run with: pytest tests/integration/test_pvt.py -v
Or: pytest -m pvt -v

IMPORTANT: These tests require REAL services - no mocks allowed.
IMPORTANT: All tests MUST pass - skipped tests indicate deployment issues.

Authentication:
PVT tests use OAuth client credentials (bootstrap client) for authenticated operations.
"""

import os
import pytest
import httpx

# Read from environment (set by .env file)
# For container testing: SERVICE_URL defaults to localhost:8010
# For local testing: Set TEST_AUTHZ_URL to the remote container's URL
SERVICE_PORT = os.getenv("SERVICE_PORT", "8010")
SERVICE_URL = os.getenv("TEST_AUTHZ_URL", f"http://localhost:{SERVICE_PORT}")

# Bootstrap client credentials (for authenticated operations)
AUTHZ_BOOTSTRAP_CLIENT_ID = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
AUTHZ_BOOTSTRAP_CLIENT_SECRET = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")

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
    async def test_bootstrap_client_token_exchange(self):
        """Can get access token using bootstrap client credentials."""
        client_id = require_env("AUTHZ_BOOTSTRAP_CLIENT_ID", AUTHZ_BOOTSTRAP_CLIENT_ID)
        client_secret = require_env("AUTHZ_BOOTSTRAP_CLIENT_SECRET", AUTHZ_BOOTSTRAP_CLIENT_SECRET)
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERVICE_URL}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "audience": "agent-api",
                },
                timeout=10.0,
            )
            assert resp.status_code == 200, f"Token exchange failed: {resp.text}"
            data = resp.json()
            assert "access_token" in data, "No access_token in response"
            assert len(data["access_token"]) > 0
    
    @pytest.mark.asyncio
    async def test_bootstrap_client_multiple_audiences(self):
        """Bootstrap client can get tokens for different audiences."""
        client_id = require_env("AUTHZ_BOOTSTRAP_CLIENT_ID", AUTHZ_BOOTSTRAP_CLIENT_ID)
        client_secret = require_env("AUTHZ_BOOTSTRAP_CLIENT_SECRET", AUTHZ_BOOTSTRAP_CLIENT_SECRET)
        
        # Test multiple allowed audiences
        audiences = ["agent-api", "ingest-api", "search-api"]
        
        async with httpx.AsyncClient() as client:
            for audience in audiences:
                resp = await client.post(
                    f"{SERVICE_URL}/oauth/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "audience": audience,
                    },
                    timeout=10.0,
                )
                assert resp.status_code == 200, f"Token exchange failed for audience {audience}: {resp.text}"
                data = resp.json()
                assert "access_token" in data, f"No access_token for audience {audience}"


@pytest.mark.pvt
class TestPVTAudit:
    """Audit logging tests - verify audit endpoint works."""
    
    @pytest.mark.asyncio
    async def test_audit_log_endpoint_requires_auth(self):
        """Audit log endpoint requires authentication."""
        async with httpx.AsyncClient() as client:
            # Without auth should fail
            resp = await client.post(
                f"{SERVICE_URL}/audit/log",
                json={
                    "actor_id": "00000000-0000-0000-0000-000000000000",
                    "action": "test.action",
                    "resource_type": "test",
                },
                timeout=5.0,
            )
            # Should require authentication
            assert resp.status_code in [401, 403], f"Expected auth required, got {resp.status_code}"
    
    @pytest.mark.asyncio
    async def test_audit_log_security_event(self):
        """Audit log endpoint accepts security events without authentication."""
        import uuid
        
        # Security events (auth.login.*, auth.logout, etc.) can be logged without auth
        # This allows logging failed login attempts before authentication succeeds
        test_action = f"auth.login.pvt_test.{uuid.uuid4().hex[:8]}"
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERVICE_URL}/audit/log",
                json={
                    "actor_id": "00000000-0000-0000-0000-000000000001",
                    "action": test_action,
                    "resource_type": "session",
                    "event_type": "auth",
                    "details": {"test": True, "source": "pvt"},
                },
                timeout=5.0,
            )
            assert resp.status_code == 200, f"Audit log failed: {resp.text}"
            data = resp.json()
            assert "audit_log_id" in data, "Response should include audit_log_id"
    
    @pytest.mark.asyncio
    async def test_audit_logs_list_requires_auth(self):
        """Audit logs list endpoint requires authentication."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SERVICE_URL}/audit/logs?limit=5",
                timeout=5.0,
            )
            # Without authentication, should get 401
            assert resp.status_code == 401, f"Expected 401 without auth, got: {resp.status_code}"


@pytest.mark.pvt
class TestPVTLoginFlow:
    """Login flow tests - verify login initiation works."""
    
    @pytest.mark.asyncio
    async def test_login_initiate_endpoint(self):
        """Login initiation endpoint works for valid email."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERVICE_URL}/auth/login/initiate",
                json={"email": "test@example.com"},
                timeout=10.0,
            )
            assert resp.status_code == 200, f"Login initiate failed: {resp.text}"
            data = resp.json()
            # Should return magic link token and TOTP code
            assert "magic_link_token" in data, "Missing magic_link_token"
            assert "totp_code" in data, "Missing totp_code"
            assert "expires_in" in data, "Missing expires_in"
    
    @pytest.mark.asyncio
    async def test_login_initiate_invalid_email(self):
        """Login initiation rejects invalid email format."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERVICE_URL}/auth/login/initiate",
                json={"email": "invalid-email"},
                timeout=5.0,
            )
            assert resp.status_code == 400, f"Expected 400 for invalid email, got: {resp.status_code}"
