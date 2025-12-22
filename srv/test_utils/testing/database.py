"""
PostgreSQL connection pooling for tests.

Provides a shared connection pool across all tests to avoid:
- Connection exhaustion from too many concurrent connections
- Slow test startup from creating new connections per test
- Connection leaks from improperly closed connections

Usage:
    from testing.database import get_db_pool, db_connection
    
    # Get the shared pool
    pool = await get_db_pool()
    
    # Use a connection from the pool
    async with db_connection() as conn:
        result = await conn.fetch("SELECT * FROM users")
    
    # Or use the fixture
    async def test_something(db_conn):
        result = await db_conn.fetch("SELECT * FROM users")
"""

import os
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
import pytest

from .fixtures import get_env


# Global connection pool (shared across all tests)
_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


def get_db_config() -> dict:
    """
    Get database configuration from environment variables.
    
    Returns:
        Dict with database connection parameters
    """
    return {
        "host": get_env("POSTGRES_HOST", "localhost"),
        "port": int(get_env("POSTGRES_PORT", "5432")),
        "user": get_env("POSTGRES_USER", "busibox"),
        "password": get_env("POSTGRES_PASSWORD", ""),
        "database": get_env("POSTGRES_DB", "busibox"),
    }


async def get_db_pool(
    min_size: int = 2,
    max_size: int = 10,
    **kwargs
) -> asyncpg.Pool:
    """
    Get or create the shared database connection pool.
    
    The pool is created lazily on first use and reused across all tests.
    
    Args:
        min_size: Minimum number of connections in pool
        max_size: Maximum number of connections in pool
        **kwargs: Additional arguments passed to asyncpg.create_pool
        
    Returns:
        asyncpg connection pool
    """
    global _pool
    
    async with _pool_lock:
        if _pool is None:
            config = get_db_config()
            _pool = await asyncpg.create_pool(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
                min_size=min_size,
                max_size=max_size,
                **kwargs
            )
        return _pool


async def close_db_pool():
    """
    Close the shared database connection pool.
    
    Should be called at the end of the test session.
    """
    global _pool
    
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
            _pool = None


@asynccontextmanager
async def db_connection():
    """
    Context manager for acquiring a database connection from the pool.
    
    Usage:
        async with db_connection() as conn:
            result = await conn.fetch("SELECT 1")
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def db_transaction():
    """
    Context manager for a database transaction.
    
    Automatically rolls back on exception.
    
    Usage:
        async with db_transaction() as conn:
            await conn.execute("INSERT INTO users ...")
            # Commits on exit, rolls back on exception
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn


async def set_rls_context(conn: asyncpg.Connection, user_id: str, role_ids: list = None):
    """
    Set Row-Level Security context variables on a connection.
    
    Args:
        conn: Database connection
        user_id: User ID for RLS
        role_ids: List of role IDs for RLS (optional)
    """
    await conn.execute(f"SET LOCAL app.user_id = '{user_id}'")
    if role_ids:
        await conn.execute(f"SET LOCAL app.user_role_ids_read = '{','.join(role_ids)}'")
    else:
        await conn.execute("SET LOCAL app.user_role_ids_read = ''")


# =============================================================================
# Pytest Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """
    Create an event loop for the test session.
    
    This is required for session-scoped async fixtures.
    """
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def db_pool():
    """
    Session-scoped database connection pool.
    
    The pool is created once per test session and closed at the end.
    
    Usage:
        async def test_something(db_pool):
            async with db_pool.acquire() as conn:
                result = await conn.fetch("SELECT 1")
    """
    pool = await get_db_pool()
    yield pool
    await close_db_pool()


@pytest.fixture
async def db_conn(db_pool):
    """
    Function-scoped database connection from the pool.
    
    Acquires a connection for each test and releases it after.
    
    Usage:
        async def test_something(db_conn):
            result = await db_conn.fetch("SELECT 1")
    """
    async with db_pool.acquire() as conn:
        yield conn


@pytest.fixture
async def db_conn_with_rls(db_conn, auth_client):
    """
    Database connection with RLS context set for the test user.
    
    Sets the app.user_id and app.user_role_ids_read session variables
    based on the test user's current roles.
    
    Usage:
        async def test_rls(db_conn_with_rls, auth_client):
            # Connection has RLS context for test user
            result = await db_conn_with_rls.fetch("SELECT * FROM protected_table")
    """
    # Get test user's current roles
    roles = auth_client.get_user_roles()
    role_ids = [r["id"] for r in roles]
    
    # Set RLS context
    await set_rls_context(db_conn, auth_client.test_user_id, role_ids)
    
    yield db_conn


@pytest.fixture
async def db_transaction_rollback(db_pool):
    """
    Database connection wrapped in a transaction that rolls back after the test.
    
    Useful for tests that modify data but should not persist changes.
    
    Usage:
        async def test_insert(db_transaction_rollback):
            await db_transaction_rollback.execute("INSERT INTO users ...")
            # Changes are rolled back after test
    """
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()

