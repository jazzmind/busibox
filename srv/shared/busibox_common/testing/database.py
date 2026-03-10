"""
Database utilities for testing.

Provides connection pooling and session management for PostgreSQL tests.
Handles connection lifecycle to avoid "too many connections" errors.
"""

import os
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

import asyncpg
import pytest


class DatabasePool:
    """
    Manages a PostgreSQL connection pool for tests.
    
    Ensures connections are properly pooled and cleaned up,
    avoiding "too many connections" errors during test runs.
    
    Usage:
        pool = DatabasePool()
        await pool.initialize()
        
        async with pool.acquire() as conn:
            result = await conn.fetch("SELECT * FROM users")
        
        await pool.close()
    """
    
    _instance: Optional["DatabasePool"] = None
    _pools: Dict[str, asyncpg.Pool] = {}
    
    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        min_size: int = 1,
        max_size: int = 5,
    ):
        """
        Initialize database pool configuration.
        
        Args:
            host: PostgreSQL host (default from POSTGRES_HOST)
            port: PostgreSQL port (default from POSTGRES_PORT or 5432)
            database: Database name (default from POSTGRES_DB)
            user: Database user (default from POSTGRES_USER)
            password: Database password (default from POSTGRES_PASSWORD)
            min_size: Minimum pool size
            max_size: Maximum pool size
        """
        self.host = host or os.getenv("POSTGRES_HOST", "localhost")
        self.port = port or int(os.getenv("POSTGRES_PORT", "5432"))
        self.database = database or os.getenv("POSTGRES_DB", "busibox_test")
        self.user = user or os.getenv("POSTGRES_USER", "busibox_test_user")
        self.password = password or os.getenv("POSTGRES_PASSWORD", "")
        self.min_size = min_size
        self.max_size = max_size
        self._pool: Optional[asyncpg.Pool] = None
        self._initialized = False
    
    @classmethod
    def get_instance(cls) -> "DatabasePool":
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @property
    def dsn(self) -> str:
        """Get the database connection string."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"
    
    async def initialize(self) -> None:
        """
        Initialize the connection pool.
        
        Safe to call multiple times - will only create pool once.
        """
        if self._initialized and self._pool is not None:
            return
        
        try:
            self._pool = await asyncpg.create_pool(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                min_size=self.min_size,
                max_size=self.max_size,
                command_timeout=30,
            )
            self._initialized = True
        except Exception as e:
            pytest.fail(f"Failed to create database pool: {e}")
    
    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._initialized = False
    
    @asynccontextmanager
    async def acquire(self):
        """
        Acquire a connection from the pool.
        
        Usage:
            async with pool.acquire() as conn:
                await conn.fetch("SELECT 1")
        """
        if not self._initialized:
            await self.initialize()
        
        async with self._pool.acquire() as conn:
            yield conn
    
    async def execute(self, query: str, *args) -> str:
        """Execute a query and return status."""
        async with self.acquire() as conn:
            return await conn.execute(query, *args)
    
    async def fetch(self, query: str, *args) -> list:
        """Fetch all rows from a query."""
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)
    
    async def fetchrow(self, query: str, *args) -> Optional[asyncpg.Record]:
        """Fetch a single row from a query."""
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)
    
    async def fetchval(self, query: str, *args) -> Any:
        """Fetch a single value from a query."""
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)


class RLSEnabledPool(DatabasePool):
    """
    Database pool that sets Row-Level Security session variables.
    
    Automatically sets app.user_id and app.user_role_ids_read/create/update/delete
    on each connection acquisition.
    
    Handles event loop changes by recreating the pool when needed.
    """
    
    def __init__(
        self,
        user_id: Optional[str] = None,
        role_ids: Optional[list] = None,
        **kwargs
    ):
        """
        Initialize RLS-enabled pool.
        
        Args:
            user_id: User ID for RLS (default from TEST_USER_ID)
            role_ids: List of role IDs for RLS
            **kwargs: Additional DatabasePool arguments
        """
        super().__init__(**kwargs)
        self.rls_user_id = user_id or os.getenv("TEST_USER_ID", "")
        self.rls_role_ids = role_ids or []
        self._current_loop = None
    
    def set_rls_context(self, user_id: str, role_ids: list = None) -> None:
        """Update RLS context for subsequent connections."""
        self.rls_user_id = user_id
        self.rls_role_ids = role_ids or []
    
    async def _ensure_pool_for_current_loop(self):
        """
        Ensure the pool is initialized for the current event loop.
        
        Recreates the pool if the event loop has changed (common in pytest).
        """
        current_loop = asyncio.get_running_loop()
        
        if self._current_loop is not current_loop:
            # Event loop changed, need to recreate pool
            if self._pool is not None:
                try:
                    await self._pool.close()
                except Exception:
                    pass  # Ignore errors closing old pool
                self._pool = None
                self._initialized = False
            self._current_loop = current_loop
        
        if not self._initialized:
            await self.initialize()
            self._current_loop = current_loop
    
    @asynccontextmanager
    async def acquire(self):
        """
        Acquire a connection with RLS variables set.
        
        Sets app.user_id and app.user_role_ids_* using SET (not SET LOCAL)
        so they persist for the entire connection without requiring a transaction.
        
        The connection is reset when returned to the pool, clearing
        the session variables.
        
        Handles event loop changes automatically.
        """
        await self._ensure_pool_for_current_loop()
        
        async with self._pool.acquire() as conn:
            # Set RLS session variables using SET (persists for connection)
            if self.rls_user_id:
                await conn.execute(f"SET app.user_id = '{self.rls_user_id}'")
            
            if self.rls_role_ids:
                # Set role IDs as CSV for all CRUD operations
                role_ids_csv = ",".join(self.rls_role_ids)
                await conn.execute(f"SET app.user_role_ids_read = '{role_ids_csv}'")
                await conn.execute(f"SET app.user_role_ids_create = '{role_ids_csv}'")
                await conn.execute(f"SET app.user_role_ids_update = '{role_ids_csv}'")
                await conn.execute(f"SET app.user_role_ids_delete = '{role_ids_csv}'")
            
            yield conn
    
    @asynccontextmanager
    async def transaction(self):
        """
        Acquire a connection with RLS variables set, wrapped in a transaction.
        
        Use this when you need transactional semantics (commit/rollback).
        
        Usage:
            async with rls_pool.transaction() as conn:
                await conn.execute("INSERT INTO ...")
                # Transaction commits on exit, rolls back on exception
        """
        if not self._initialized:
            await self.initialize()
        
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Set RLS session variables
                if self.rls_user_id:
                    await conn.execute(f"SET LOCAL app.user_id = '{self.rls_user_id}'")
                if self.rls_role_ids:
                    import json
                    role_ids_json = json.dumps(self.rls_role_ids)
                    await conn.execute(f"SET LOCAL app.user_role_ids = '{role_ids_json}'")
                
                yield conn


# =============================================================================
# Pytest Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests (session-scoped)."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def db_pool():
    """
    Session-scoped database pool fixture.
    
    Creates a single pool for all tests in the session,
    avoiding connection exhaustion.
    
    Usage:
        async def test_something(db_pool):
            async with db_pool.acquire() as conn:
                result = await conn.fetch("SELECT 1")
    """
    pool = DatabasePool.get_instance()
    await pool.initialize()
    yield pool
    await pool.close()


@pytest.fixture(scope="function")
async def db_conn(db_pool: DatabasePool):
    """
    Function-scoped database connection fixture.
    
    Acquires a connection from the session pool for each test.
    Connection is returned to pool after test.
    
    Usage:
        async def test_something(db_conn):
            result = await db_conn.fetch("SELECT 1")
    """
    async with db_pool.acquire() as conn:
        yield conn


@pytest.fixture(scope="session")
async def rls_pool():
    """
    Session-scoped RLS-enabled database pool.
    
    Usage:
        async def test_rls(rls_pool):
            rls_pool.set_rls_context(user_id="...", role_ids=["..."])
            async with rls_pool.acquire() as conn:
                # RLS variables are set automatically
                result = await conn.fetch("SELECT * FROM protected_table")
    """
    pool = RLSEnabledPool()
    await pool.initialize()
    yield pool
    await pool.close()


# =============================================================================
# Test Utilities
# =============================================================================

async def check_postgres_connection(
    host: Optional[str] = None,
    port: Optional[int] = None,
    database: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
) -> bool:
    """
    Check if PostgreSQL is reachable.
    
    Returns True if connection succeeds, False otherwise.
    """
    try:
        conn = await asyncpg.connect(
            host=host or os.getenv("POSTGRES_HOST", "localhost"),
            port=port or int(os.getenv("POSTGRES_PORT", "5432")),
            database=database or os.getenv("POSTGRES_DB", "busibox_test"),
            user=user or os.getenv("POSTGRES_USER", "busibox_test_user"),
            password=password or os.getenv("POSTGRES_PASSWORD", ""),
            timeout=5,
        )
        await conn.close()
        return True
    except Exception:
        return False


async def wait_for_postgres(
    timeout: int = 30,
    interval: float = 1.0,
    **kwargs
) -> bool:
    """
    Wait for PostgreSQL to become available.
    
    Args:
        timeout: Maximum seconds to wait
        interval: Seconds between checks
        **kwargs: Connection parameters
    
    Returns:
        True if connected, False if timeout
    """
    import time
    start = time.time()
    
    while time.time() - start < timeout:
        if await check_postgres_connection(**kwargs):
            return True
        await asyncio.sleep(interval)
    
    return False
