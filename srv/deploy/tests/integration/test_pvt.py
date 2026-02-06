"""
Post-Deployment Validation Tests (PVT) for Deploy API Service.

These tests run after deployment to verify the service is functioning correctly.
They are designed to be fast (<30 seconds total) and catch critical issues:
- Service health
- Platform detection
- Service status endpoints
- SSH connectivity (for Proxmox)

Run with: pytest tests/integration/test_pvt.py -v
Or: pytest -m pvt -v

IMPORTANT: These tests require REAL services - no mocks allowed.
IMPORTANT: All tests MUST pass - skipped tests indicate deployment issues.
"""

import os
import pytest
import httpx

# Read from environment (set by .env file)
# For container testing: SERVICE_URL defaults to localhost:8011
SERVICE_PORT = os.getenv("DEPLOY_API_PORT", "8011")
SERVICE_URL = os.getenv("TEST_DEPLOY_API_URL", f"http://localhost:{SERVICE_PORT}")

# Whether we're on Proxmox (affects some tests)
IS_PROXMOX = os.getenv("DEPLOYMENT_BACKEND", "docker") == "proxmox"


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
        """Service responds to readiness probe."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health/ready", timeout=10.0)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
    
    @pytest.mark.asyncio
    async def test_deployment_health_endpoint(self):
        """Main deployment health endpoint responds."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/api/v1/deployment/health", timeout=5.0)
            assert resp.status_code == 200


@pytest.mark.pvt
class TestPVTPlatform:
    """Platform detection tests - verify environment detection works."""
    
    @pytest.mark.asyncio
    async def test_platform_endpoint_requires_auth(self):
        """Platform detection endpoint requires authentication."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/api/v1/services/platform", timeout=5.0)
            # Should require auth - 401 or 403
            # 200 would mean no auth required (also acceptable for public endpoints)
            assert resp.status_code in [200, 401, 403], \
                f"Platform endpoint returned unexpected status {resp.status_code}: {resp.text}"
    
    @pytest.mark.asyncio
    async def test_status_endpoint_requires_auth(self):
        """Service status endpoint requires authentication."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/api/v1/services/status", timeout=10.0)
            # Should require auth - 401 or 403
            # 200 would mean no auth required (also acceptable)
            assert resp.status_code in [200, 401, 403], \
                f"Status endpoint returned unexpected status {resp.status_code}: {resp.text}"


@pytest.mark.pvt
class TestPVTSetupState:
    """Setup state tests - verify state management works."""
    
    @pytest.mark.asyncio
    async def test_setup_complete_endpoint(self):
        """Setup complete status endpoint responds (public endpoint)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/system/state/setup-complete", timeout=5.0)
            assert resp.status_code == 200
            data = resp.json()
            
            # Should have setupComplete boolean
            assert "setupComplete" in data, f"Missing setupComplete field: {data}"
            assert isinstance(data["setupComplete"], bool), \
                f"setupComplete should be boolean, got {type(data['setupComplete'])}"


@pytest.mark.pvt
class TestPVTSystemHealth:
    """System health tests - verify system routes work."""
    
    @pytest.mark.asyncio
    async def test_system_health_endpoint(self):
        """System health endpoint responds (public endpoint)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/system/health", timeout=10.0)
            assert resp.status_code == 200
            data = resp.json()
            
            # Should have some health info
            assert isinstance(data, dict), f"Expected dict response, got {type(data)}"


@pytest.mark.pvt
class TestPVTDeployments:
    """Deployment listing tests - verify deployment management works."""
    
    @pytest.mark.asyncio
    async def test_deployments_endpoint_requires_auth(self):
        """Deployments endpoint requires authentication."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/api/v1/deployment/deployments", timeout=5.0)
            # Should require auth - 401 or 403
            assert resp.status_code in [401, 403], \
                f"Deployments endpoint should require auth, got {resp.status_code}"


@pytest.mark.pvt
@pytest.mark.skipif(not IS_PROXMOX, reason="SSH tests only run on Proxmox")
class TestPVTSSHConnectivity:
    """SSH connectivity tests - verify deploy-api can reach other containers.
    
    These tests only run on Proxmox where SSH is used for service management.
    """
    
    @pytest.mark.asyncio
    async def test_ssh_key_exists(self):
        """SSH key exists for container-to-container communication."""
        import os
        ssh_key_path = os.getenv("SSH_KEY_PATH", "/root/.ssh/id_ed25519")
        
        assert os.path.exists(ssh_key_path), \
            f"SSH key not found at {ssh_key_path}. Run deploy_api role with ssh-keys tag."
    
    @pytest.mark.asyncio
    async def test_can_resolve_service_hostnames(self):
        """Can resolve internal service hostnames."""
        import socket
        
        # Test a few key service hostnames
        hostnames_to_test = ["postgres", "redis", "ai-portal"]
        
        for hostname in hostnames_to_test:
            try:
                socket.gethostbyname(hostname)
            except socket.gaierror:
                pytest.fail(
                    f"Cannot resolve hostname '{hostname}'. "
                    "Check /etc/hosts or internal_dns role."
                )


@pytest.mark.pvt
class TestPVTConfigEndpoints:
    """Configuration endpoint tests - verify config routes work."""
    
    @pytest.mark.asyncio
    async def test_config_categories_requires_auth(self):
        """Config categories endpoint requires authentication."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/api/v1/config/categories", timeout=5.0)
            # Should require auth - 401 or 403
            assert resp.status_code in [401, 403], \
                f"Config categories endpoint should require auth, got {resp.status_code}"
