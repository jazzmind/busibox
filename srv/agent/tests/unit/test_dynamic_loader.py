"""
Unit tests for dynamic agent loader and tool validation.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dynamic_loader import (
    TOOL_REGISTRY,
    load_active_agents,
    register_agent,
    validate_tool_references,
)
from app.models.domain import AgentDefinition
from app.schemas.definitions import AgentDefinitionCreate


def test_tool_registry_has_expected_tools():
    """Test TOOL_REGISTRY contains the core tools."""
    assert "search" in TOOL_REGISTRY
    assert "ingest" in TOOL_REGISTRY
    assert "rag" in TOOL_REGISTRY
    assert len(TOOL_REGISTRY) == 3


def test_validate_tool_references_success():
    """Test validate_tool_references passes with valid tools."""
    # Should not raise
    validate_tool_references(["search", "ingest"])
    validate_tool_references(["rag"])
    validate_tool_references([])


def test_validate_tool_references_invalid_tool():
    """Test validate_tool_references raises on invalid tool."""
    with pytest.raises(ValueError, match="Invalid tool references: invalid_tool"):
        validate_tool_references(["invalid_tool"])
    
    with pytest.raises(ValueError, match="Invalid tool references: bad1, bad2"):
        validate_tool_references(["search", "bad1", "bad2"])


def test_validate_tool_references_error_message():
    """Test validate_tool_references error includes available tools."""
    with pytest.raises(ValueError) as exc_info:
        validate_tool_references(["unknown"])
    
    error_msg = str(exc_info.value)
    assert "Available tools:" in error_msg
    assert "search" in error_msg
    assert "ingest" in error_msg
    assert "rag" in error_msg


@pytest.mark.asyncio
async def test_load_active_agents_empty(test_session: AsyncSession):
    """Test load_active_agents returns empty dict when no agents exist."""
    agents = await load_active_agents(test_session)
    assert agents == {}


@pytest.mark.asyncio
async def test_load_active_agents_with_agents(test_session: AsyncSession):
    """Test load_active_agents loads active agents from database."""
    # Create test agent definitions
    agent1 = AgentDefinition(
        name="test-agent-1",
        model="anthropic:claude-3-5-sonnet",
        instructions="Test instructions",
        tools={"names": ["search", "rag"]},
        is_active=True,
    )
    agent2 = AgentDefinition(
        name="test-agent-2",
        model="openai:gpt-4",
        instructions="Another test",
        tools={"names": ["ingest"]},
        is_active=True,
    )
    # Inactive agent should not be loaded
    agent3 = AgentDefinition(
        name="inactive-agent",
        model="openai:gpt-4",
        instructions="Inactive",
        tools={"names": []},
        is_active=False,
    )
    
    test_session.add_all([agent1, agent2, agent3])
    await test_session.commit()
    await test_session.refresh(agent1)
    await test_session.refresh(agent2)
    
    # Load agents
    agents = await load_active_agents(test_session)
    
    # Verify only active agents loaded
    assert len(agents) == 2
    assert agent1.id in agents
    assert agent2.id in agents
    assert agent3.id not in agents
    
    # Verify agents are Pydantic AI Agent instances
    from pydantic_ai import Agent
    assert isinstance(agents[agent1.id], Agent)
    assert isinstance(agents[agent2.id], Agent)


@pytest.mark.asyncio
async def test_register_agent_success(test_session: AsyncSession):
    """Test register_agent creates definition and returns hydrated agent."""
    payload = AgentDefinitionCreate(
        name="new-agent",
        display_name="New Agent",
        description="Test agent",
        model="anthropic:claude-3-5-sonnet",
        instructions="Be helpful",
        tools={"names": ["search", "rag"]},
        scopes=["agent.execute", "search.read"],
    )
    
    agent_id, agent = await register_agent(test_session, payload)
    
    # Verify agent_id is UUID
    assert isinstance(agent_id, uuid.UUID)
    
    # Verify agent is Pydantic AI Agent
    from pydantic_ai import Agent
    assert isinstance(agent, Agent)
    
    # Verify definition persisted
    definition = await test_session.get(AgentDefinition, agent_id)
    assert definition is not None
    assert definition.name == "new-agent"
    assert definition.model == "anthropic:claude-3-5-sonnet"
    assert definition.tools == {"names": ["search", "rag"]}
    assert definition.is_active is True


@pytest.mark.asyncio
async def test_register_agent_invalid_tools(test_session: AsyncSession):
    """Test register_agent raises ValueError for invalid tool references."""
    payload = AgentDefinitionCreate(
        name="bad-agent",
        model="anthropic:claude-3-5-sonnet",
        instructions="Test",
        tools={"names": ["search", "invalid_tool"]},
    )
    
    with pytest.raises(ValueError, match="Invalid tool references: invalid_tool"):
        await register_agent(test_session, payload)
    
    # Verify no definition was persisted
    from sqlalchemy import select
    result = await test_session.execute(
        select(AgentDefinition).where(AgentDefinition.name == "bad-agent")
    )
    assert result.scalars().first() is None


@pytest.mark.asyncio
async def test_register_agent_no_tools(test_session: AsyncSession):
    """Test register_agent works with no tools."""
    payload = AgentDefinitionCreate(
        name="no-tools-agent",
        model="anthropic:claude-3-5-sonnet",
        instructions="Simple agent",
        tools={"names": []},
    )
    
    agent_id, agent = await register_agent(test_session, payload)
    
    assert isinstance(agent_id, uuid.UUID)
    from pydantic_ai import Agent
    assert isinstance(agent, Agent)


@pytest.mark.asyncio
async def test_load_active_agents_skips_invalid_tools(test_session: AsyncSession):
    """Test load_active_agents handles agents with invalid tool references gracefully."""
    # Create agent with invalid tool (this might happen if tool was removed from registry)
    agent = AgentDefinition(
        name="agent-with-old-tool",
        model="anthropic:claude-3-5-sonnet",
        instructions="Test",
        tools={"names": ["search", "deprecated_tool"]},  # deprecated_tool not in registry
        is_active=True,
    )
    test_session.add(agent)
    await test_session.commit()
    await test_session.refresh(agent)
    
    # Should load agent but skip invalid tool
    agents = await load_active_agents(test_session)
    
    assert len(agents) == 1
    assert agent.id in agents
    # Agent should have search tool but not the invalid one
    from pydantic_ai import Agent
    assert isinstance(agents[agent.id], Agent)
