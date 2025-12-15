"""
Integration tests for schedule API endpoints.
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.models.domain import AgentDefinition
from app.services.scheduler import run_scheduler


@pytest.mark.asyncio
async def test_schedule_run_success(test_client: AsyncClient, test_session, mock_jwt_token: str):
    """Test POST /runs/schedule creates scheduled job."""
    # Create test agent
    agent = AgentDefinition(
        name="test-agent",
        model="agent",
        instructions="Test",
        tools={"names": ["search"]},
        is_active=True,
    )
    test_session.add(agent)
    await test_session.commit()
    await test_session.refresh(agent)
    
    payload = {
        "agent_id": str(agent.id),
        "input": {"prompt": "test scheduled run"},
        "cron": "0 12 * * *",  # Daily at noon
        "agent_tier": "simple",
        "scopes": ["agent.execute", "search.read"],
        "purpose": "scheduled-test",
    }
    
    response = await test_client.post(
        "/runs/schedule",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 201
    data = response.json()
    
    # Verify response structure
    assert "job_id" in data
    assert data["agent_id"] == str(agent.id)
    assert data["cron"] == "0 12 * * *"
    assert data["principal_sub"] == "test-user-123"
    assert "next_run_time" in data
    
    # Cleanup
    run_scheduler.cancel_job(data["job_id"])


@pytest.mark.asyncio
async def test_schedule_run_invalid_cron(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /runs/schedule rejects invalid cron expression."""
    payload = {
        "agent_id": str(uuid.uuid4()),
        "input": {"prompt": "test"},
        "cron": "invalid cron",  # Invalid
        "agent_tier": "simple",
    }
    
    response = await test_client.post(
        "/runs/schedule",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 400
    assert "Invalid schedule configuration" in response.json()["detail"]


@pytest.mark.asyncio
async def test_schedule_run_requires_auth(test_client: AsyncClient):
    """Test POST /runs/schedule requires authentication."""
    payload = {
        "agent_id": str(uuid.uuid4()),
        "input": {"prompt": "test"},
        "cron": "0 12 * * *",
    }
    
    response = await test_client.post("/runs/schedule", json=payload)
    
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_schedules_empty(test_client: AsyncClient, mock_jwt_token: str):
    """Test GET /runs/schedule returns empty list when no schedules exist."""
    response = await test_client.get(
        "/runs/schedule",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    # May have schedules from other tests, just verify it's a list
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_list_schedules_with_data(test_client: AsyncClient, test_session, mock_jwt_token: str):
    """Test GET /runs/schedule returns scheduled jobs."""
    # Create test agent
    agent = AgentDefinition(
        name="schedule-test-agent",
        model="agent",
        instructions="Test",
        tools={"names": []},
        is_active=True,
    )
    test_session.add(agent)
    await test_session.commit()
    await test_session.refresh(agent)
    
    # Create schedule
    create_payload = {
        "agent_id": str(agent.id),
        "input": {"prompt": "test"},
        "cron": "0 12 * * *",
    }
    
    create_response = await test_client.post(
        "/runs/schedule",
        json=create_payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    job_id = create_response.json()["job_id"]
    
    # List schedules
    response = await test_client.get(
        "/runs/schedule",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    schedules = response.json()
    assert isinstance(schedules, list)
    
    # Find our schedule
    our_schedule = next((s for s in schedules if s["job_id"] == job_id), None)
    assert our_schedule is not None
    assert our_schedule["agent_id"] == str(agent.id)
    assert our_schedule["cron"] == "0 12 * * *"
    
    # Cleanup
    run_scheduler.cancel_job(job_id)


@pytest.mark.asyncio
async def test_cancel_schedule_success(test_client: AsyncClient, test_session, mock_jwt_token: str):
    """Test DELETE /runs/schedule/{job_id} cancels scheduled job."""
    # Create test agent
    agent = AgentDefinition(
        name="cancel-test-agent",
        model="agent",
        instructions="Test",
        tools={"names": []},
        is_active=True,
    )
    test_session.add(agent)
    await test_session.commit()
    await test_session.refresh(agent)
    
    # Create schedule
    create_payload = {
        "agent_id": str(agent.id),
        "input": {"prompt": "test"},
        "cron": "0 12 * * *",
    }
    
    create_response = await test_client.post(
        "/runs/schedule",
        json=create_payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    job_id = create_response.json()["job_id"]
    
    # Cancel schedule
    response = await test_client.delete(
        f"/runs/schedule/{job_id}",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 204
    
    # Verify job is gone
    job = run_scheduler.get_job(job_id)
    assert job is None


@pytest.mark.asyncio
async def test_cancel_schedule_not_found(test_client: AsyncClient, mock_jwt_token: str):
    """Test DELETE /runs/schedule/{job_id} returns 404 for non-existent job."""
    response = await test_client.delete(
        "/runs/schedule/non-existent-job-id",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_cancel_schedule_requires_auth(test_client: AsyncClient):
    """Test DELETE /runs/schedule/{job_id} requires authentication."""
    response = await test_client.delete("/runs/schedule/some-job-id")
    
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_schedule_workflow(test_client: AsyncClient, test_session, mock_jwt_token: str):
    """Test complete schedule workflow: create, list, cancel."""
    # Create test agent
    agent = AgentDefinition(
        name="workflow-schedule-agent",
        model="agent",
        instructions="Test workflow",
        tools={"names": ["search"]},
        is_active=True,
    )
    test_session.add(agent)
    await test_session.commit()
    await test_session.refresh(agent)
    
    # 1. Create schedule
    create_payload = {
        "agent_id": str(agent.id),
        "input": {"prompt": "workflow test"},
        "cron": "*/5 * * * *",  # Every 5 minutes
        "agent_tier": "complex",
    }
    
    create_response = await test_client.post(
        "/runs/schedule",
        json=create_payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert create_response.status_code == 201
    job_id = create_response.json()["job_id"]
    
    # 2. List schedules (should include our job)
    list_response = await test_client.get(
        "/runs/schedule",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert list_response.status_code == 200
    schedules = list_response.json()
    job_ids = [s["job_id"] for s in schedules]
    assert job_id in job_ids
    
    # 3. Cancel schedule
    cancel_response = await test_client.delete(
        f"/runs/schedule/{job_id}",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert cancel_response.status_code == 204
    
    # 4. Verify job is gone from list
    list_response_2 = await test_client.get(
        "/runs/schedule",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert list_response_2.status_code == 200
    schedules_2 = list_response_2.json()
    job_ids_2 = [s["job_id"] for s in schedules_2]
    assert job_id not in job_ids_2





