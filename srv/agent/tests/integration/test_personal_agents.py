"""
Integration tests for personal agent management (User Story 1).

Tests:
- Personal agent filtering (only creator can see personal agents)
- Built-in agents visible to all users
- Authorization checks for agent access
- Ownership validation for updates/deletes
"""

import uuid
from typing import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import AgentDefinition


@pytest.fixture
def user_a_principal():
    """Principal for User A."""
    from app.schemas.auth import Principal
    return Principal(
        sub="user-a",
        email="user-a@example.com",
        roles=["user"],
        scopes=["agent.read", "agent.write"],
    )


@pytest.fixture
def user_b_principal():
    """Principal for User B."""
    from app.schemas.auth import Principal
    return Principal(
        sub="user-b",
        email="user-b@example.com",
        roles=["user"],
        scopes=["agent.read", "agent.write"],
    )


@pytest.fixture
async def user_a_token() -> str:
    """JWT token for User A."""
    return "Bearer mock-token-user-a"


@pytest.fixture
async def user_b_token() -> str:
    """JWT token for User B."""
    return "Bearer mock-token-user-b"


# Thread-local storage for test principal override
import threading
_test_principal_storage = threading.local()


def set_test_principal(principal):
    """Set the principal for the current request context."""
    _test_principal_storage.principal = principal


def get_test_principal():
    """Get the principal for the current request context."""
    return getattr(_test_principal_storage, 'principal', None)


def clear_test_principal():
    """Clear the test principal."""
    if hasattr(_test_principal_storage, 'principal'):
        del _test_principal_storage.principal


class UserClient:
    """
    A wrapper around AsyncClient that sets the correct principal for each request.
    """
    def __init__(self, base_client: "AsyncClient", principal):
        self._client = base_client
        self._principal = principal
    
    async def get(self, *args, **kwargs):
        set_test_principal(self._principal)
        try:
            return await self._client.get(*args, **kwargs)
        finally:
            clear_test_principal()
    
    async def post(self, *args, **kwargs):
        set_test_principal(self._principal)
        try:
            return await self._client.post(*args, **kwargs)
        finally:
            clear_test_principal()
    
    async def put(self, *args, **kwargs):
        set_test_principal(self._principal)
        try:
            return await self._client.put(*args, **kwargs)
        finally:
            clear_test_principal()
    
    async def delete(self, *args, **kwargs):
        set_test_principal(self._principal)
        try:
            return await self._client.delete(*args, **kwargs)
        finally:
            clear_test_principal()


@pytest.fixture(scope="function")
async def user_a_client(user_a_principal):
    """HTTP client authenticated as User A."""
    from httpx import ASGITransport, AsyncClient
    from app.auth.dependencies import get_principal
    from app.main import app
    
    async def test_principal_override():
        # Check thread-local storage first, fall back to user_a_principal
        p = get_test_principal()
        return p if p else user_a_principal
    
    original_override = app.dependency_overrides.get(get_principal)
    app.dependency_overrides[get_principal] = test_principal_override
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield UserClient(client, user_a_principal)
    
    # Restore
    if original_override is not None:
        app.dependency_overrides[get_principal] = original_override
    elif get_principal in app.dependency_overrides:
        del app.dependency_overrides[get_principal]


@pytest.fixture(scope="function")
async def user_b_client(user_b_principal):
    """HTTP client authenticated as User B."""
    from httpx import ASGITransport, AsyncClient
    from app.auth.dependencies import get_principal
    from app.main import app
    
    async def test_principal_override():
        # Check thread-local storage first, fall back to user_b_principal
        p = get_test_principal()
        return p if p else user_b_principal
    
    original_override = app.dependency_overrides.get(get_principal)
    app.dependency_overrides[get_principal] = test_principal_override
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield UserClient(client, user_b_principal)
    
    # Restore
    if original_override is not None:
        app.dependency_overrides[get_principal] = original_override
    elif get_principal in app.dependency_overrides:
        del app.dependency_overrides[get_principal]


@pytest.fixture
async def builtin_agent_id(db_session: AsyncSession) -> uuid.UUID:
    """Create a built-in agent visible to all users.
    
    Uses a savepoint to ensure cleanup even if test fails.
    """
    unique_name = f"builtin-test-agent-{uuid.uuid4().hex[:8]}"
    agent = AgentDefinition(
        name=unique_name,
        display_name="Built-in Test Agent",
        description="System agent for testing",
        model="agent",
        instructions="You are a helpful assistant",
        tools={"names": []},
        scopes=[],
        is_active=True,
        is_builtin=True,
        created_by=None,  # System agent, no creator
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)
    agent_id = agent.id
    
    yield agent_id
    
    # Cleanup: delete the test agent after test completes
    try:
        from sqlalchemy import delete
        await db_session.execute(
            delete(AgentDefinition).where(AgentDefinition.id == agent_id)
        )
        await db_session.commit()
    except Exception:
        pass  # Best effort cleanup


