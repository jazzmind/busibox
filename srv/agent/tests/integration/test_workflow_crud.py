"""
Integration tests for workflow CRUD operations (User Story 3).

Tests:
- GET /agents/workflows/{workflow_id} returns workflow
- PUT /agents/workflows/{workflow_id} updates and increments version
- PUT validates workflow steps before saving
- DELETE workflow with active schedules returns 409
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import WorkflowDefinition


@pytest.fixture
async def custom_workflow_id(db_session: AsyncSession, mock_user_id: str) -> uuid.UUID:
    """Create a custom workflow for testing."""
    unique_name = f"custom_test_workflow_{uuid.uuid4().hex[:8]}"
    workflow = WorkflowDefinition(
        name=unique_name,
        description="Custom workflow for testing",
        steps=[
            {
                "id": "step1",
                "type": "agent",
                "agent_id": str(uuid.uuid4()),
                "input_mapping": {},
                "output_mapping": {}
            }
        ],
        is_active=True,
        created_by=mock_user_id,
    )
    db_session.add(workflow)
    await db_session.commit()
    await db_session.refresh(workflow)
    return workflow.id


@pytest.mark.asyncio
async def test_get_workflow_by_id(
    client: AsyncClient,
    custom_workflow_id: uuid.UUID,
    mock_token: str
):
    """
    Test: GET /agents/workflows/{workflow_id} returns workflow.
    """
    response = await client.get(
        f"/agents/workflows/{custom_workflow_id}",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    workflow = response.json()
    assert workflow["id"] == str(custom_workflow_id)
    assert workflow["name"].startswith("custom_test_workflow_")


@pytest.mark.asyncio
async def test_update_workflow_increments_version(
    client: AsyncClient,
    custom_workflow_id: uuid.UUID,
    mock_token: str
):
    """
    Test: PUT /agents/workflows/{workflow_id} updates and increments version.
    """
    # Get initial version
    response = await client.get(
        f"/agents/workflows/{custom_workflow_id}",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    initial_version = response.json()["version"]
    
    # Update workflow
    response = await client.put(
        f"/agents/workflows/{custom_workflow_id}",
        json={
            "description": "Updated workflow description"
        },
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    updated_workflow = response.json()
    
    assert updated_workflow["description"] == "Updated workflow description"
    assert updated_workflow["version"] == initial_version + 1


@pytest.mark.asyncio
async def test_update_workflow_validates_steps(
    client: AsyncClient,
    custom_workflow_id: uuid.UUID,
    mock_token: str
):
    """
    Test: PUT validates workflow steps before saving.
    """
    # Try to update with invalid steps (missing required fields)
    response = await client.put(
        f"/agents/workflows/{custom_workflow_id}",
        json={
            "steps": [
                {"id": "step1"}  # Missing type, agent_id/tool_id
            ]
        },
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_unused_workflow_returns_204(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_token: str,
    mock_user_id: str
):
    """
    Test: DELETE unused workflow returns 204.
    """
    # Create unused workflow
    unique_name = f"unused_test_workflow_{uuid.uuid4().hex[:8]}"
    workflow = WorkflowDefinition(
        name=unique_name,
        description="Unused workflow",
        steps=[],
        is_active=True,
        created_by=mock_user_id,
    )
    db_session.add(workflow)
    await db_session.commit()
    await db_session.refresh(workflow)
    workflow_id = workflow.id
    
    # Delete workflow
    response = await client.delete(
        f"/agents/workflows/{workflow_id}",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 204
    
    # Verify workflow is soft-deleted (expire cache first to get fresh data)
    db_session.expire_all()  # sync method - no await
    from sqlalchemy import select
    stmt = select(WorkflowDefinition).where(WorkflowDefinition.id == workflow_id)
    result = await db_session.execute(stmt)
    workflow = result.scalar_one_or_none()
    assert workflow is not None, "Workflow should still exist in database"
    assert workflow.is_active is False, "Workflow should be soft-deleted"









