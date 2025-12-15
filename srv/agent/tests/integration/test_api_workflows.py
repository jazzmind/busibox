"""
Integration tests for workflow execution API.
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.models.domain import AgentDefinition, WorkflowDefinition


@pytest.mark.asyncio
async def test_create_workflow_success(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/workflows creates workflow with validation."""
    payload = {
        "name": "test-workflow",
        "description": "Test workflow",
        "steps": [
            {"id": "search", "type": "tool", "tool": "search", "args": {"query": "$.input.query", "top_k": 5}},
            {"id": "analyze", "type": "agent", "agent": "analyzer", "input": "$.search.hits"},
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
    assert "id" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_create_workflow_invalid_steps(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/workflows rejects invalid step definitions."""
    # Missing step ID
    payload = {
        "name": "bad-workflow",
        "steps": [
            {"type": "tool", "tool": "search"}  # Missing id
        ],
    }
    
    response = await test_client.post(
        "/agents/workflows",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 400
    assert "missing required field: id" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_workflow_duplicate_step_ids(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /agents/workflows rejects duplicate step IDs."""
    payload = {
        "name": "dup-workflow",
        "steps": [
            {"id": "step1", "type": "tool", "tool": "search"},
            {"id": "step1", "type": "tool", "tool": "ingest"},  # Duplicate
        ],
    }
    
    response = await test_client.post(
        "/agents/workflows",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 400
    assert "Duplicate step ID" in response.json()["detail"]


@pytest.mark.asyncio
async def test_execute_workflow_success(test_client: AsyncClient, test_session, mock_jwt_token: str):
    """Test POST /runs/workflow executes multi-step workflow."""
    # Create workflow
    workflow = WorkflowDefinition(
        name="test-exec-workflow",
        description="Test execution",
        steps=[
            {"id": "search", "type": "tool", "tool": "search", "args": {"query": "$.input.query", "top_k": 5}},
        ],
        is_active=True,
    )
    test_session.add(workflow)
    await test_session.commit()
    await test_session.refresh(workflow)
    
    # Mock Busibox client
    with patch("app.workflows.engine.BusiboxClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value={"hits": [{"id": "doc1"}], "total": 1})
        mock_client_class.return_value = mock_client
        
        # Mock token exchange
        with patch("app.workflows.engine.get_or_exchange_token") as mock_token:
            mock_token.return_value = MagicMock(access_token="test-token")
            
            # Execute workflow
            response = await test_client.post(
                f"/runs/workflow?workflow_id={workflow.id}",
                json={"query": "test query"},
                headers={"Authorization": f"Bearer {mock_jwt_token}"},
            )
            
            assert response.status_code == 202
            data = response.json()
            
            # Verify run was created
            assert "id" in data
            assert data["workflow_id"] == str(workflow.id)
            assert data["status"] in ["running", "succeeded"]
            
            # Verify events include step execution
            assert len(data["events"]) > 0
            step_events = [e for e in data["events"] if e.get("step_id") == "search"]
            assert len(step_events) >= 1


@pytest.mark.asyncio
async def test_execute_workflow_not_found(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /runs/workflow returns 500 for non-existent workflow."""
    non_existent_id = uuid.uuid4()
    
    response = await test_client.post(
        f"/runs/workflow?workflow_id={non_existent_id}",
        json={"query": "test"},
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 500
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_execute_workflow_requires_auth(test_client: AsyncClient):
    """Test POST /runs/workflow requires authentication."""
    workflow_id = uuid.uuid4()
    
    response = await test_client.post(
        f"/runs/workflow?workflow_id={workflow_id}",
        json={"query": "test"},
    )
    
    assert response.status_code == 401


def test_validate_workflow_steps_complex():
    """Test validate_workflow_steps with complex workflow."""
    steps = [
        {"id": "ingest", "type": "tool", "tool": "ingest", "args": {"path": "$.input.path"}},
        {"id": "search", "type": "tool", "tool": "search", "args": {"query": "$.input.query"}},
        {"id": "analyze", "type": "agent", "agent": "analyzer", "input": "$.search.hits"},
        {"id": "summarize", "type": "agent", "agent": "summarizer", "input": "$.analyze.result"},
    ]
    
    # Should not raise
    validate_workflow_steps(steps)


def test_validate_workflow_steps_all_error_cases():
    """Test validate_workflow_steps catches all validation errors."""
    # Empty workflow
    with pytest.raises(ValueError, match="must have at least one step"):
        validate_workflow_steps([])
    
    # Missing ID
    with pytest.raises(ValueError, match="missing required field: id"):
        validate_workflow_steps([{"type": "tool"}])
    
    # Missing type
    with pytest.raises(ValueError, match="missing required field: type"):
        validate_workflow_steps([{"id": "step1"}])
    
    # Invalid type
    with pytest.raises(ValueError, match="invalid type"):
        validate_workflow_steps([{"id": "step1", "type": "unknown"}])
    
    # Tool missing tool field
    with pytest.raises(ValueError, match="missing required field: tool"):
        validate_workflow_steps([{"id": "step1", "type": "tool"}])
    
    # Agent missing agent field
    with pytest.raises(ValueError, match="missing required field: agent"):
        validate_workflow_steps([{"id": "step1", "type": "agent"}])
    
    # Duplicate IDs
    with pytest.raises(ValueError, match="Duplicate step ID"):
        validate_workflow_steps([
            {"id": "step1", "type": "tool", "tool": "search"},
            {"id": "step1", "type": "tool", "tool": "ingest"},
        ])





