from contextlib import asynccontextmanager
from typing import AsyncGenerator

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


async def get_session() -> AsyncSession:
    """
    Get a database session.
    
    Note: This uses the production database. For test mode support, 
    see get_session_for_test which checks the X-Test-Mode header.
    
    Yields:
        AsyncSession connected to production database
    """
    async with SessionLocal() as session:
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
