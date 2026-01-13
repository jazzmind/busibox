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


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def get_session_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for getting a database session outside of FastAPI dependency injection.
    Use this when you need a session in non-request contexts (e.g., background tasks, tools).
    """
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
