"""
Pytest configuration and shared fixtures for Agent service.

Uses real JWT tokens from authz - no mocks for auth tests.
Uses shared test_utils library for auth handling.
"""
import os
import sys
from pathlib import Path

# Add shared testing library to path FIRST (before any other imports)
# When deployed: /opt/agent/test_utils/testing/
# When local: ../../test_utils/testing/
_test_utils_paths = [
    os.path.join(os.path.dirname(__file__), "..", "test_utils"),  # Deployed: /opt/agent/test_utils
    os.path.join(os.path.dirname(__file__), "..", "..", "test_utils"),  # Local: srv/test_utils
]
for _path in _test_utils_paths:
    if os.path.exists(_path) and _path not in sys.path:
        sys.path.insert(0, _path)

# CRITICAL: Load environment variables BEFORE any other imports
# This must happen at the very top of conftest.py before pytest imports test files
from testing.environment import load_env_files, create_service_auth_fixture
load_env_files(Path(__file__).parent.parent)

# Override auth_audience to None for tests (skip audience validation)
# This must be done AFTER loading env files since dotenv overrides env vars
os.environ["auth_audience"] = ""

# Clear settings cache immediately after loading env files
try:
    from app.config.settings import get_settings
    get_settings.cache_clear()
except ImportError:
    pass  # app not imported yet

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import shared testing utilities
from testing.auth import AuthTestClient, auth_client  # noqa: F401 - auth_client for fixture discovery
from testing.fixtures import require_env
from testing.database import DatabasePool, RLSEnabledPool

from app.config.settings import get_settings
from app.main import app
from app.models.base import Base
from app.models.domain import AgentDefinition, RunRecord, TokenGrant
from app.schemas.auth import Principal

# =============================================================================
# Environment setup - using shared service auth fixture factory
# =============================================================================

# Creates an autouse fixture that sets AUTHZ_AUDIENCE=agent-api
set_auth_env = create_service_auth_fixture("agent")


# =============================================================================
# Database settings
# =============================================================================

# Use the actual database from settings (PostgreSQL)
settings = get_settings()
TEST_DATABASE_URL = settings.database_url

# Get real test credentials from environment
TEST_USER_ID = os.getenv("TEST_USER_ID", "test-user-123")
TEST_USER_EMAIL = os.getenv("TEST_USER_EMAIL", "test@busibox.local")


# =============================================================================
# Session-scoped fixtures for connection pooling
# =============================================================================

# Session-scoped engine shared across all tests
_session_engine = None
_session_engine_lock = asyncio.Lock()

@pytest.fixture(scope="session")
async def session_engine():
    """
    Session-scoped database engine.
    
    Creates tables once at session start, shared by all tests.
    This avoids the "Event loop is closed" error from creating
    engines per-test.
    """
    global _session_engine
    
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_pre_ping=True)
    
    # Create tables once
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    _session_engine = engine
    yield engine
    
    # Cleanup at session end
    await engine.dispose()
    _session_engine = None


@pytest.fixture(scope="function")
async def test_session(session_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Function-scoped database session for tests that need direct DB access.
    
    Uses the session-scoped engine to avoid connection pool issues.
    Rolls back changes after each test for isolation.
    """
    SessionLocal = async_sessionmaker(session_engine, expire_on_commit=False, class_=AsyncSession)
    
    async with SessionLocal() as session:
        yield session
        # Rollback any uncommitted changes
        await session.rollback()


# =============================================================================
# Auth fixtures
# =============================================================================

@pytest.fixture(scope="session", autouse=True)
def clear_settings_cache():
    """Clear cached settings at the start of the test session."""
    from app.config.settings import get_settings
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def set_agent_auth_env(monkeypatch):
    """Set agent service auth environment variables."""
    monkeypatch.setenv("auth_issuer", "busibox-authz")
    jwks_url = os.getenv("AUTHZ_JWKS_URL", "")
    if jwks_url:
        monkeypatch.setenv("auth_jwks_url", jwks_url)


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


# =============================================================================
# HTTP Client fixtures
# =============================================================================

@pytest.fixture
async def test_client(session_engine) -> AsyncGenerator[AsyncClient, None]:
    """
    Create test HTTP client with database setup.
    
    Uses session-scoped engine to avoid connection issues.
    """
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
async def client(session_engine, mock_principal: Principal) -> AsyncClient:
    """
    Test HTTP client with mocked auth.
    
    Uses mock_principal instead of real JWT validation.
    """
    from httpx import ASGITransport
    from app.auth.dependencies import get_principal
    
    async def override_get_principal():
        return mock_principal
    
    app.dependency_overrides[get_principal] = override_get_principal
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    
    app.dependency_overrides.clear()


@pytest.fixture
async def async_client(session_engine) -> AsyncGenerator[AsyncClient, None]:
    """
    Async HTTP client for integration tests.
    
    Requires auth_headers for authenticated requests.
    """
    from httpx import ASGITransport
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# =============================================================================
# JWT Token fixtures
# =============================================================================

def _get_real_jwt_token() -> str:
    """Get a real JWT token from the authz service using shared AuthTestClient."""
    try:
        client = AuthTestClient()
        return client.get_token(audience="agent-api")
    except Exception as e:
        pytest.fail(f"Failed to get JWT token from authz: {e}")


@pytest.fixture
def mock_jwt_token() -> str:
    """
    Get a real JWT token from the authz service.
    
    Uses the shared AuthTestClient for consistent token handling.
    Will fail if authz is unavailable.
    """
    return _get_real_jwt_token()


@pytest.fixture
def auth_headers(mock_jwt_token: str) -> dict:
    """Get authentication headers with a real JWT token."""
    return {"Authorization": f"Bearer {mock_jwt_token}"}


# =============================================================================
# User ID fixtures
# =============================================================================

@pytest.fixture
def test_user_id(auth_client) -> str:
    """The test user ID from shared auth_client."""
    return auth_client.test_user_id


@pytest.fixture
def mock_user_id() -> str:
    """Real test user ID from environment."""
    return TEST_USER_ID


# =============================================================================
# Test data fixtures
# =============================================================================

@pytest.fixture
async def test_agent(test_session: AsyncSession) -> AgentDefinition:
    """Create test agent definition."""
    agent = AgentDefinition(
        name=f"test-chat-agent-{uuid.uuid4().hex[:8]}",
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
async def test_run(test_session: AsyncSession, test_agent: AgentDefinition) -> RunRecord:
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
async def test_token(test_session: AsyncSession) -> TokenGrant:
    """Create test token grant."""
    scopes = sorted(["aud:ingest-api", "read", "write"])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    token = TokenGrant(
        subject=TEST_USER_ID,
        scopes=scopes,
        token=f"test-access-token-{uuid.uuid4().hex[:8]}",
        expires_at=now + timedelta(hours=1),
    )
    test_session.add(token)
    await test_session.commit()
    await test_session.refresh(token)
    return token


@pytest.fixture
async def db_session(test_session: AsyncSession) -> AsyncSession:
    """Alias for test_session for consistency."""
    return test_session
