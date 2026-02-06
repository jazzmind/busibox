"""
Unified AsyncPG Connection Pool Manager for Busibox Services.

This module provides a shared, standardized way to manage asyncpg connection pools
across all Busibox services (data, search, authz, etc.).

Features:
- Singleton pattern per database configuration
- Event loop change handling (important for testing)
- Configurable pool sizes via environment variables or config dict
- Optional RLS (Row-Level Security) context support
- Thread-safe pool initialization with asyncio locks

Usage:
    from busibox_common import AsyncPGPoolManager
    
    # Create from config dict
    pool_manager = AsyncPGPoolManager.from_config(config)
    
    # Create from environment variables
    pool_manager = AsyncPGPoolManager.from_env()
    
    # Use in FastAPI startup/shutdown
    @app.on_event("startup")
    async def startup():
        await pool_manager.connect()
    
    @app.on_event("shutdown")
    async def shutdown():
        await pool_manager.disconnect()
    
    # Acquire connections
    async with pool_manager.acquire() as conn:
        result = await conn.fetch("SELECT * FROM my_table")
    
    # With RLS context (for user-scoped access)
    async with pool_manager.acquire(rls_user_id=user_id, rls_role_ids=role_ids) as conn:
        result = await conn.fetch("SELECT * FROM my_table")

Environment Variables:
    POSTGRES_HOST: Database host (default: 10.96.200.203)
    POSTGRES_PORT: Database port (default: 5432)
    POSTGRES_DB: Database name (default: busibox)
    POSTGRES_USER: Database user
    POSTGRES_PASSWORD: Database password
    POSTGRES_POOL_MIN: Minimum pool size (default: 2)
    POSTGRES_POOL_MAX: Maximum pool size (default: 10)
    POSTGRES_POOL_RECYCLE: Connection recycle time in seconds (default: 3600)
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import asyncpg
import structlog

logger = structlog.get_logger()


@dataclass
class PoolConfig:
    """Configuration for AsyncPG connection pool."""
    
    host: str = "postgres"
    port: int = 5432
    database: str = "busibox"
    user: str = "app_user"
    password: str = ""
    
    # Pool settings
    min_size: int = 2
    max_size: int = 10
    max_inactive_connection_lifetime: float = 3600.0  # 1 hour
    
    # Optional: command to run on each new connection
    init_command: Optional[str] = None
    
    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "PoolConfig":
        """
        Create PoolConfig from a dictionary.
        
        Supports common config key patterns used across services.
        """
        return cls(
            host=config.get("postgres_host", config.get("host", "postgres")),
            port=int(config.get("postgres_port", config.get("port", 5432))),
            database=config.get("postgres_db", config.get("database", "busibox")),
            user=config.get("postgres_user", config.get("user", "app_user")),
            password=config.get("postgres_password", config.get("password", "")),
            min_size=int(config.get("postgres_pool_min", config.get("min_size", 2))),
            max_size=int(config.get("postgres_pool_max", config.get("max_size", 10))),
            max_inactive_connection_lifetime=float(
                config.get("postgres_pool_recycle", config.get("max_inactive_connection_lifetime", 3600))
            ),
        )
    
    @classmethod
    def from_env(cls) -> "PoolConfig":
        """Create PoolConfig from environment variables."""
        return cls(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            database=os.environ.get("POSTGRES_DB", "busibox"),
            user=os.environ.get("POSTGRES_USER", "app_user"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
            min_size=int(os.environ.get("POSTGRES_POOL_MIN", "2")),
            max_size=int(os.environ.get("POSTGRES_POOL_MAX", "10")),
            max_inactive_connection_lifetime=float(
                os.environ.get("POSTGRES_POOL_RECYCLE", "3600")
            ),
        )


class AsyncPGPoolManager:
    """
    Unified asyncpg connection pool manager.
    
    Features:
    - Singleton pattern per database (use same instance across your app)
    - Event loop change handling (reconnects if loop changes, common in tests)
    - Configurable pool sizes
    - Optional RLS context support via acquire() parameters
    - Thread-safe pool initialization with asyncio locks
    
    Usage:
        # Create manager
        pool_manager = AsyncPGPoolManager.from_config(config)
        
        # Connect on startup
        await pool_manager.connect()
        
        # Use connections
        async with pool_manager.acquire() as conn:
            await conn.fetch("SELECT 1")
        
        # With RLS context
        async with pool_manager.acquire(
            rls_user_id="user-uuid",
            rls_role_ids=["role-1", "role-2"]
        ) as conn:
            await conn.fetch("SELECT * FROM files")
        
        # Disconnect on shutdown
        await pool_manager.disconnect()
    """
    
    def __init__(self, config: PoolConfig):
        """
        Initialize pool manager with configuration.
        
        Args:
            config: PoolConfig instance with connection settings
        """
        self.config = config
        self.pool: Optional[asyncpg.Pool] = None
        self._pool_loop: Optional[asyncio.AbstractEventLoop] = None
        self._connect_lock: Optional[asyncio.Lock] = None
        
        # Optional callbacks for schema initialization
        self._on_connect_callbacks: List[Callable] = []
    
    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "AsyncPGPoolManager":
        """
        Create pool manager from a config dictionary.
        
        Args:
            config: Dictionary with database configuration
        
        Returns:
            Configured AsyncPGPoolManager instance
        """
        return cls(PoolConfig.from_dict(config))
    
    @classmethod
    def from_env(cls) -> "AsyncPGPoolManager":
        """
        Create pool manager from environment variables.
        
        Returns:
            Configured AsyncPGPoolManager instance
        """
        return cls(PoolConfig.from_env())
    
    def on_connect(self, callback: Callable) -> None:
        """
        Register a callback to run after pool is connected.
        
        Useful for schema initialization, running migrations, etc.
        
        Args:
            callback: Async function to call after connect()
        """
        self._on_connect_callbacks.append(callback)
    
    def _get_lock(self) -> asyncio.Lock:
        """Get or create a lock for the current event loop."""
        current_loop = asyncio.get_running_loop()
        if self._connect_lock is None or self._pool_loop != current_loop:
            self._connect_lock = asyncio.Lock()
        return self._connect_lock
    
    async def connect(self) -> None:
        """
        Create the connection pool.
        
        Safe to call multiple times - will reconnect if event loop changed.
        """
        current_loop = asyncio.get_running_loop()
        
        # Check if we need to reconnect due to event loop change
        if self.pool and self._pool_loop and self._pool_loop != current_loop:
            logger.warning(
                "Event loop changed, closing old pool and reconnecting",
                database=self.config.database,
            )
            try:
                await asyncio.wait_for(self.pool.close(), timeout=1.0)
            except Exception as e:
                logger.warning("Failed to close old pool", error=str(e))
            self.pool = None
            self._pool_loop = None
        
        if not self.pool:
            self.pool = await asyncpg.create_pool(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.user,
                password=self.config.password,
                min_size=self.config.min_size,
                max_size=self.config.max_size,
                max_inactive_connection_lifetime=self.config.max_inactive_connection_lifetime,
            )
            self._pool_loop = current_loop
            
            logger.info(
                "PostgreSQL connection pool created",
                host=self.config.host,
                database=self.config.database,
                min_size=self.config.min_size,
                max_size=self.config.max_size,
            )
            
            # Run on_connect callbacks
            for callback in self._on_connect_callbacks:
                try:
                    await callback(self)
                except Exception as e:
                    logger.error("on_connect callback failed", error=str(e))
                    raise
    
    async def disconnect(self) -> None:
        """Close the connection pool."""
        if self.pool:
            try:
                await self.pool.close()
            except RuntimeError as e:
                # Handle "Event loop is closed" during test teardown
                if "Event loop is closed" in str(e):
                    logger.warning("Could not close pool - event loop already closed")
                else:
                    raise
            self.pool = None
            self._pool_loop = None
            logger.info(
                "PostgreSQL connection pool closed",
                database=self.config.database,
            )
    
    async def _ensure_connected(self) -> None:
        """Ensure pool is connected, handling event loop changes."""
        current_loop = asyncio.get_running_loop()
        if not self.pool or self._pool_loop != current_loop:
            async with self._get_lock():
                # Double-check after acquiring lock
                if not self.pool or self._pool_loop != current_loop:
                    await self.connect()
    
    @asynccontextmanager
    async def acquire(
        self,
        rls_user_id: Optional[str] = None,
        rls_role_ids: Optional[List[str]] = None,
    ) -> AsyncIterator[asyncpg.Connection]:
        """
        Get a connection from the pool.
        
        Optionally sets RLS session variables for row-level security.
        
        Args:
            rls_user_id: User ID for RLS context (optional)
            rls_role_ids: List of role IDs for RLS context (optional)
        
        Yields:
            asyncpg.Connection from the pool
        
        Example:
            async with pool_manager.acquire() as conn:
                result = await conn.fetch("SELECT * FROM users")
            
            async with pool_manager.acquire(
                rls_user_id="user-123",
                rls_role_ids=["role-1", "role-2"]
            ) as conn:
                # RLS session variables are set
                result = await conn.fetch("SELECT * FROM files")
        """
        await self._ensure_connected()
        
        async with self.pool.acquire() as conn:
            # Set RLS session variables if provided
            if rls_user_id is not None:
                await self._set_rls_vars(conn, rls_user_id, rls_role_ids or [])
            yield conn
    
    async def _set_rls_vars(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        role_ids: List[str],
    ) -> None:
        """
        Set PostgreSQL session variables for RLS enforcement.
        
        Uses SET (not SET LOCAL) to persist for the connection session.
        
        Args:
            conn: asyncpg connection
            user_id: User ID to set
            role_ids: List of role IDs for role-based access
        """
        # Convert role_ids list to CSV string (RLS policies use string_to_array)
        role_ids_csv = ",".join(role_ids) if role_ids else ""
        
        # Set all RLS session variables
        await conn.execute(f"SET app.user_id = '{user_id}'")
        await conn.execute(f"SET app.user_role_ids_read = '{role_ids_csv}'")
        await conn.execute(f"SET app.user_role_ids_create = '{role_ids_csv}'")
        await conn.execute(f"SET app.user_role_ids_update = '{role_ids_csv}'")
        await conn.execute(f"SET app.user_role_ids_delete = '{role_ids_csv}'")
        
        logger.debug(
            "RLS session variables set",
            user_id=user_id,
            role_count=len(role_ids),
        )
    
    @property
    def is_connected(self) -> bool:
        """Check if pool is currently connected."""
        return self.pool is not None
    
    async def execute(self, query: str, *args) -> str:
        """
        Execute a query and return the status.
        
        Convenience method for simple queries without needing to acquire.
        """
        async with self.acquire() as conn:
            return await conn.execute(query, *args)
    
    async def fetch(self, query: str, *args) -> List[asyncpg.Record]:
        """
        Fetch all rows from a query.
        
        Convenience method for simple queries without needing to acquire.
        """
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)
    
    async def fetchrow(self, query: str, *args) -> Optional[asyncpg.Record]:
        """
        Fetch a single row from a query.
        
        Convenience method for simple queries without needing to acquire.
        """
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)
    
    async def fetchval(self, query: str, *args) -> Any:
        """
        Fetch a single value from a query.
        
        Convenience method for simple queries without needing to acquire.
        """
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)


# Module-level convenience functions for simpler usage patterns

_default_pool: Optional[AsyncPGPoolManager] = None


def get_pool() -> AsyncPGPoolManager:
    """
    Get the default pool manager.
    
    Must call init_pool() first.
    
    Returns:
        The default AsyncPGPoolManager instance
    
    Raises:
        RuntimeError: If init_pool() was not called
    """
    global _default_pool
    if _default_pool is None:
        raise RuntimeError(
            "Pool not initialized. Call init_pool() first, "
            "or use AsyncPGPoolManager directly."
        )
    return _default_pool


def init_pool(config: Optional[Dict[str, Any]] = None) -> AsyncPGPoolManager:
    """
    Initialize the default pool manager.
    
    Args:
        config: Optional config dict. If not provided, uses environment variables.
    
    Returns:
        The default AsyncPGPoolManager instance
    """
    global _default_pool
    if config:
        _default_pool = AsyncPGPoolManager.from_config(config)
    else:
        _default_pool = AsyncPGPoolManager.from_env()
    return _default_pool


def reset_pool() -> None:
    """
    Reset the default pool manager.
    
    Useful for testing to ensure clean state between tests.
    Does NOT disconnect - call disconnect() first if needed.
    """
    global _default_pool
    _default_pool = None
