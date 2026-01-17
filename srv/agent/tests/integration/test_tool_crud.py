"""
Integration tests for tool CRUD operations (User Story 3).

Tests:
- GET /agents/tools/{tool_id} returns tool
- PUT /agents/tools/{tool_id} updates tool and increments version
- DELETE built-in tool returns 403
- DELETE tool in use returns 409 with agent list
- DELETE unused tool returns 204
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import AgentDefinition, ToolDefinition


@pytest.fixture
async def custom_tool_id(db_session: AsyncSession, mock_user_id: str) -> uuid.UUID:
    """Create a custom tool for testing."""
    unique_name = f"custom_test_tool_{uuid.uuid4().hex[:8]}"
    tool = ToolDefinition(
        name=unique_name,
        description="Custom tool for testing",
        schema={"input": {"type": "object"}, "output": {"type": "object"}},
        entrypoint="app.tools.test:custom_tool",
        scopes=["test.read"],
        is_active=True,
        is_builtin=False,
        created_by=mock_user_id,
    )
    db_session.add(tool)
    await db_session.commit()
    await db_session.refresh(tool)
    return tool.id


@pytest.fixture
async def builtin_tool_id(db_session: AsyncSession) -> uuid.UUID:
    """Create a built-in tool for testing."""
    unique_name = f"builtin_test_tool_{uuid.uuid4().hex[:8]}"
    tool = ToolDefinition(
        name=unique_name,
        description="Built-in tool for testing",
        schema={"input": {"type": "object"}, "output": {"type": "object"}},
        entrypoint="app.tools.builtin:test_tool",
        scopes=[],
        is_active=True,
        is_builtin=True,
        created_by=None,
    )
    db_session.add(tool)
    await db_session.commit()
    await db_session.refresh(tool)
    return tool.id


@pytest.mark.asyncio
async def test_get_tool_by_id(
    client: AsyncClient,
    custom_tool_id: uuid.UUID,
    mock_token: str
):
    """
    Test: GET /agents/tools/{tool_id} returns tool.
    
    Acceptance Scenario: Can retrieve individual tool by ID.
    """
    response = await client.get(
        f"/agents/tools/{custom_tool_id}",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    tool = response.json()
    assert tool["id"] == str(custom_tool_id)
    assert tool["name"].startswith("custom_test_tool_")
    assert tool["is_builtin"] is False


@pytest.mark.asyncio
async def test_update_tool_increments_version(
    client: AsyncClient,
    custom_tool_id: uuid.UUID,
    mock_token: str,
    db_session: AsyncSession
):
    """
    Test: PUT /agents/tools/{tool_id} updates tool and increments version.
    
    Acceptance Scenario 2: Given I have a custom tool, When I update its description 
    and schema, Then the changes are saved and the version increments.
    """
    # Get initial version
    response = await client.get(
        f"/agents/tools/{custom_tool_id}",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    initial_version = response.json()["version"]
    
    # Update tool
    response = await client.put(
        f"/agents/tools/{custom_tool_id}",
        json={
            "description": "Updated description",
            "schema": {"input": {"type": "string"}, "output": {"type": "string"}}
        },
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    updated_tool = response.json()
    
    assert updated_tool["description"] == "Updated description"
    assert updated_tool["version"] == initial_version + 1, "Version should increment"
    assert updated_tool["schema"]["input"]["type"] == "string"


@pytest.mark.asyncio
async def test_delete_builtin_tool_returns_403(
    client: AsyncClient,
    builtin_tool_id: uuid.UUID,
    mock_token: str
):
    """
    Test: DELETE built-in tool returns 403.
    
    Acceptance Scenario 3: Given I try to delete a built-in tool, When I make the 
    delete request, Then I receive a 403 Forbidden error.
    """
    response = await client.delete(
        f"/agents/tools/{builtin_tool_id}",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 403
    assert "built-in" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_tool_in_use_returns_409(
    client: AsyncClient,
    custom_tool_id: uuid.UUID,
    mock_token: str,
    db_session: AsyncSession,
    mock_user_id: str
):
    """
    Test: DELETE tool in use returns 409 with agent list.
    
    Acceptance Scenario 4: Given my custom tool is used by an active agent, When I try 
    to delete it, Then I receive a 409 Conflict error with details about which agents use it.
    """
    # Create agent that uses the tool - we need to get the actual tool name
    from sqlalchemy import select
    stmt = select(ToolDefinition).where(ToolDefinition.id == custom_tool_id)
    result = await db_session.execute(stmt)
    tool = result.scalar_one()
    
    agent = AgentDefinition(
        name=f"agent-using-tool-{uuid.uuid4().hex[:8]}",
        model="agent",
        instructions="Test agent",
        tools={"names": [tool.name]},
        scopes=[],
        is_active=True,
        is_builtin=False,
        created_by=mock_user_id,
    )
    db_session.add(agent)
    await db_session.commit()
    
    # Try to delete tool
    response = await client.delete(
        f"/agents/tools/{custom_tool_id}",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 409
    error_data = response.json()
    assert error_data["detail"]["error"] == "resource_in_use"
    assert "agents" in error_data["detail"]
    assert len(error_data["detail"]["agents"]) > 0


@pytest.mark.asyncio
async def test_delete_unused_tool_returns_204(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_token: str,
    mock_user_id: str
):
    """
    Test: DELETE unused tool returns 204.
    
    Acceptance Scenario 5: Given I have a custom tool not in use, When I delete it, 
    Then it is soft-deleted (is_active = false) and no longer appears in my tool list.
    """
    # Create unused tool
    unique_name = f"unused_test_tool_{uuid.uuid4().hex[:8]}"
    tool = ToolDefinition(
        name=unique_name,
        description="Unused tool",
        schema={},
        entrypoint="app.tools.test:unused",
        scopes=[],
        is_active=True,
        is_builtin=False,
        created_by=mock_user_id,
    )
    db_session.add(tool)
    await db_session.commit()
    await db_session.refresh(tool)
    tool_id = tool.id
    
    # Delete tool
    response = await client.delete(
        f"/agents/tools/{tool_id}",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 204
    
    # Verify tool is soft-deleted (query fresh from DB)
    db_session.expire_all()  # Expire all cached objects (sync method)
    from sqlalchemy import select
    stmt = select(ToolDefinition).where(ToolDefinition.id == tool_id)
    result = await db_session.execute(stmt)
    tool = result.scalar_one_or_none()
    assert tool is not None, "Tool should still exist in database"
    assert tool.is_active is False, "Tool should be soft-deleted"
    
    # Verify tool no longer in list
    response = await client.get(
        "/agents/tools",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    tool_ids = [t["id"] for t in response.json()]
    assert str(tool_id) not in tool_ids, "Deleted tool should not appear in list"


@pytest.mark.asyncio
async def test_update_builtin_tool_returns_403(
    client: AsyncClient,
    builtin_tool_id: uuid.UUID,
    mock_token: str
):
    """
    Test: Cannot update built-in tools.
    
    Success Criterion SC-007: System prevents 100% of attempts to modify or delete 
    built-in tools/agents for all users including admins.
    """
    response = await client.put(
        f"/agents/tools/{builtin_tool_id}",
        json={"description": "Trying to update built-in tool"},
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 403
    assert "built-in" in response.json()["detail"].lower()









