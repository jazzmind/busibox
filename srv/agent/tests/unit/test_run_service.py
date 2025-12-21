"""
Unit tests for run service.

These tests focus on error handling and edge cases that are difficult or 
expensive to test with real services:
- Timeout behavior (would require waiting 30+ seconds)
- Agent not found error handling
- Execution error handling
- Pure utility functions

For tests that verify successful execution with real agents and services,
see: tests/integration/test_api_runs.py
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import AgentDefinition
from app.schemas.auth import Principal
from app.services.run_service import (
    add_run_event,
    create_run,
    get_agent_memory_limit,
    get_agent_timeout,
    get_run_by_id,
    list_runs,
)


class TestGetAgentTimeout:
    """Test timeout calculation for agent tiers. No mocks needed - pure logic."""
    
    def test_simple_tier(self):
        assert get_agent_timeout("simple") == 30
    
    def test_complex_tier(self):
        assert get_agent_timeout("complex") == 300
    
    def test_batch_tier(self):
        assert get_agent_timeout("batch") == 1800
    
    def test_default_tier(self):
        assert get_agent_timeout("unknown") == 30
        assert get_agent_timeout() == 30


class TestGetAgentMemoryLimit:
    """Test memory limit calculation for agent tiers. No mocks needed - pure logic."""
    
    def test_simple_tier(self):
        assert get_agent_memory_limit("simple") == 512
    
    def test_complex_tier(self):
        assert get_agent_memory_limit("complex") == 2048
    
    def test_batch_tier(self):
        assert get_agent_memory_limit("batch") == 4096
    
    def test_default_tier(self):
        assert get_agent_memory_limit("unknown") == 512


class TestAddRunEvent:
    """Test run event tracking. No mocks needed - pure logic."""
    
    @pytest.mark.asyncio
    async def test_add_event_to_empty_list(self, test_session: AsyncSession, test_agent: AgentDefinition, mock_principal: Principal):
        from app.models.domain import RunRecord
        
        run_record = RunRecord(
            agent_id=test_agent.id,
            status="pending",
            input={"prompt": "test"},
            created_by=mock_principal.sub,
            events=[],
        )
        
        add_run_event(run_record, "test_event", data={"foo": "bar"})
        
        assert len(run_record.events) == 1
        assert run_record.events[0]["type"] == "test_event"
        assert run_record.events[0]["data"]["foo"] == "bar"
        assert "timestamp" in run_record.events[0]
    
    @pytest.mark.asyncio
    async def test_add_event_with_error(self, test_session: AsyncSession, test_agent: AgentDefinition, mock_principal: Principal):
        from app.models.domain import RunRecord
        
        run_record = RunRecord(
            agent_id=test_agent.id,
            status="pending",
            input={"prompt": "test"},
            created_by=mock_principal.sub,
            events=[],
        )
        
        add_run_event(run_record, "error", error="Something went wrong")
        
        assert len(run_record.events) == 1
        assert run_record.events[0]["type"] == "error"
        assert run_record.events[0]["error"] == "Something went wrong"


class TestGetRunById:
    """Test run retrieval. Uses real database, no mocks."""
    
    @pytest.mark.asyncio
    async def test_get_existing_run(self, test_session: AsyncSession, test_run):
        """Test retrieving an existing run."""
        run = await get_run_by_id(test_session, test_run.id)
        assert run is not None
        assert run.id == test_run.id
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_run(self, test_session: AsyncSession):
        """Test retrieving a non-existent run returns None."""
        run = await get_run_by_id(test_session, uuid.uuid4())
        assert run is None


class TestListRuns:
    """Test run listing. Uses real database, no mocks."""
    
    @pytest.mark.asyncio
    async def test_list_runs_empty(self, test_session: AsyncSession):
        """Test listing runs when none exist."""
        runs = await list_runs(test_session)
        assert isinstance(runs, list)
    
    @pytest.mark.asyncio
    async def test_list_runs_with_filter(self, test_session: AsyncSession, test_run, mock_principal):
        """Test listing runs with user filter."""
        runs = await list_runs(test_session, created_by=mock_principal.sub)
        assert len(runs) >= 1
        assert all(r.created_by == mock_principal.sub for r in runs)


class TestCreateRunErrorHandling:
    """
    Test run creation error handling.
    
    These tests use mocks because they test error scenarios that are:
    1. Difficult to reliably trigger with real services
    2. Would require waiting for timeouts (30+ seconds)
    3. Would require creating specific failure conditions
    
    Integration tests for successful runs are in tests/integration/test_api_runs.py
    """
    
    @pytest.mark.asyncio
    async def test_create_run_invalid_payload(self, test_session: AsyncSession, test_agent: AgentDefinition, mock_principal: Principal):
        """Test run creation with invalid payload (no prompt)."""
        with pytest.raises(ValueError, match="prompt"):
            await create_run(
                session=test_session,
                principal=mock_principal,
                agent_id=test_agent.id,
                payload={},  # Missing prompt
                scopes=["search.read"],
                purpose="test",
            )
    
    @pytest.mark.asyncio
    async def test_create_run_invalid_tier(self, test_session: AsyncSession, test_agent: AgentDefinition, mock_principal: Principal):
        """Test run creation with invalid agent tier."""
        with pytest.raises(ValueError, match="Invalid agent_tier"):
            await create_run(
                session=test_session,
                principal=mock_principal,
                agent_id=test_agent.id,
                payload={"prompt": "test"},
                scopes=["search.read"],
                purpose="test",
                agent_tier="invalid_tier",
            )
    
    @pytest.mark.asyncio
    async def test_create_run_agent_not_found(self, test_session: AsyncSession, mock_principal: Principal):
        """
        Test run creation with non-existent agent.
        
        Mock justification: We need to test the specific error handling path when
        agent_registry raises KeyError. The real registry would also raise this
        error for non-existent agents, but mocking provides more control.
        """
        agent_id = uuid.uuid4()  # Random ID that doesn't exist
        
        # The agent registry will raise KeyError for non-existent agent
        # We don't need to mock this - the real registry handles it
        run = await create_run(
            session=test_session,
            principal=mock_principal,
            agent_id=agent_id,
            payload={"prompt": "test"},
            scopes=[],  # No scopes to avoid token exchange
            purpose="test",
        )
        
        assert run.status == "failed"
        assert "not found" in run.output["error"].lower() or "not loaded" in run.output["error"].lower()
    
    @pytest.mark.asyncio
    async def test_create_run_timeout(self, test_session: AsyncSession, test_agent: AgentDefinition, mock_principal: Principal):
        """
        Test run timeout handling.
        
        Mock justification: Testing real timeout would require waiting 30+ seconds.
        We mock the agent to simulate a slow run that exceeds the timeout.
        The integration tests in test_api_runs.py test real execution.
        """
        import asyncio
        
        with patch("app.services.run_service.agent_registry.get_or_load") as mock_get:
            mock_agent = AsyncMock()
            
            # Simulate a slow agent that would timeout
            async def slow_run(*args, **kwargs):
                await asyncio.sleep(100)  # Would timeout after 30s
            
            mock_agent.run = slow_run
            mock_get.return_value = mock_agent
            
            run = await create_run(
                session=test_session,
                principal=mock_principal,
                agent_id=test_agent.id,
                payload={"prompt": "test"},
                scopes=[],  # No scopes to skip token exchange
                purpose="test",
                agent_tier="simple",  # 30s timeout
            )
            
            assert run.status == "timeout"
            assert "timeout" in run.output["error"].lower()
            assert run.output["timeout"] == 30
    
    @pytest.mark.asyncio
    async def test_create_run_execution_error(self, test_session: AsyncSession, test_agent: AgentDefinition, mock_principal: Principal):
        """
        Test run execution error handling.
        
        Mock justification: We need to test the error handling path when
        an agent raises an exception during execution. Mocking allows us
        to trigger specific error types without relying on LLM behavior.
        """
        with patch("app.services.run_service.agent_registry.get_or_load") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run.side_effect = ValueError("Tool call failed")
            mock_get.return_value = mock_agent
            
            run = await create_run(
                session=test_session,
                principal=mock_principal,
                agent_id=test_agent.id,
                payload={"prompt": "test"},
                scopes=[],  # No scopes to skip token exchange
                purpose="test",
            )
            
            assert run.status == "failed"
            assert "Tool call failed" in run.output["error"]
            assert run.output["error_type"] == "ValueError"
