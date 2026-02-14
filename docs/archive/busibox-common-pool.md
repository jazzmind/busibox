# Busibox Common Pool Module

**Created:** 2026-01-21
**Status:** Active
**Category:** Development

## Overview

The `busibox_common.pool` module provides a unified, standardized way to manage asyncpg connection pools across all Busibox services. It replaces the individual `PostgresService` implementations in each service with a shared, well-tested solution.

### Why Use the Shared Pool?

1. **Consistency**: All services use the same pool configuration and behavior
2. **Singleton Pattern**: Prevents connection leaks from accidentally creating multiple pools
3. **Event Loop Handling**: Automatically reconnects when event loop changes (common in testing)
4. **RLS Support**: Built-in Row-Level Security session variable support
5. **Simplified Maintenance**: Bug fixes and improvements benefit all services

## Quick Start

### Basic Usage

```python
from busibox_common import AsyncPGPoolManager

# Create from config dict
pool_manager = AsyncPGPoolManager.from_config({
    "postgres_host": "10.96.200.203",
    "postgres_port": 5432,
    "postgres_db": "busibox",
    "postgres_user": "app_user",
    "postgres_password": "secret",
})

# Or create from environment variables
pool_manager = AsyncPGPoolManager.from_env()

# Connect on startup
await pool_manager.connect()

# Use connections
async with pool_manager.acquire() as conn:
    result = await conn.fetch("SELECT * FROM users")

# Disconnect on shutdown
await pool_manager.disconnect()
```

### FastAPI Integration

```python
from fastapi import FastAPI
from busibox_common import AsyncPGPoolManager

app = FastAPI()
pg_pool = AsyncPGPoolManager.from_env()

@app.on_event("startup")
async def startup():
    await pg_pool.connect()

@app.on_event("shutdown")
async def shutdown():
    await pg_pool.disconnect()

@app.get("/users")
async def get_users():
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM users")
        return [dict(row) for row in rows]
```

### With RLS (Row-Level Security)

```python
@app.get("/files")
async def get_files(request: Request):
    user_id = request.state.user_id
    role_ids = request.state.role_ids
    
    async with pg_pool.acquire(
        rls_user_id=user_id,
        rls_role_ids=role_ids
    ) as conn:
        # RLS session variables are automatically set
        rows = await conn.fetch("SELECT * FROM files")
        return [dict(row) for row in rows]
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `10.96.200.203` | Database host |
| `POSTGRES_PORT` | `5432` | Database port |
| `POSTGRES_DB` | `busibox` | Database name |
| `POSTGRES_USER` | `app_user` | Database user |
| `POSTGRES_PASSWORD` | (empty) | Database password |
| `POSTGRES_POOL_MIN` | `2` | Minimum pool size |
| `POSTGRES_POOL_MAX` | `10` | Maximum pool size |
| `POSTGRES_POOL_RECYCLE` | `3600` | Connection recycle time (seconds) |

### Config Dictionary

The `from_config()` method accepts these keys (with fallback alternatives):

```python
config = {
    # Primary keys (recommended)
    "postgres_host": "10.96.200.203",
    "postgres_port": 5432,
    "postgres_db": "busibox",
    "postgres_user": "app_user",
    "postgres_password": "secret",
    "postgres_pool_min": 2,
    "postgres_pool_max": 10,
    "postgres_pool_recycle": 3600,
    
    # Alternative keys (also supported)
    "host": "10.96.200.203",
    "port": 5432,
    "database": "busibox",
    "user": "app_user",
    "password": "secret",
    "min_size": 2,
    "max_size": 10,
}
```

### PoolConfig Dataclass

For fine-grained control, use `PoolConfig` directly:

```python
from busibox_common import PoolConfig, AsyncPGPoolManager

config = PoolConfig(
    host="10.96.200.203",
    port=5432,
    database="busibox",
    user="app_user",
    password="secret",
    min_size=2,
    max_size=10,
    max_inactive_connection_lifetime=3600.0,
)

pool_manager = AsyncPGPoolManager(config)
```

## API Reference

### AsyncPGPoolManager

#### Constructor

```python
AsyncPGPoolManager(config: PoolConfig)
```

Creates a pool manager with the specified configuration.

#### Class Methods

| Method | Description |
|--------|-------------|
| `from_config(config: dict)` | Create from a config dictionary |
| `from_env()` | Create from environment variables |

#### Instance Methods

| Method | Description |
|--------|-------------|
| `connect()` | Create the connection pool |
| `disconnect()` | Close the connection pool |
| `acquire(rls_user_id=None, rls_role_ids=None)` | Context manager to get a connection |
| `on_connect(callback)` | Register callback to run after connect |
| `execute(query, *args)` | Execute a query (convenience method) |
| `fetch(query, *args)` | Fetch all rows (convenience method) |
| `fetchrow(query, *args)` | Fetch one row (convenience method) |
| `fetchval(query, *args)` | Fetch one value (convenience method) |

#### Properties

| Property | Description |
|----------|-------------|
| `is_connected` | Boolean indicating if pool is connected |
| `pool` | The underlying asyncpg.Pool (or None) |
| `config` | The PoolConfig used |

### Module-Level Functions

For simpler usage patterns, you can use module-level convenience functions:

```python
from busibox_common import init_pool, get_pool, reset_pool

# Initialize once at startup
pool = init_pool(config)  # or init_pool() to use env vars
await pool.connect()

# Use anywhere in your app
async with get_pool().acquire() as conn:
    await conn.fetch("SELECT 1")

