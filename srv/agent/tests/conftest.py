"""Pytest configuration and shared fixtures."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator, Dict

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings
from app.main import app
from app.models.base import Base
from app.models.domain import AgentDefinition, RunRecord, TokenGrant
from app.schemas.auth import Principal

# Test database URL (use in-memory SQLite for fast tests)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

settings = get_settings()


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
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
    """Create mock authenticated principal."""
    return Principal(
        sub="test-user-123",
        email="test@example.com",
        roles=["user"],
        scopes=["search.read", "ingest.write", "rag.query"],
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
        model="anthropic:claude-3-5-sonnet",
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
        created_by=mock_principal.sub,
    )
    test_session.add(run)
    await test_session.commit()
    await test_session.refresh(run)
    return run


@pytest.fixture
async def test_token(test_session: AsyncSession, mock_principal: Principal) -> TokenGrant:
    """Create test token grant."""
    token = TokenGrant(
        subject=mock_principal.sub,
        scopes=["search.read", "ingest.write"],
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


@pytest.fixture
def mock_jwt_token() -> str:
    """Create mock JWT token for auth testing."""
    # In real tests, generate a valid JWT with test keys
    return "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0LXVzZXItMTIzIiwiZW1haWwiOiJ0ZXN0QGV4YW1wbGUuY29tIiwicm9sZXMiOlsidXNlciJdLCJzY29wZXMiOlsic2VhcmNoLnJlYWQiLCJpbmdlc3Qud3JpdGUiXX0.test-signature"

