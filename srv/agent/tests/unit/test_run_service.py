"""Unit tests for run service."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import AgentDefinition, RunRecord
from app.schemas.auth import Principal
from app.services.run_service import create_run, get_agent_timeout


class TestGetAgentTimeout:
    """Test timeout calculation for agent tiers."""
    
    def test_simple_tier(self):
        assert get_agent_timeout("simple") == 30
    
    def test_complex_tier(self):
        assert get_agent_timeout("complex") == 300
    
    def test_batch_tier(self):
        assert get_agent_timeout("batch") == 1800
    
    def test_default_tier(self):
        assert get_agent_timeout("unknown") == 30
        assert get_agent_timeout() == 30


class TestCreateRun:
    """Test run creation and execution."""
    
    @pytest.mark.asyncio
    async def test_create_run_success(self, test_session: AsyncSession, test_agent: AgentDefinition, mock_principal: Principal):
        """Test successful run creation."""
        with patch("app.services.run_service.get_or_exchange_token") as mock_token, \
             patch("app.services.run_service.agent_registry") as mock_registry, \
             patch("app.services.run_service.BusiboxClient") as mock_client:
            
            # Setup mocks
            mock_token.return_value = MagicMock(access_token="test-token")
            mock_agent = AsyncMock()
            mock_result = MagicMock(output={"message": "test response"})
            mock_agent.run.return_value = mock_result
            mock_registry.get.return_value = mock_agent
            
            # Execute
            run = await create_run(
                session=test_session,
                principal=mock_principal,
                agent_id=test_agent.id,
                payload={"prompt": "test query"},
                scopes=["search.read"],
                purpose="test",
            )
            
            # Verify
            assert run.status == "succeeded"
            assert run.agent_id == test_agent.id
            assert run.input == {"prompt": "test query"}
            assert run.output == {"message": "test response"}
            assert run.created_by == mock_principal.sub
    
    @pytest.mark.asyncio
    async def test_create_run_agent_not_found(self, test_session: AsyncSession, mock_principal: Principal):
        """Test run creation with non-existent agent."""
        with patch("app.services.run_service.get_or_exchange_token") as mock_token, \
             patch("app.services.run_service.agent_registry") as mock_registry:
            
            mock_token.return_value = MagicMock(access_token="test-token")
            mock_registry.get.side_effect = KeyError("Agent not found")
            
            agent_id = uuid.uuid4()
            run = await create_run(
                session=test_session,
                principal=mock_principal,
                agent_id=agent_id,
                payload={"prompt": "test"},
                scopes=["search.read"],
                purpose="test",
            )
            
            assert run.status == "failed"
            assert "Agent not found" in run.output["error"]
    
    @pytest.mark.asyncio
    async def test_create_run_timeout(self, test_session: AsyncSession, test_agent: AgentDefinition, mock_principal: Principal):
        """Test run timeout handling."""
        import asyncio
        
        with patch("app.services.run_service.get_or_exchange_token") as mock_token, \
             patch("app.services.run_service.agent_registry") as mock_registry, \
             patch("app.services.run_service.BusiboxClient"):
            
            mock_token.return_value = MagicMock(access_token="test-token")
            mock_agent = AsyncMock()
            
            # Simulate timeout
            async def slow_run(*args, **kwargs):
                await asyncio.sleep(100)
            
            mock_agent.run = slow_run
            mock_registry.get.return_value = mock_agent
            
            run = await create_run(
                session=test_session,
                principal=mock_principal,
                agent_id=test_agent.id,
                payload={"prompt": "test"},
                scopes=["search.read"],
                purpose="test",
                agent_tier="simple",  # 30s timeout
            )
            
            assert run.status == "timeout"
            assert "timeout" in run.output["error"].lower()
            assert run.output["timeout"] == 30
    
    @pytest.mark.asyncio
    async def test_create_run_execution_error(self, test_session: AsyncSession, test_agent: AgentDefinition, mock_principal: Principal):
        """Test run execution error handling."""
        with patch("app.services.run_service.get_or_exchange_token") as mock_token, \
             patch("app.services.run_service.agent_registry") as mock_registry, \
             patch("app.services.run_service.BusiboxClient"):
            
            mock_token.return_value = MagicMock(access_token="test-token")
            mock_agent = AsyncMock()
            mock_agent.run.side_effect = ValueError("Tool call failed")
            mock_registry.get.return_value = mock_agent
            
            run = await create_run(
                session=test_session,
                principal=mock_principal,
                agent_id=test_agent.id,
                payload={"prompt": "test"},
                scopes=["search.read"],
                purpose="test",
            )
            
            assert run.status == "failed"
            assert "Tool call failed" in run.output["error"]
            assert run.output["error_type"] == "ValueError"

