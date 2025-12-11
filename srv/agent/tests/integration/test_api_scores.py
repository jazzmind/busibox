"""
Integration tests for scores API endpoints.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.models.domain import EvalDefinition, RunRecord


@pytest.mark.asyncio
async def test_execute_score_success(test_client: AsyncClient, test_session, mock_jwt_token: str):
    """Test POST /scores/execute scores a completed run."""
    # Create scorer
    scorer = EvalDefinition(
        name="test-latency-scorer",
        description="Latency scorer",
        config={"type": "latency", "threshold_ms": 5000},
        is_active=True,
    )
    test_session.add(scorer)
    
    # Create completed run
    now = datetime.now(timezone.utc)
    run_record = RunRecord(
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={"prompt": "test"},
        output={"message": "success"},
        created_by="test-user-123",
        created_at=now,
        updated_at=now + timedelta(seconds=2),
    )
    test_session.add(run_record)
    await test_session.commit()
    await test_session.refresh(scorer)
    await test_session.refresh(run_record)
    
    # Execute scorer
    payload = {
        "scorer_id": str(scorer.id),
        "run_id": str(run_record.id),
    }
    
    response = await test_client.post(
        "/scores/execute",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify score result
    assert data["run_id"] == str(run_record.id)
    assert data["scorer_name"] == "latency"
    assert data["score"] == 1.0
    assert data["passed"] is True
    assert "details" in data
    assert data["details"]["latency_ms"] == 2000


@pytest.mark.asyncio
async def test_execute_score_not_found(test_client: AsyncClient, mock_jwt_token: str):
    """Test POST /scores/execute returns 400 for non-existent scorer."""
    payload = {
        "scorer_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
    }
    
    response = await test_client.post(
        "/scores/execute",
        json=payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_execute_score_requires_auth(test_client: AsyncClient):
    """Test POST /scores/execute requires authentication."""
    payload = {
        "scorer_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
    }
    
    response = await test_client.post("/scores/execute", json=payload)
    
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_aggregates_empty(test_client: AsyncClient, mock_jwt_token: str):
    """Test GET /scores/aggregates with no runs."""
    response = await test_client.get(
        "/scores/aggregates",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert "total_runs" in data
    assert "successful_runs" in data
    assert "success_rate" in data


@pytest.mark.asyncio
async def test_get_aggregates_with_runs(test_client: AsyncClient, test_session, mock_jwt_token: str):
    """Test GET /scores/aggregates calculates statistics."""
    agent_id = uuid.uuid4()
    
    # Create runs
    run1 = RunRecord(
        agent_id=agent_id,
        status="succeeded",
        input={},
        output={},
        created_by="test",
    )
    run2 = RunRecord(
        agent_id=agent_id,
        status="succeeded",
        input={},
        output={},
        created_by="test",
    )
    run3 = RunRecord(
        agent_id=agent_id,
        status="failed",
        input={},
        output={},
        created_by="test",
    )
    
    test_session.add_all([run1, run2, run3])
    await test_session.commit()
    
    # Get aggregates
    response = await test_client.get(
        f"/scores/aggregates?agent_id={agent_id}",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["total_runs"] == 3
    assert data["successful_runs"] == 2
    assert abs(data["success_rate"] - 0.667) < 0.01
    assert data["agent_id"] == str(agent_id)


@pytest.mark.asyncio
async def test_get_aggregates_requires_auth(test_client: AsyncClient):
    """Test GET /scores/aggregates requires authentication."""
    response = await test_client.get("/scores/aggregates")
    
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_score_workflow(test_client: AsyncClient, test_session, mock_jwt_token: str):
    """Test complete scoring workflow: create scorer, execute, get aggregates."""
    # 1. Create scorer
    scorer_payload = {
        "name": "workflow-scorer",
        "description": "Test scorer",
        "config": {"type": "success"},
        "is_active": True,
    }
    
    scorer_response = await test_client.post(
        "/agents/evals",
        json=scorer_payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert scorer_response.status_code == 201
    scorer_id = scorer_response.json()["id"]
    
    # 2. Create completed run
    run_record = RunRecord(
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={"prompt": "test"},
        output={"message": "success"},
        created_by="test-user-123",
    )
    test_session.add(run_record)
    await test_session.commit()
    await test_session.refresh(run_record)
    
    # 3. Execute scorer
    score_payload = {
        "scorer_id": scorer_id,
        "run_id": str(run_record.id),
    }
    
    score_response = await test_client.post(
        "/scores/execute",
        json=score_payload,
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert score_response.status_code == 200
    score_data = score_response.json()
    assert score_data["score"] == 1.0
    assert score_data["passed"] is True
    
    # 4. Get aggregates
    aggregates_response = await test_client.get(
        "/scores/aggregates",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert aggregates_response.status_code == 200
    aggregates = aggregates_response.json()
    assert aggregates["successful_runs"] >= 1
