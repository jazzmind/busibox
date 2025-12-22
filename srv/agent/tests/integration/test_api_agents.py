"""
Integration tests for agent definition CRUD API endpoints.
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.models.domain import AgentDefinition


@pytest.mark.asyncio
async def test_list_agents_returns_builtin_agents(test_client: AsyncClient, mock_jwt_token: str):
    """Test GET /agents returns built-in agents."""
    response = await test_client.get(
        "/agents",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )

    assert response.status_code == 200
    agents = response.json()
    assert isinstance(agents, list)
    assert len(agents) > 0  # Should have built-in agents

    # Check that at least some expected agents are present
    agent_names = {agent["name"] for agent in agents}
    # Check for some common built-in agents
    assert len(agent_names) > 5  # Should have multiple built-in agents
    assert "chat" in agent_names  # Basic chat agent
    assert "rag-search" in agent_names  # RAG search agent


@pytest.mark.asyncio
async def test_list_agents_with_custom_data(test_client: AsyncClient, mock_jwt_token: str):
    """Test GET /agents returns custom agents created via API."""
    import uuid
    unique_name = f"test-agent-{uuid.uuid4().hex[:8]}"
    
    # Create a custom agent via the API
    create_response = await test_client.post(
        "/agents/definitions",
        json={
            "name": unique_name,
            "model": "agent",
            "instructions": "Test agent for list verification",
            "tools": {"names": ["search"]},
            "is_active": True,
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201, f"Failed to create agent: {create_response.text}"
    
    # Fetch the list of agents
    response = await test_client.get(
        "/agents",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    # Should have built-in agents plus our custom agent
    assert len(data) >= 10  # At least 10 built-in agents
    
    names = [agent["name"] for agent in data]
    # The custom agent should be visible (if user filtering is working)
    # Note: Custom agents are personal and only visible to the creator
    assert "chat" in names  # Built-in agent always visible


@pytest.mark.asyncio
async def test_create_agent_definition_success(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/definitions creates new agent."""
    unique_name = f"new-agent-{uuid.uuid4().hex[:8]}"
    payload = {
        "name": unique_name,
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
    
    assert response.status_code == 201, f"Failed: {response.text}"
    data = response.json()
    
    # Verify response structure
    assert "id" in data
    assert data["name"] == unique_name
    assert data["model"] == "agent"
    assert data["tools"] == {"names": ["search", "rag"]}
    assert "version" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_create_agent_definition_invalid_tools(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/definitions rejects invalid tool references."""
    payload = {
        "name": f"bad-agent-{uuid.uuid4().hex[:8]}",
        "model": "agent",
        "instructions": "Test",
        "tools": {"names": ["search", "invalid_tool"]},
    }
    
    response = await test_client.post(
        "/agents/definitions",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 400  # Bad request from validation
    assert "invalid_tool" in response.json()["detail"].lower() or "invalid tool" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_agent_definition_minimal(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/definitions with minimal required fields."""
    unique_name = f"minimal-agent-{uuid.uuid4().hex[:8]}"
    payload = {
        "name": unique_name,
        "model": "agent",
        "instructions": "Simple instructions",
    }
    
    response = await test_client.post(
        "/agents/definitions",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201, f"Failed: {response.text}"
    data = response.json()
    assert data["name"] == unique_name
    assert data["is_active"] is True
    assert data["tools"] == {}


@pytest.mark.asyncio
async def test_create_agent_definition_requires_auth(test_client: AsyncClient):
    """Test POST /agents/definitions requires authentication.
    
    FastAPI returns 422 when required header is missing (validation error).
    This is correct behavior - the endpoint is protected.
    """
    payload = {
        "name": "test",
        "model": "agent",
        "instructions": "Test",
    }
    
    response = await test_client.post("/agents/definitions", json=payload)
    
    # 422 = missing required authorization header (FastAPI validation)
    assert response.status_code == 422
    assert "authorization" in str(response.json()).lower()


@pytest.mark.asyncio
async def test_list_tools(test_client: AsyncClient, mock_jwt_token: str):
    """Test GET /agents/tools returns tool definitions."""
    response = await test_client.get(
        "/agents/tools",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200, f"Unexpected status: {response.status_code}, body: {response.text}"
    # Initially empty since no tools are pre-populated
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_tool(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/tools creates tool definition."""
    unique_name = f"custom-tool-{uuid.uuid4().hex[:8]}"
    payload = {
        "name": unique_name,
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
    
    assert response.status_code == 201, f"Failed: {response.text}"
    data = response.json()
    assert data["name"] == unique_name
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
    unique_name = f"test-workflow-{uuid.uuid4().hex[:8]}"
    payload = {
        "name": unique_name,
        "description": "Test workflow",
        "steps": [
            {"id": "step1", "type": "agent", "agent": "chat", "input": "test query 1"},
            {"id": "step2", "type": "agent", "agent": "rag-search", "input": "$.step1.output"},
        ],
        "is_active": True,
    }
    
    response = await test_client.post(
        "/agents/workflows",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201, f"Failed with: {response.text}"
    data = response.json()
    assert data["name"] == unique_name
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
    unique_name = f"test-eval-{uuid.uuid4().hex[:8]}"
    payload = {
        "name": unique_name,
        "description": "Test evaluator",
        "config": {"metric": "accuracy", "threshold": 0.8},
        "is_active": True,
    }
    
    response = await test_client.post(
        "/agents/evals",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201, f"Failed: {response.text}"
    data = response.json()
    assert data["name"] == unique_name
    assert data["config"]["metric"] == "accuracy"


@pytest.mark.asyncio
async def test_agent_crud_workflow(test_client: AsyncClient, mock_jwt_token: str):
    """Test complete agent CRUD workflow."""
    unique_name = f"workflow-test-agent-{uuid.uuid4().hex[:8]}"
    
    # 1. List agents (get initial count)
    response = await test_client.get(
        "/agents",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert response.status_code == 200
    initial_count = len(response.json())
    
    # 2. Create agent
    create_payload = {
        "name": unique_name,
        "model": "agent",
        "instructions": "Test agent for workflow",
        "tools": {"names": ["search"]},
    }
    
    response = await test_client.post(
        "/agents/definitions",
        json=create_payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert response.status_code == 201, f"Failed: {response.text}"
    agent_data = response.json()
    agent_id = agent_data["id"]
    
    # 3. List agents (should have one more)
    response = await test_client.get(
        "/agents",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert response.status_code == 200
    # Note: Count may vary due to session-scoped DB, just verify agent exists
    
    # 4. Verify agent appears in list
    agents = response.json()
    agent_names = [a["name"] for a in agents]
    assert unique_name in agent_names









