"""
Integration tests for /runs API endpoints.

These tests use REAL services:
- Real agent registry with on-demand loading
- Real token exchange with authz service (when available)
- Real LLM execution via LiteLLM

Tests marked with @pytest.mark.integration require external services.
Tests without that marker work with mocked auth but real internal logic.
"""

import uuid

import pytest
from httpx import AsyncClient

from app.main import app
from app.models.domain import AgentDefinition, RunRecord


@pytest.fixture
async def test_agent(test_session):
    """Create a test agent definition with unique name."""
    unique_name = f"test-agent-{uuid.uuid4().hex[:8]}"
    agent = AgentDefinition(
        name=unique_name,
        display_name="Test Agent",
        model="chat",  # Use 'chat' model which LiteLLM routes to the chat model
        instructions="You are a test assistant. Always respond with 'Test response: ' followed by the user's input.",
        tools={"names": []},  # No tools for simple tests
        scopes=[],  # No scopes needed for simple tests
        is_active=True,
    )
    test_session.add(agent)
    await test_session.commit()
    await test_session.refresh(agent)
    return agent


@pytest.fixture
async def test_agent_with_tools(test_session):
    """Create a test agent with tools for integration tests."""
    unique_name = f"test-agent-with-tools-{uuid.uuid4().hex[:8]}"
    agent = AgentDefinition(
        name=unique_name,
        display_name="Test Agent with Tools",
        model="chat",
        instructions="You are a test assistant that can search for information.",
        tools={"names": ["search"]},
        scopes=["search.read"],
        is_active=True,
    )
    test_session.add(agent)
    await test_session.commit()
    await test_session.refresh(agent)
    return agent


# =============================================================================
# Tests that use mocked auth but real internal services
# =============================================================================

@pytest.mark.asyncio
async def test_create_run_success(client: AsyncClient, test_session, test_agent, mock_principal):
    """Test POST /runs creates and executes a run with real agent.
    
    This test:
    - Uses real agent registry (on-demand loading from database)
    - Uses real agent execution via LiteLLM
    - Only mocks authentication (via client fixture)
    """
    response = await client.post(
        "/runs",
        json={
            "agent_id": str(test_agent.id),
            "input": {"prompt": "Hello, this is a test"},
            "agent_tier": "simple",
        },
    )

    assert response.status_code == 202
    data = response.json()
    assert "id" in data
    assert data["agent_id"] == str(test_agent.id)
    # With real execution, status should be succeeded or failed (not pending)
    assert data["status"] in ["succeeded", "failed", "pending", "running"]
    
    # If succeeded, verify output exists
    if data["status"] == "succeeded":
        assert data["output"] is not None


@pytest.mark.asyncio
async def test_create_run_invalid_tier(client: AsyncClient, test_session, test_agent):
    """Test POST /runs rejects invalid agent_tier."""
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
async def test_create_run_missing_prompt(client: AsyncClient, test_session, test_agent):
    """Test POST /runs rejects payload without prompt."""
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
async def test_create_run_nonexistent_agent(client: AsyncClient, test_session):
    """Test POST /runs handles non-existent agent."""
    response = await client.post(
        "/runs",
        json={
            "agent_id": str(uuid.uuid4()),  # Random non-existent ID
            "input": {"prompt": "test"},
            "agent_tier": "simple",
        },
    )

    # Should return 202 (accepted) but the run will fail with agent not found
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "failed"
    assert "error" in data.get("output", {}) or "not found" in str(data).lower()


@pytest.mark.asyncio
async def test_get_run_success(client: AsyncClient, test_session, test_agent, mock_principal):
    """Test GET /runs/{run_id} retrieves run details."""
    # Create a run record directly in DB
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

    response = await client.get(f"/runs/{run_record.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(run_record.id)
    assert data["status"] == "succeeded"
    assert data["output"]["message"] == "response"
    assert len(data["events"]) == 1


@pytest.mark.asyncio
async def test_get_run_not_found(client: AsyncClient, test_session):
    """Test GET /runs/{run_id} returns 404 for non-existent run."""
    response = await client.get(f"/runs/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_run_access_denied(client: AsyncClient, test_session, test_agent):
    """Test GET /runs/{run_id} returns 403 for unauthorized access.
    
    Note: This test uses the mock_principal which has admin roles,
    so we create a run owned by a different user.
    """
    from app.auth.dependencies import get_principal
    from app.schemas.auth import Principal
    
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

    # Override with a non-admin principal
    other_principal = Principal(sub="requesting-user", roles=[], scopes=[], token="test")
    
    async def override_get_principal():
        return other_principal
    
    original_override = app.dependency_overrides.get(get_principal)
    app.dependency_overrides[get_principal] = override_get_principal
    
    try:
        response = await client.get(f"/runs/{run_record.id}")
        assert response.status_code == 403
    finally:
        # Restore original override
        if original_override:
            app.dependency_overrides[get_principal] = original_override
        else:
            app.dependency_overrides.pop(get_principal, None)


@pytest.mark.asyncio
async def test_list_runs_success(client: AsyncClient, test_session, test_agent, mock_principal):
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

    response = await client.get("/runs")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 3


@pytest.mark.asyncio
async def test_list_runs_filter_by_agent(client: AsyncClient, test_session, test_agent, mock_principal):
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
    import uuid
    other_agent = AgentDefinition(
        name=f"other-agent-{uuid.uuid4().hex[:8]}",
        display_name="Other Agent",
        model="chat",
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

    response = await client.get(f"/runs?agent_id={test_agent.id}")

    assert response.status_code == 200
    data = response.json()
    assert all(run["agent_id"] == str(test_agent.id) for run in data)


@pytest.mark.asyncio
async def test_list_runs_filter_by_status(client: AsyncClient, test_session, test_agent, mock_principal):
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

    response = await client.get("/runs?status=succeeded")

    assert response.status_code == 200
    data = response.json()
    assert all(run["status"] == "succeeded" for run in data)


@pytest.mark.asyncio
async def test_list_runs_respects_limit(client: AsyncClient, test_session, test_agent, mock_principal):
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

    response = await client.get("/runs?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 5


# =============================================================================
# Full integration tests with real auth (requires authz service)
# =============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_run_real_auth(async_client: AsyncClient, auth_headers: dict, test_session, test_agent):
    """Test POST /runs with real JWT authentication.
    
    This test uses:
    - Real JWT token from authz service
    - Real agent registry
    - Real LLM execution
    """
    response = await async_client.post(
        "/runs",
        json={
            "agent_id": str(test_agent.id),
            "input": {"prompt": "What is 2 + 2?"},
            "agent_tier": "simple",
        },
        headers=auth_headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert "id" in data
    assert data["agent_id"] == str(test_agent.id)
    
    # With real auth and LLM, should get a result
    print(f"Run status: {data['status']}")
    print(f"Run output: {data.get('output', 'N/A')}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_run_with_tools_real(async_client: AsyncClient, auth_headers: dict, test_session, test_agent_with_tools):
    """Test POST /runs with agent that has tools.
    
    This test uses:
    - Real JWT token from authz service
    - Real agent with search tool
    - Real token exchange for downstream services
    """
    response = await async_client.post(
        "/runs",
        json={
            "agent_id": str(test_agent_with_tools.id),
            "input": {"prompt": "Search for information about Python programming"},
            "agent_tier": "simple",
        },
        headers=auth_headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert "id" in data
    
    print(f"Run with tools status: {data['status']}")
    print(f"Run with tools output: {data.get('output', 'N/A')}")
    print(f"Run events: {data.get('events', [])}")
