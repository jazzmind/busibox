"""
Integration tests for agent tasks API endpoints.

Tests the full CRUD lifecycle for agent tasks including
scheduling, notifications, and execution tracking.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_task_requires_auth(test_client: AsyncClient):
    """Test POST /tasks requires authentication."""
    response = await test_client.post(
        "/tasks",
        json={
            "name": "Test Task",
            "agent_id": str(uuid.uuid4()),
            "prompt": "Test prompt",
            "trigger_type": "cron",
        },
    )
    
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_tasks_requires_auth(test_client: AsyncClient):
    """Test GET /tasks requires authentication."""
    response = await test_client.get("/tasks")
    
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_tasks_empty(test_client: AsyncClient, mock_jwt_token: str):
    """Test GET /tasks returns empty list when no tasks."""
    response = await test_client.get(
        "/tasks",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "tasks" in data
    assert isinstance(data["tasks"], list)


@pytest.mark.asyncio
async def test_create_task_success(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test POST /tasks creates a new task."""
    task_name = f"test-task-{uuid.uuid4().hex[:8]}"
    
    response = await test_client.post(
        "/tasks",
        json={
            "name": task_name,
            "description": "Test task for integration tests",
            "agent_id": str(test_agent.id),
            "prompt": "Test prompt - do nothing",
            "trigger_type": "cron",
            "trigger_config": {"cron": "0 9 * * *"},
            "notification_config": {
                "enabled": False,
            },
            "insights_config": {
                "enabled": True,
                "max_insights": 50,
            },
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201, f"Failed: {response.text}"
    data = response.json()
    
    assert "id" in data
    assert data["name"] == task_name
    assert data["status"] == "active"
    assert data["trigger_type"] == "cron"
    assert data["run_count"] == 0
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_create_task_with_preset_schedule(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test POST /tasks with schedule preset."""
    task_name = f"preset-task-{uuid.uuid4().hex[:8]}"
    
    response = await test_client.post(
        "/tasks",
        json={
            "name": task_name,
            "agent_id": str(test_agent.id),
            "prompt": "Hourly check",
            "trigger_type": "cron",
            "trigger_config": {"cron": "hourly"},  # Preset name
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201, f"Failed: {response.text}"
    data = response.json()
    
    # Preset should be converted to actual cron
    assert data["trigger_config"]["cron"] == "0 * * * *"


@pytest.mark.asyncio
async def test_create_task_webhook_trigger(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test POST /tasks with webhook trigger."""
    task_name = f"webhook-task-{uuid.uuid4().hex[:8]}"
    
    response = await test_client.post(
        "/tasks",
        json={
            "name": task_name,
            "agent_id": str(test_agent.id),
            "prompt": "Process webhook event",
            "trigger_type": "webhook",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201, f"Failed: {response.text}"
    data = response.json()
    
    assert data["trigger_type"] == "webhook"
    # Webhook tasks should have a webhook_url
    assert "webhook_url" in data or "webhook_secret" in data


@pytest.mark.asyncio
async def test_create_task_invalid_agent(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /tasks with non-existent agent fails."""
    response = await test_client.post(
        "/tasks",
        json={
            "name": "Invalid Agent Task",
            "agent_id": str(uuid.uuid4()),  # Non-existent
            "prompt": "Test",
            "trigger_type": "cron",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code in [400, 404]


@pytest.mark.asyncio
async def test_get_task_success(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test GET /tasks/{id} returns task details."""
    # First create a task
    task_name = f"get-task-{uuid.uuid4().hex[:8]}"
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": task_name,
            "agent_id": str(test_agent.id),
            "prompt": "Test prompt",
            "trigger_type": "cron",
            "trigger_config": {"cron": "0 12 * * *"},
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["id"]
    
    # Get the task
    response = await test_client.get(
        f"/tasks/{task_id}",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == task_id
    assert data["name"] == task_name


@pytest.mark.asyncio
async def test_get_task_not_found(test_client: AsyncClient, mock_jwt_token: str):
    """Test GET /tasks/{id} returns 404 for non-existent task."""
    fake_id = str(uuid.uuid4())
    
    response = await test_client.get(
        f"/tasks/{fake_id}",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_task_success(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test PATCH /tasks/{id} updates task."""
    # Create a task
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"update-task-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "Original prompt",
            "trigger_type": "cron",
            "trigger_config": {"cron": "0 9 * * *"},
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["id"]
    
    # Update the task
    new_name = f"updated-task-{uuid.uuid4().hex[:8]}"
    response = await test_client.patch(
        f"/tasks/{task_id}",
        json={
            "name": new_name,
            "prompt": "Updated prompt",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == new_name
    assert data["prompt"] == "Updated prompt"


@pytest.mark.asyncio
async def test_delete_task_success(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test DELETE /tasks/{id} removes task."""
    # Create a task
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"delete-task-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "To be deleted",
            "trigger_type": "cron",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["id"]
    
    # Delete the task
    response = await test_client.delete(
        f"/tasks/{task_id}",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 204
    
    # Verify it's gone
    get_response = await test_client.get(
        f"/tasks/{task_id}",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_pause_task_success(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test POST /tasks/{id}/pause pauses active task."""
    # Create an active task
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"pause-task-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "To be paused",
            "trigger_type": "cron",
            "trigger_config": {"cron": "0 9 * * *"},
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["id"]
    assert create_response.json()["status"] == "active"
    
    # Pause the task
    response = await test_client.post(
        f"/tasks/{task_id}/pause",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "paused"


@pytest.mark.asyncio
async def test_resume_task_success(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test POST /tasks/{id}/resume resumes paused task."""
    # Create a task
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"resume-task-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "To be resumed",
            "trigger_type": "cron",
            "trigger_config": {"cron": "0 9 * * *"},
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["id"]
    
    # Pause the task first
    pause_response = await test_client.post(
        f"/tasks/{task_id}/pause",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "paused"
    
    # Resume the task
    response = await test_client.post(
        f"/tasks/{task_id}/resume",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_run_task_manually(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test POST /tasks/{id}/run triggers manual execution."""
    # Create a task
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"run-task-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "Run manually",
            "trigger_type": "cron",
            "trigger_config": {"cron": "0 9 * * *"},
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["id"]
    
    # Trigger manual run
    response = await test_client.post(
        f"/tasks/{task_id}/run",
        json={},
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code in [200, 202]
    data = response.json()
    assert "execution_id" in data or "message" in data


@pytest.mark.asyncio
async def test_list_task_executions(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test GET /tasks/{id}/executions returns execution history."""
    # Create a task
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"exec-task-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "Track executions",
            "trigger_type": "cron",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["id"]
    
    # Get executions (should be empty initially)
    response = await test_client.get(
        f"/tasks/{task_id}/executions",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test GET /tasks with status filter."""
    # Create an active task
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"filter-task-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "Filter test",
            "trigger_type": "cron",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    
    # Filter by active status
    response = await test_client.get(
        "/tasks?status=active",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "tasks" in data
    # All returned tasks should be active
    for task in data["tasks"]:
        assert task["status"] == "active"


@pytest.mark.asyncio
async def test_list_tasks_filter_by_trigger_type(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test GET /tasks with trigger_type filter."""
    # Create a cron task
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"cron-filter-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "Cron filter test",
            "trigger_type": "cron",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    
    # Filter by cron trigger
    response = await test_client.get(
        "/tasks?trigger_type=cron",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    # All returned tasks should have cron trigger
    for task in data["tasks"]:
        assert task["trigger_type"] == "cron"


@pytest.mark.asyncio
async def test_create_task_with_notifications(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test POST /tasks with notification configuration."""
    task_name = f"notify-task-{uuid.uuid4().hex[:8]}"
    
    response = await test_client.post(
        "/tasks",
        json={
            "name": task_name,
            "agent_id": str(test_agent.id),
            "prompt": "Send notification",
            "trigger_type": "cron",
            "trigger_config": {"cron": "0 9 * * *"},
            "notification_config": {
                "enabled": True,
                "channel": "email",
                "recipient": "test@example.com",
                "include_summary": True,
                "include_portal_link": True,
            },
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201, f"Failed: {response.text}"
    data = response.json()
    
    assert data["notification_config"]["enabled"] is True
    assert data["notification_config"]["channel"] == "email"
    assert data["notification_config"]["recipient"] == "test@example.com"


@pytest.mark.asyncio
async def test_create_task_with_insights(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test POST /tasks with insights configuration."""
    task_name = f"insights-task-{uuid.uuid4().hex[:8]}"
    
    response = await test_client.post(
        "/tasks",
        json={
            "name": task_name,
            "agent_id": str(test_agent.id),
            "prompt": "Remember results",
            "trigger_type": "cron",
            "insights_config": {
                "enabled": True,
                "max_insights": 100,
                "purge_after_days": 14,
                "include_in_context": True,
                "context_limit": 10,
            },
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201, f"Failed: {response.text}"
    data = response.json()
    
    assert data["insights_config"]["enabled"] is True
    assert data["insights_config"]["max_insights"] == 100