# Reset for testing
reset_pool()
```

## Migration Guide

### Migrating from Service-Specific PostgresService

#### Before (Old Pattern)

```python
# srv/search/src/api/services/postgres.py
class PostgresService:
    def __init__(self, config: dict):
        self.host = config.get("postgres_host", "10.96.200.203")
        self.port = config.get("postgres_port", 5432)
        # ... more config
        self.pool = None
    
    async def connect(self):
        if not self.pool:
            self.pool = await asyncpg.create_pool(...)
    
    async def disconnect(self):
        if self.pool:
            await self.pool.close()
            self.pool = None

# srv/search/src/api/main.py
pg_service = PostgresService(config)

@app.on_event("startup")
async def startup():
    await pg_service.connect()
```

#### After (New Pattern)

```python
# srv/search/src/api/main.py
from busibox_common import AsyncPGPoolManager

pg_pool = AsyncPGPoolManager.from_config(config)

@app.on_event("startup")
async def startup():
    await pg_pool.connect()

@app.on_event("shutdown")
async def shutdown():
    await pg_pool.disconnect()

# In routes, use pg_pool.acquire() instead of pg_service.pool.acquire()
```

### Migrating with RLS (authz, ingest)

#### Before

```python
# authz: uses user_id and role_ids parameters
async with pg_service.acquire(user_id, role_ids) as conn:
    ...

# ingest: uses request object
async with pg_service.acquire(request) as conn:
    ...
```

#### After

```python
# Both patterns: extract user_id and role_ids, pass to acquire()
user_id = request.state.user_id
role_ids = request.state.role_ids

async with pg_pool.acquire(
    rls_user_id=user_id,
    rls_role_ids=role_ids
) as conn:
    ...
```

### Keeping Service-Specific Logic

If your service has domain-specific database methods (like `ingest.PostgresService.create_file_record()`), you can:

1. **Wrap the pool manager in a service class:**

```python
from busibox_common import AsyncPGPoolManager

class IngestDatabaseService:
    """Domain-specific database operations for ingest service."""
    
    def __init__(self, pool: AsyncPGPoolManager):
        self._pool = pool
    
    async def create_file_record(self, file_id: str, ...):
        async with self._pool.acquire() as conn:
            await conn.execute(...)
    
    async def update_status(self, file_id: str, stage: str, ...):
        async with self._pool.acquire() as conn:
            await conn.execute(...)

# Usage in main.py
pg_pool = AsyncPGPoolManager.from_config(config)
ingest_db = IngestDatabaseService(pg_pool)
```

2. **Use the pool directly for simple operations:**

```python
# For services like search that just need basic queries
async with pg_pool.acquire() as conn:
    result = await conn.fetch("SELECT * FROM documents WHERE ...")
```

## Schema Initialization

Register schema initialization callbacks with `on_connect()`:

```python
from busibox_common import AsyncPGPoolManager

async def ensure_schema(pool: AsyncPGPoolManager):
    """Initialize database schema on startup."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS my_table (
                id UUID PRIMARY KEY,
                name TEXT NOT NULL
            )
        """)

pg_pool = AsyncPGPoolManager.from_config(config)
pg_pool.on_connect(ensure_schema)

# Schema will be initialized when connect() is called
await pg_pool.connect()
```

## Testing

### Unit Tests

```python
import pytest
from busibox_common import AsyncPGPoolManager, PoolConfig

@pytest.fixture
async def pg_pool():
    """Create a test pool that connects to test database."""
    config = PoolConfig(
        host="localhost",
        database="test_db",
        user="test_user",
        password="test_pass",
    )
    pool = AsyncPGPoolManager(config)
    await pool.connect()
    yield pool
    await pool.disconnect()

async def test_fetch_users(pg_pool):
    async with pg_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (name) VALUES ('Test')")
        rows = await conn.fetch("SELECT * FROM users")
        assert len(rows) == 1
```

### Event Loop Changes

The pool manager handles event loop changes automatically. This is particularly useful when tests create new event loops:

```python
async def test_with_new_loop():
    pool = AsyncPGPoolManager.from_env()
    await pool.connect()
    
    # If event loop changes (common in pytest-asyncio)
    # pool will automatically reconnect
    async with pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
        assert result == 1
```

## Troubleshooting

### Connection Pool Exhausted

**Symptom:** `TooManyConnectionsError` or connections timing out

**Causes:**
1. Connections not being returned to pool (not using `async with`)
2. Multiple pool instances being created
3. Pool size too small for workload

**Solutions:**
1. Always use `async with pool.acquire() as conn:`
2. Use a single pool instance (singleton pattern)
3. Increase `POSTGRES_POOL_MAX` if needed

### Event Loop Errors

**Symptom:** `RuntimeError: Event loop is closed`

**Cause:** Pool was created in a different event loop

**Solution:** The pool manager handles this automatically by detecting loop changes and reconnecting. If you see this error, ensure you're:
1. Using `async with` for all pool operations
2. Not storing connections across async boundaries

### RLS Not Working

**Symptom:** Queries return no data or wrong data

**Causes:**
1. RLS session variables not set
2. Wrong user_id or role_ids passed

**Solutions:**
1. Use `acquire(rls_user_id=..., rls_role_ids=...)` 
2. Verify user context is correctly extracted from JWT
3. Check RLS policies in PostgreSQL

## Related Documentation

- [PostgreSQL Connection Pooling](https://www.postgresql.org/docs/current/libpq-connect.html)
- [asyncpg Documentation](https://magicstack.github.io/asyncpg/)
- [Row-Level Security](../architecture/rls-design.md)
- [Test Mode Support](../development/test-mode.md)
