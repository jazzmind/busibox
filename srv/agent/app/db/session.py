from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings

settings = get_settings()

# Configure connection pool to prevent exhaustion
# Default SQLAlchemy pool size is 5, max_overflow 10 = 15 total connections
# This is reasonable for agent-api, but we set explicit limits
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,  # Verify connections before using
    pool_size=5,  # Base pool size
    max_overflow=10,  # Additional connections beyond pool_size
    pool_recycle=3600,  # Recycle connections after 1 hour
    future=True
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Test database engine and session factory (only created if test mode is enabled)
test_engine = None
TestSessionLocal = None

if settings.test_mode_enabled and settings.test_database_url:
    test_engine = create_async_engine(
        settings.test_database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,
        future=True
    )
    TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


# Test mode header
TEST_MODE_HEADER = "X-Test-Mode"


def get_session_factory(use_test: bool = False):
    """Get the appropriate session factory based on test mode."""
    if use_test and TestSessionLocal:
        return TestSessionLocal
    return SessionLocal


def _should_use_test_db(request: Optional[Request] = None) -> bool:
    """
    Check if request should use test database.
    
    Returns True if:
    - Test mode is enabled in settings
    - Test database URL is configured
    - Request has X-Test-Mode: true header
    """
    if not settings.test_mode_enabled or not TestSessionLocal:
        return False
    
    if request is None:
        return False
    
    test_mode_header = request.headers.get(TEST_MODE_HEADER, "").lower()
    return test_mode_header == "true"


async def get_session(request: Request = None) -> AsyncSession:
    """
    Get a database session, routing to test or production database based on request.
    
    If X-Test-Mode: true header is present and test mode is enabled,
    returns a session connected to the test database. Otherwise returns
    a session connected to the production database.
    
    This is the primary dependency for FastAPI routes.
    
    Args:
        request: The FastAPI Request object (optional, for header checking)
    
    Yields:
        AsyncSession connected to appropriate database
    """
    use_test = _should_use_test_db(request)
    factory = TestSessionLocal if use_test else SessionLocal
    
    async with factory() as session:
        yield session


@asynccontextmanager
async def get_session_context(use_test_db: bool = False) -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for getting a database session outside of FastAPI dependency injection.
    Use this when you need a session in non-request contexts (e.g., background tasks, tools).
    
    Args:
        use_test_db: If True and test mode is enabled, use test database
    """
    factory = TestSessionLocal if (use_test_db and TestSessionLocal) else SessionLocal
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()
