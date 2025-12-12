"""
Integration tests for agent definition CRUD API endpoints.
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.models.domain import AgentDefinition


@pytest.mark.asyncio
async def test_list_agents_empty(test_client: AsyncClient, mock_jwt_token: str):
    """Test GET /agents returns empty list when no agents exist."""
    response = await test_client.get(
        "/agents",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_agents_with_data(test_client: AsyncClient, test_session, mock_jwt_token: str):
    """Test GET /agents returns active agents."""
    # Create test agents
    agent1 = AgentDefinition(
        name="agent-1",
        model="agent",
        instructions="Test 1",
        tools={"names": ["search"]},
        is_active=True,
    )
    agent2 = AgentDefinition(
        name="agent-2",
        model="agent",
        instructions="Test 2",
        tools={"names": ["rag"]},
        is_active=True,
    )
    # Inactive agent should not appear
    agent3 = AgentDefinition(
        name="inactive",
        model="agent",
        instructions="Inactive",
        tools={"names": []},
        is_active=False,
    )
    
    test_session.add_all([agent1, agent2, agent3])
    await test_session.commit()
    
    response = await test_client.get(
        "/agents",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    
    names = [agent["name"] for agent in data]
    assert "agent-1" in names
    assert "agent-2" in names
    assert "inactive" not in names


@pytest.mark.asyncio
async def test_create_agent_definition_success(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/definitions creates new agent."""
    payload = {
        "name": "new-agent",
        "display_name": "New Agent",
        "description": "Test agent",
        "model": "agent",
        "instructions": "Be helpful and concise",
        "tools": {"names": ["search", "rag"]},
        "scopes": ["agent.execute", "search.read"],
        "is_active": True,
    }
    
    response = await test_client.post(
        "/agents/definitions",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201
    data = response.json()
    
    # Verify response structure
    assert "id" in data
    assert data["name"] == "new-agent"
    assert data["model"] == "agent"
    assert data["tools"] == {"names": ["search", "rag"]}
    assert "version" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_create_agent_definition_invalid_tools(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/definitions rejects invalid tool references."""
    payload = {
        "name": "bad-agent",
        "model": "agent",
        "instructions": "Test",
        "tools": {"names": ["search", "invalid_tool"]},
    }
    
    response = await test_client.post(
        "/agents/definitions",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 500  # Internal error from validation
    # Could be improved to return 400 with better error handling


@pytest.mark.asyncio
async def test_create_agent_definition_minimal(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/definitions with minimal required fields."""
    payload = {
        "name": "minimal-agent",
        "model": "agent",
        "instructions": "Simple instructions",
    }
    
    response = await test_client.post(
        "/agents/definitions",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "minimal-agent"
    assert data["is_active"] is True
    assert data["tools"] == {}


@pytest.mark.asyncio
async def test_create_agent_definition_requires_auth(test_client: AsyncClient):
    """Test POST /agents/definitions requires authentication."""
    payload = {
        "name": "test",
        "model": "agent",
        "instructions": "Test",
    }
    
    response = await test_client.post("/agents/definitions", json=payload)
    
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_tools(test_client: AsyncClient, mock_jwt_token: str):
    """Test GET /agents/tools returns tool definitions."""
    response = await test_client.get(
        "/agents/tools",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    # Initially empty since no tools are pre-populated
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_tool(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/tools creates tool definition."""
    payload = {
        "name": "custom-tool",
        "description": "Custom tool",
        "schema": {"query": {"type": "string"}},
        "entrypoint": "custom_adapter",
        "scopes": ["tool.execute"],
        "is_active": True,
    }
    
    response = await test_client.post(
        "/agents/tools",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "custom-tool"
    assert "id" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_list_workflows(test_client: AsyncClient, mock_jwt_token: str):
    """Test GET /agents/workflows returns workflow definitions."""
    response = await test_client.get(
        "/agents/workflows",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_workflow(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/workflows creates workflow definition."""
    payload = {
        "name": "test-workflow",
        "description": "Test workflow",
        "steps": [
            {"agent": "agent-1", "input": "step1"},
            {"agent": "agent-2", "input": "step2"},
        ],
        "is_active": True,
    }
    
    response = await test_client.post(
        "/agents/workflows",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "test-workflow"
    assert len(data["steps"]) == 2


@pytest.mark.asyncio
async def test_list_evals(test_client: AsyncClient, mock_jwt_token: str):
    """Test GET /agents/evals returns eval definitions."""
    response = await test_client.get(
        "/agents/evals",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_eval(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/evals creates eval definition."""
    payload = {
        "name": "test-eval",
        "description": "Test evaluator",
        "config": {"metric": "accuracy", "threshold": 0.8},
        "is_active": True,
    }
    
    response = await test_client.post(
        "/agents/evals",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "test-eval"
    assert data["config"]["metric"] == "accuracy"


@pytest.mark.asyncio
async def test_agent_crud_workflow(test_client: AsyncClient, mock_jwt_token: str):
    """Test complete agent CRUD workflow."""
    # 1. List agents (should be empty)
    response = await test_client.get(
        "/agents",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert response.status_code == 200
    initial_count = len(response.json())
    
    # 2. Create agent
    create_payload = {
        "name": "workflow-test-agent",
        "model": "agent",
        "instructions": "Test agent for workflow",
        "tools": {"names": ["search"]},
    }
    
    response = await test_client.post(
        "/agents/definitions",
        json=create_payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert response.status_code == 201
    agent_data = response.json()
    agent_id = agent_data["id"]
    
    # 3. List agents (should have one more)
    response = await test_client.get(
        "/agents",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) == initial_count + 1
    
    # 4. Verify agent appears in list
    agents = response.json()
    agent_names = [a["name"] for a in agents]
    assert "workflow-test-agent" in agent_names
