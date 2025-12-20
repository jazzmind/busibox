"""Pytest configuration and shared fixtures."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator, Dict

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings
from app.main import app
from app.models.base import Base
from app.models.domain import AgentDefinition, RunRecord, TokenGrant
from app.schemas.auth import Principal

# Use the actual database from settings (PostgreSQL)
# This ensures tests use the same database types (JSONB, UUID, etc.)
settings = get_settings()
TEST_DATABASE_URL = settings.database_url

# Get real test credentials from environment (created by bootstrap-test-credentials.sh)
TEST_USER_ID = os.getenv("TEST_USER_ID", "test-user-123")
TEST_USER_EMAIL = os.getenv("TEST_USER_EMAIL", "test@busibox.local")
TEST_CLIENT_ID = os.getenv("AUTHZ_TEST_CLIENT_ID", "")
TEST_CLIENT_SECRET = os.getenv("AUTHZ_TEST_CLIENT_SECRET", "")


@pytest.fixture(scope="function")
def event_loop():
    """Create event loop for async tests (function-scoped to avoid conflicts)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture
async def test_engine():
    """Create test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    # Cleanup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create test database session."""
    SessionLocal = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    
    async with SessionLocal() as session:
        yield session


@pytest.fixture
def mock_principal() -> Principal:
    """Create mock authenticated principal using real test user."""
    return Principal(
        sub=TEST_USER_ID,
        email=TEST_USER_EMAIL,
        roles=["Admin", "User"],
        scopes=["read", "write", "admin"],
    )


@pytest.fixture
def admin_principal() -> Principal:
    """Create mock admin principal."""
    return Principal(
        sub="admin-user-456",
        email="admin@example.com",
        roles=["admin", "user"],
        scopes=["admin.read", "admin.write", "search.read", "ingest.write", "rag.query"],
    )


@pytest.fixture
async def test_agent(test_session: AsyncSession) -> AgentDefinition:
    """Create test agent definition."""
    agent = AgentDefinition(
        name="test-chat-agent",
        display_name="Test Chat Agent",
        description="Test agent for unit tests",
        model="agent",
        instructions="You are a test assistant. Be concise.",
        tools={"names": ["search", "ingest"]},
        scopes=["search.read", "ingest.write"],
        is_active=True,
    )
    test_session.add(agent)
    await test_session.commit()
    await test_session.refresh(agent)
    return agent


@pytest.fixture
async def test_run(test_session: AsyncSession, test_agent: AgentDefinition, mock_principal: Principal) -> RunRecord:
    """Create test run record."""
    run = RunRecord(
        agent_id=test_agent.id,
        status="succeeded",
        input={"prompt": "test query"},
        output={"message": "test response"},
        events=[],
        created_by=TEST_USER_ID,
    )
    test_session.add(run)
    await test_session.commit()
    await test_session.refresh(run)
    return run


@pytest.fixture
async def test_token(test_session: AsyncSession, mock_principal: Principal) -> TokenGrant:
    """Create test token grant."""
    # Cache key includes inferred downstream audience marker.
    scopes = sorted(["aud:ingest-api", "read", "write"])
    token = TokenGrant(
        subject=TEST_USER_ID,
        scopes=scopes,
        token="test-access-token-123",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    test_session.add(token)
    await test_session.commit()
    await test_session.refresh(token)
    return token


@pytest.fixture
async def test_client() -> AsyncGenerator[AsyncClient, None]:
    """Create test HTTP client."""
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _get_real_jwt_token() -> str:
    """Get a real JWT token from the authz service using test credentials."""
    # Load from .env file since pytest may not have environment variables loaded
    from dotenv import load_dotenv
    load_dotenv()
    
    test_client_id = os.getenv("AUTHZ_TEST_CLIENT_ID")
    test_client_secret = os.getenv("AUTHZ_TEST_CLIENT_SECRET")
    
    if not test_client_id or not test_client_secret:
        raise ValueError(
            "Test credentials not configured in .env. Run: bash scripts/bootstrap-test-credentials.sh test"
        )
    
    authz_url = str(settings.auth_token_url).rsplit("/oauth/token", 1)[0]
    
    # Get a token using client credentials grant
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{authz_url}/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": test_client_id,
                "client_secret": test_client_secret,
                "audience": "agent-api",
                "scope": "read write admin",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        return data["access_token"]


@pytest.fixture
async def mock_jwt_token() -> str:
    """Get a real JWT token for auth testing from the authz service.
    
    This fixture always fetches a real token - it will fail if authz is unavailable.
    """
    token = await _get_real_jwt_token()
    return token


# Additional fixtures for new tests

@pytest.fixture
def mock_user_id() -> str:
    """Real test user ID from environment."""
    return TEST_USER_ID


@pytest.fixture
def mock_token() -> str:
    """Mock bearer token for testing."""
    return "mock-token-test-user"


@pytest.fixture
async def db_session(test_session: AsyncSession) -> AsyncSession:
    """Alias for test_session for consistency."""
    return test_session


@pytest.fixture
async def client(test_engine, mock_principal: Principal) -> AsyncClient:
    """Test HTTP client with mocked auth (uses mock_principal and test_engine for DB setup)."""
    from httpx import ASGITransport
    from app.auth.dependencies import get_principal
    
    async def override_get_principal():
        return mock_principal
    
    app.dependency_overrides[get_principal] = override_get_principal
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    
    app.dependency_overrides.clear()





