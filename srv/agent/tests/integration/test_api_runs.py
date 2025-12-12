"""
Integration tests for /runs API endpoints.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.main import app
from app.models.domain import AgentDefinition, RunRecord


@pytest.fixture
async def test_agent(test_session):
    """Create a test agent definition."""
    agent = AgentDefinition(
        name="test-agent",
        display_name="Test Agent",
        model="agent",
        instructions="Test instructions",
        tools={"names": ["search"]},
        scopes=["search.read"],
        is_active=True,
    )
    test_session.add(agent)
    await test_session.commit()
    await test_session.refresh(agent)
    return agent


@pytest.mark.asyncio
async def test_create_run_success(test_session, test_agent, mock_principal):
    """Test POST /runs creates a run successfully."""
    # Mock the agent registry to return a mock agent
    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.data = {"message": "Test response"}
    mock_agent.run = AsyncMock(return_value=mock_result)

    with patch("app.api.runs.get_principal", return_value=mock_principal):
        with patch("app.services.run_service.agent_registry.get", return_value=mock_agent):
            with patch("app.services.run_service.get_or_exchange_token") as mock_token:
                mock_token.return_value = MagicMock(access_token="test-token")

                async with AsyncClient(app=app, base_url="http://test") as client:
                    response = await client.post(
                        "/runs",
                        json={
                            "agent_id": str(test_agent.id),
                            "input": {"prompt": "test prompt"},
                            "agent_tier": "simple",
                        },
                    )

    assert response.status_code == 202
    data = response.json()
    assert "id" in data
    assert data["agent_id"] == str(test_agent.id)
    assert data["status"] in ["pending", "running", "succeeded"]


@pytest.mark.asyncio
async def test_create_run_invalid_tier(test_session, test_agent, mock_principal):
    """Test POST /runs rejects invalid agent_tier."""
    with patch("app.api.runs.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/runs",
                json={
                    "agent_id": str(test_agent.id),
                    "input": {"prompt": "test prompt"},
                    "agent_tier": "invalid",
                },
            )

    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_create_run_missing_prompt(test_session, test_agent, mock_principal):
    """Test POST /runs rejects payload without prompt."""
    with patch("app.api.runs.get_principal", return_value=mock_principal):
        with patch("app.services.run_service.agent_registry.get"):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/runs",
                    json={
                        "agent_id": str(test_agent.id),
                        "input": {},  # Missing prompt
                        "agent_tier": "simple",
                    },
                )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_run_success(test_session, test_agent, mock_principal):
    """Test GET /runs/{run_id} retrieves run details."""
    # Create a run record
    run_record = RunRecord(
        agent_id=test_agent.id,
        status="succeeded",
        input={"prompt": "test"},
        output={"message": "response"},
        events=[{"type": "created", "timestamp": "2025-01-01T00:00:00Z"}],
        created_by=mock_principal.sub,
    )
    test_session.add(run_record)
    await test_session.commit()
    await test_session.refresh(run_record)

    with patch("app.api.runs.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/runs/{run_record.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(run_record.id)
    assert data["status"] == "succeeded"
    assert data["output"]["message"] == "response"
    assert len(data["events"]) == 1


@pytest.mark.asyncio
async def test_get_run_not_found(test_session, mock_principal):
    """Test GET /runs/{run_id} returns 404 for non-existent run."""
    with patch("app.api.runs.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/runs/{uuid.uuid4()}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_run_access_denied(test_session, test_agent):
    """Test GET /runs/{run_id} returns 403 for unauthorized access."""
    # Create a run owned by different user
    run_record = RunRecord(
        agent_id=test_agent.id,
        status="succeeded",
        input={"prompt": "test"},
        created_by="other-user",
    )
    test_session.add(run_record)
    await test_session.commit()
    await test_session.refresh(run_record)

    # Mock principal as different user without admin role
    from app.schemas.auth import Principal

    other_principal = Principal(sub="requesting-user", roles=[], scopes=[], token="test")

    with patch("app.api.runs.get_principal", return_value=other_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/runs/{run_record.id}")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_runs_success(test_session, test_agent, mock_principal):
    """Test GET /runs lists runs with filtering."""
    # Create multiple runs
    for i in range(3):
        run_record = RunRecord(
            agent_id=test_agent.id,
            status="succeeded",
            input={"prompt": f"test {i}"},
            created_by=mock_principal.sub,
        )
        test_session.add(run_record)
    await test_session.commit()

    with patch("app.api.runs.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/runs")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 3


@pytest.mark.asyncio
async def test_list_runs_filter_by_agent(test_session, test_agent, mock_principal):
    """Test GET /runs filters by agent_id."""
    # Create run for test agent
    run1 = RunRecord(
        agent_id=test_agent.id,
        status="succeeded",
        input={"prompt": "test1"},
        created_by=mock_principal.sub,
    )
    test_session.add(run1)

    # Create another agent and run
    other_agent = AgentDefinition(
        name="other-agent",
        display_name="Other Agent",
        model="agent",
        instructions="Other instructions",
        is_active=True,
    )
    test_session.add(other_agent)
    await test_session.commit()
    await test_session.refresh(other_agent)

    run2 = RunRecord(
        agent_id=other_agent.id,
        status="succeeded",
        input={"prompt": "test2"},
        created_by=mock_principal.sub,
    )
    test_session.add(run2)
    await test_session.commit()

    with patch("app.api.runs.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/runs?agent_id={test_agent.id}")

    assert response.status_code == 200
    data = response.json()
    assert all(run["agent_id"] == str(test_agent.id) for run in data)


@pytest.mark.asyncio
async def test_list_runs_filter_by_status(test_session, test_agent, mock_principal):
    """Test GET /runs filters by status."""
    # Create runs with different statuses
    run1 = RunRecord(
        agent_id=test_agent.id,
        status="succeeded",
        input={"prompt": "test1"},
        created_by=mock_principal.sub,
    )
    run2 = RunRecord(
        agent_id=test_agent.id,
        status="failed",
        input={"prompt": "test2"},
        created_by=mock_principal.sub,
    )
    test_session.add(run1)
    test_session.add(run2)
    await test_session.commit()

    with patch("app.api.runs.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/runs?status=succeeded")

    assert response.status_code == 200
    data = response.json()
    assert all(run["status"] == "succeeded" for run in data)


@pytest.mark.asyncio
async def test_list_runs_respects_limit(test_session, test_agent, mock_principal):
    """Test GET /runs respects limit parameter."""
    # Create multiple runs
    for i in range(10):
        run_record = RunRecord(
            agent_id=test_agent.id,
            status="succeeded",
            input={"prompt": f"test {i}"},
            created_by=mock_principal.sub,
        )
        test_session.add(run_record)
    await test_session.commit()

    with patch("app.api.runs.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/runs?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 5
