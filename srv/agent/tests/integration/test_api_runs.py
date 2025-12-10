"""Integration tests for runs API endpoints."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.domain import AgentDefinition, RunRecord


class TestRunsAPI:
    """Test /runs endpoints."""
    
    @pytest.mark.asyncio
    async def test_create_run_endpoint(self, test_client: AsyncClient, test_agent: AgentDefinition):
        """Test POST /runs endpoint."""
        with patch("app.api.runs.get_principal") as mock_auth, \
             patch("app.services.run_service.get_or_exchange_token") as mock_token, \
             patch("app.services.run_service.agent_registry") as mock_registry, \
             patch("app.services.run_service.BusiboxClient"):
            
            # Setup mocks
            from app.schemas.auth import Principal
            mock_auth.return_value = Principal(
                sub="test-user",
                email="test@example.com",
                roles=["user"],
                scopes=["search.read"],
            )
            mock_token.return_value = MagicMock(access_token="test-token")
            mock_agent = AsyncMock()
            mock_result = MagicMock(output={"message": "success"})
            mock_agent.run.return_value = mock_result
            mock_registry.get.return_value = mock_agent
            
            # Execute
            response = await test_client.post(
                "/runs",
                json={
                    "agent_id": str(test_agent.id),
                    "input": {"prompt": "test query"}
                },
                headers={"Authorization": "Bearer test-token"}
            )
            
            # Verify
            assert response.status_code == 202
            data = response.json()
            assert data["status"] in ["running", "succeeded"]
            assert data["agent_id"] == str(test_agent.id)
            assert data["input"] == {"prompt": "test query"}
    
    @pytest.mark.asyncio
    async def test_get_run_endpoint(self, test_client: AsyncClient, test_run: RunRecord):
        """Test GET /runs/{id} endpoint."""
        with patch("app.api.runs.get_principal") as mock_auth:
            from app.schemas.auth import Principal
            mock_auth.return_value = Principal(
                sub=test_run.created_by,
                email="test@example.com",
                roles=["user"],
                scopes=[],
            )
            
            response = await test_client.get(
                f"/runs/{test_run.id}",
                headers={"Authorization": "Bearer test-token"}
            )
            
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == str(test_run.id)
            assert data["status"] == test_run.status
            assert data["input"] == test_run.input
            assert data["output"] == test_run.output
    
    @pytest.mark.asyncio
    async def test_get_run_not_found(self, test_client: AsyncClient):
        """Test GET /runs/{id} with non-existent run."""
        with patch("app.api.runs.get_principal") as mock_auth:
            from app.schemas.auth import Principal
            mock_auth.return_value = Principal(
                sub="test-user",
                email="test@example.com",
                roles=["user"],
                scopes=[],
            )
            
            fake_id = uuid.uuid4()
            response = await test_client.get(
                f"/runs/{fake_id}",
                headers={"Authorization": "Bearer test-token"}
            )
            
            assert response.status_code == 404
            assert "not found" in response.json()["detail"].lower()
    
    @pytest.mark.asyncio
    async def test_create_run_unauthorized(self, test_client: AsyncClient, test_agent: AgentDefinition):
        """Test POST /runs without auth token."""
        response = await test_client.post(
            "/runs",
            json={
                "agent_id": str(test_agent.id),
                "input": {"prompt": "test"}
            }
        )
        
        # Should fail without Authorization header
        assert response.status_code in [401, 422]  # 422 if header validation fails first