@pytest.mark.asyncio
async def test_personal_agent_filtering_user_a_creates_agent(
    user_a_client: AsyncClient,
    db_session: AsyncSession,
):
    """
    Test: User A creates personal agent → User A sees it in list.
    
    Acceptance Scenario 1: Given I am logged in as User A, When I create a 
    personal agent named "My Research Assistant", Then only I can see it in my agent list.
    """
    # Create personal agent as User A
    unique_name = f"user-a-research-assistant-{uuid.uuid4().hex[:8]}"
    response = await user_a_client.post(
        "/agents/definitions",
        json={
            "name": unique_name,
            "display_name": "My Research Assistant",
            "description": "Personal research assistant",
            "model": "agent",
            "instructions": "Help with research tasks",
            "tools": {"names": []},
            "scopes": [],
            "is_active": True,
        },
    )
    
    assert response.status_code == 201
    agent_data = response.json()
    agent_id = agent_data["id"]
    
    # Verify agent created with correct ownership
    assert agent_data["is_builtin"] is False
    assert agent_data["created_by"] == "user-a"  # Should match token sub
    
    # User A lists agents - should see their personal agent
    response = await user_a_client.get("/agents")
    
    assert response.status_code == 200
    agents = response.json()
    agent_ids = [a["id"] for a in agents]
    assert agent_id in agent_ids, "User A should see their own personal agent"


@pytest.mark.asyncio
async def test_personal_agent_not_visible_to_other_users(
    user_a_client: AsyncClient,
    user_b_client: AsyncClient,
    db_session: AsyncSession,
):
    """
    Test: User A creates personal agent → User B cannot see it.
    
    Acceptance Scenario 1 (continued): User B should not see User A's personal agent.
    """
    # Create personal agent as User A
    unique_name = f"user-a-private-agent-{uuid.uuid4().hex[:8]}"
    response = await user_a_client.post(
        "/agents/definitions",
        json={
            "name": unique_name,
            "display_name": "User A Private Agent",
            "model": "agent",
            "instructions": "Private agent",
            "tools": {"names": []},
            "scopes": [],
        },
    )
    
    assert response.status_code == 201
    agent_id = response.json()["id"]
    
    # User B lists agents - should NOT see User A's agent
    response = await user_b_client.get("/agents")
    
    assert response.status_code == 200
    agents = response.json()
    agent_ids = [a["id"] for a in agents]
    assert agent_id not in agent_ids, "User B should not see User A's personal agent"


@pytest.mark.asyncio
async def test_builtin_agents_visible_to_all_users(
    user_a_client: AsyncClient,
    user_b_client: AsyncClient,
    builtin_agent_id: uuid.UUID,
):
    """
    Test: Built-in agents visible to all users.
    
    Acceptance Scenario 2: Given I am logged in as any user, When I view the agent list,
    Then I see all built-in agents plus only my own personal agents.
    """
    # User A should see built-in agent
    response = await user_a_client.get("/agents")
    
    assert response.status_code == 200
    agents = response.json()
    agent_ids = [a["id"] for a in agents]
    assert str(builtin_agent_id) in agent_ids, "User A should see built-in agent"
    
    # User B should also see built-in agent
    response = await user_b_client.get("/agents")
    
    assert response.status_code == 200
    agents = response.json()
    agent_ids = [a["id"] for a in agents]
    assert str(builtin_agent_id) in agent_ids, "User B should see built-in agent"


@pytest.mark.asyncio
async def test_unauthorized_access_to_personal_agent_returns_404(
    user_a_client: AsyncClient,
    user_b_client: AsyncClient,
    db_session: AsyncSession,
):
    """
    Test: User B tries to access User A's personal agent by ID → 404 Not Found.
    
    Acceptance Scenario 3: Given User B tries to access User A's personal agent via API,
    When they make the request, Then they receive a 404 Not Found error.
    """
    # Create personal agent as User A
    unique_name = f"user-a-secret-agent-{uuid.uuid4().hex[:8]}"
    agent = AgentDefinition(
        name=unique_name,
        display_name="User A Secret Agent",
        model="agent",
        instructions="Secret instructions",
        tools={"names": []},
        scopes=[],
        is_active=True,
        is_builtin=False,
        created_by="user-a",
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)
    
    # User A can access their own agent
    response = await user_a_client.get(f"/agents/{agent.id}")
    assert response.status_code == 200
    
    # User B tries to access User A's agent → 404 (not 403, to hide existence)
    response = await user_b_client.get(f"/agents/{agent.id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Agent not found"


@pytest.mark.asyncio
async def test_builtin_agent_accessible_to_all_users(
    user_a_client: AsyncClient,
    user_b_client: AsyncClient,
    builtin_agent_id: uuid.UUID,
):
    """
    Test: Built-in agents accessible to all users via direct ID access.
    """
    # User A can access built-in agent
    response = await user_a_client.get(f"/agents/{builtin_agent_id}")
    assert response.status_code == 200
    agent = response.json()
    assert agent["is_builtin"] is True
    
    # User B can also access built-in agent
    response = await user_b_client.get(f"/agents/{builtin_agent_id}")
    assert response.status_code == 200
    agent = response.json()
    assert agent["is_builtin"] is True


@pytest.mark.asyncio
async def test_personal_agent_created_with_correct_ownership(
    user_a_client: AsyncClient,
    db_session: AsyncSession,
):
    """
    Test: Personal agents created with is_builtin=False and created_by set to user.
    """
    unique_name = f"ownership-test-agent-{uuid.uuid4().hex[:8]}"
    response = await user_a_client.post(
        "/agents/definitions",
        json={
            "name": unique_name,
            "model": "agent",
            "instructions": "Test agent",
            "tools": {"names": []},
        },
    )
    
    assert response.status_code == 201
    agent = response.json()
    
    # Verify ownership fields
    assert agent["is_builtin"] is False, "User-created agents should not be built-in"
    assert agent["created_by"] == "user-a", "created_by should match authenticated user"
    
    # Verify in database
    stmt = select(AgentDefinition).where(AgentDefinition.id == uuid.UUID(agent["id"]))
    result = await db_session.execute(stmt)
    db_agent = result.scalar_one()
    
    assert db_agent.is_builtin is False
    assert db_agent.created_by == "user-a"









