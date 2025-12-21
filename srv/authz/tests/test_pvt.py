"""
Post-Deployment Validation Tests (PVT) for AuthZ Service.

These tests run after deployment to verify the service is functioning correctly.
They are designed to be fast (<30 seconds total) and catch critical issues:
- Service health
- Database connectivity
- JWT signing/validation
- Token exchange
- Keystore (encryption)

Run with: pytest tests/test_pvt.py -v
Or: pytest -m pvt -v
"""

import os
import pytest
import httpx

# Test against the local service (deployed on this container)
SERVICE_URL = os.getenv("TEST_AUTHZ_URL", "http://localhost:8010")
ADMIN_TOKEN = os.getenv("AUTHZ_ADMIN_TOKEN", "")


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
class TestPVTAuth:
    """Authentication tests - verify JWT infrastructure works."""
    
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
            assert jwk["kty"] == "RSA"
    
    @pytest.mark.asyncio
    async def test_admin_endpoint_requires_auth(self):
        """Admin endpoints reject unauthenticated requests."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/admin/roles", timeout=5.0)
            assert resp.status_code == 401


@pytest.mark.pvt
class TestPVTKeystore:
    """Keystore tests - verify encryption infrastructure works."""
    
    @pytest.fixture
    def admin_token(self):
        if not ADMIN_TOKEN:
            pytest.skip("AUTHZ_ADMIN_TOKEN not set")
        return ADMIN_TOKEN
    
    @pytest.mark.asyncio
    async def test_keystore_kek_creation(self, admin_token):
        """Can create a KEK for a role (tests master key is configured)."""
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
            assert resp.status_code in [400, 401, 403]

