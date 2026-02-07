"""
Unit tests for dynamic agent loader and tool validation.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dynamic_loader import (
    get_available_tool_names,
    get_tool_function,
    load_active_agents,
    register_agent,
    validate_tool_references,
)
from app.models.domain import AgentDefinition
from app.schemas.definitions import AgentDefinitionCreate


def test_get_available_tool_names():
    """Test get_available_tool_names returns all available tools."""
    available = get_available_tool_names()
    
    # Legacy bundle tools
    assert "search" in available
    assert "data" in available
    assert "rag" in available
    
    # Individual data tools from BUILTIN_TOOL_METADATA
    assert "create_data_document" in available
    assert "query_data" in available
    assert "insert_records" in available
    assert "update_records" in available
    assert "delete_records" in available
    assert "list_data_documents" in available
    assert "get_data_document" in available
    
    # Search tools
    assert "document_search" in available
    assert "web_search" in available
    
    # Should have at least 12 tools (3 legacy + 9 builtin)
    assert len(available) >= 12


def test_get_tool_function():
    """Test get_tool_function returns tool functions."""
    # Legacy tools
    assert get_tool_function("search") is not None
    assert get_tool_function("data") is not None
    assert get_tool_function("rag") is not None
    
    # Builtin tools
    assert get_tool_function("web_search") is not None
    assert get_tool_function("query_data") is not None
    
    # Unknown tool
    assert get_tool_function("nonexistent_tool") is None


def test_validate_tool_references_success():
    """Test validate_tool_references passes with valid tools."""
    # Should not raise - legacy tools
    validate_tool_references(["search", "data"])
    validate_tool_references(["rag"])
    validate_tool_references([])
    
    # Should not raise - individual data tools
    validate_tool_references(["list_data_documents", "query_data"])
    validate_tool_references(["insert_records", "update_records", "delete_records"])
    
    # Should not raise - mixed
    validate_tool_references(["data", "document_search", "web_search"])


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
    # Check legacy tools
    assert "search" in error_msg
    assert "data" in error_msg
    assert "rag" in error_msg
    # Check individual tools
    assert "query_data" in error_msg
    assert "list_data_documents" in error_msg


@pytest.mark.asyncio
async def test_load_active_agents_empty(test_session: AsyncSession):
    """Test load_active_agents returns dict (may contain existing agents in session-scoped DB)."""
    agents = await load_active_agents(test_session)
    # In a session-scoped database, there may be existing agents from other tests
    # We just verify the function returns a dict without error
    assert isinstance(agents, dict)


@pytest.mark.asyncio
async def test_load_active_agents_with_agents(test_session: AsyncSession):
    """Test load_active_agents loads active agents from database."""
    # Create test agent definitions with unique names
    unique_suffix = uuid.uuid4().hex[:8]
    agent1 = AgentDefinition(
        name=f"test-agent-1-{unique_suffix}",
        model="agent",  # LiteLLM task purpose
        instructions="Test instructions",
        tools={"names": ["search", "rag"]},
        is_active=True,
    )
    agent2 = AgentDefinition(
        name=f"test-agent-2-{unique_suffix}",
        model="fast",  # LiteLLM task purpose
        instructions="Another test",
        tools={"names": ["data"]},
        is_active=True,
    )
    # Inactive agent should not be loaded
    agent3 = AgentDefinition(
        name=f"inactive-agent-{unique_suffix}",
        model="agent",  # LiteLLM task purpose
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
    
    # Verify our active agents are loaded (there may be others from the session-scoped DB)
    assert agent1.id in agents
    assert agent2.id in agents
    assert agent3.id not in agents  # Inactive agent should not be loaded
    
    # Verify agents are Pydantic AI Agent instances
    from pydantic_ai import Agent
    assert isinstance(agents[agent1.id], Agent)
    assert isinstance(agents[agent2.id], Agent)


@pytest.mark.asyncio
async def test_register_agent_success(test_session: AsyncSession):
    """Test register_agent creates definition and returns hydrated agent."""
    unique_name = f"new-agent-{uuid.uuid4().hex[:8]}"
    payload = AgentDefinitionCreate(
        name=unique_name,
        display_name="New Agent",
        description="Test agent",
        model="agent",  # LiteLLM task purpose
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
    assert definition.name == unique_name
    assert definition.model == "agent"
    assert definition.tools == {"names": ["search", "rag"]}
    assert definition.is_active is True


@pytest.mark.asyncio
async def test_register_agent_invalid_tools(test_session: AsyncSession):
    """Test register_agent raises ValueError for invalid tool references."""
    unique_name = f"bad-agent-{uuid.uuid4().hex[:8]}"
    payload = AgentDefinitionCreate(
        name=unique_name,
        model="agent",
        instructions="Test",
        tools={"names": ["search", "invalid_tool"]},
    )
    
    with pytest.raises(ValueError, match="Invalid tool references: invalid_tool"):
        await register_agent(test_session, payload)
    
    # Verify no definition was persisted
    from sqlalchemy import select
    result = await test_session.execute(
        select(AgentDefinition).where(AgentDefinition.name == unique_name)
    )
    assert result.scalars().first() is None


@pytest.mark.asyncio
async def test_register_agent_no_tools(test_session: AsyncSession):
    """Test register_agent works with no tools."""
    unique_name = f"no-tools-agent-{uuid.uuid4().hex[:8]}"
    payload = AgentDefinitionCreate(
        name=unique_name,
        model="agent",
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
    unique_name = f"agent-with-old-tool-{uuid.uuid4().hex[:8]}"
    agent = AgentDefinition(
        name=unique_name,
        model="agent",
        instructions="Test",
        tools={"names": ["search", "deprecated_tool"]},  # deprecated_tool not in registry
        is_active=True,
    )
    test_session.add(agent)
    await test_session.commit()
    await test_session.refresh(agent)
    
    # Should load agent but skip invalid tool
    agents = await load_active_agents(test_session)
    
    # Verify our agent is in the loaded agents (there may be others from previous tests)
    assert agent.id in agents
    # Agent should have search tool but not the invalid one
    from pydantic_ai import Agent
    assert isinstance(agents[agent.id], Agent)









