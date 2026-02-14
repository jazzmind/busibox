---
title: "Shared Library - busibox_common"
category: "developer"
order: 124
description: "Shared Python library for common utilities across Busibox services"
published: true
---

# Shared Library: busibox_common

## Overview

`busibox_common` is a shared Python library providing common utilities for all Busibox services. It's located at `srv/shared/busibox_common/`.

## Installation

Services can install the shared library by adding to their requirements:

```
# In requirements.txt or pyproject.toml
../shared  # Local development
```

Or by adding the path to `PYTHONPATH`:
```bash
export PYTHONPATH="${PYTHONPATH}:/srv/shared"
```

## SchemaManager

The `SchemaManager` class provides a fluent API for defining database schemas with idempotent creation.

### Basic Usage

```python
from busibox_common import SchemaManager

schema = SchemaManager()

# Add PostgreSQL extensions
schema.add_extension("pgcrypto")
schema.add_extension("uuid-ossp")

# Add tables (CREATE TABLE IF NOT EXISTS)
schema.add_table("""
    CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email TEXT NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
""")

# Add indexes (CREATE INDEX IF NOT EXISTS)
schema.add_index("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")

# Add inline migrations (for column additions, etc.)
schema.add_migration("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'users' AND column_name = 'status'
        ) THEN
            ALTER TABLE users ADD COLUMN status TEXT;
        END IF;
    END $$
""")

# Apply to database connection (idempotent)
async with pool.acquire() as conn:
    await schema.apply(conn)
```

### Key Features

- **Idempotent**: Safe to run on every service startup
- **Ordered execution**: Extensions → Tables → Indexes → Migrations
- **Connection agnostic**: Works with asyncpg or SQLAlchemy connections
- **Fallback support**: Inline fallback if shared library isn't installed

## DatabaseInitializer

Combines SchemaManager with Alembic for hybrid schema management:

```python
from busibox_common import DatabaseInitializer, SchemaManager

# With SchemaManager only
schema = SchemaManager()
schema.add_table("...")

db_init = DatabaseInitializer(
    database_url="postgresql+asyncpg://user:pass@host/db",
    schema_manager=schema,
    service_name="my-service",
)
await db_init.ensure_ready()

# With Alembic only
db_init = DatabaseInitializer(
    database_url="postgresql+asyncpg://user:pass@host/db",
    alembic_config_path="/app/alembic.ini",
    service_name="agent",
)
await db_init.ensure_ready()

# With both (SchemaManager creates tables, Alembic handles migrations)
db_init = DatabaseInitializer(
    database_url="postgresql+asyncpg://user:pass@host/db",
    alembic_config_path="/app/alembic.ini",
    schema_manager=schema,
    service_name="hybrid-service",
)
await db_init.ensure_ready()
```

## Service Schema Files

Each service defines its schema in a dedicated file:

| Service | Schema Location | Pattern |
|---------|-----------------|---------|
| Agent | `srv/agent/alembic/` | Alembic migrations |
| AuthZ | `srv/authz/src/schema.py` | SchemaManager |
| Ingest | `srv/ingest/src/schema.py` | SchemaManager |

### AuthZ Schema Example

```python
# srv/authz/src/schema.py
from busibox_common import SchemaManager

def get_authz_schema() -> SchemaManager:
    schema = SchemaManager()
    schema.add_extension("pgcrypto")
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_users (
            user_id uuid PRIMARY KEY,
            email text NOT NULL,
            status text NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    # ... more tables
    
    return schema
```

### Service Integration

```python
# In PostgresService.ensure_schema()
from schema import get_authz_schema

async def ensure_schema(self) -> None:
    schema = get_authz_schema()
    async with self.pool.acquire() as conn:
        await schema.apply(conn)
```

## Testing

The shared library includes a comprehensive test suite:

```bash
cd /path/to/busibox
python srv/shared/test_schema_manager.py
```

Tests verify:
1. Package imports correctly
2. SchemaManager basic functionality
3. Service schemas import and apply
4. Database isolation between test/production

## Related Documentation

- [Database Separation Migration](../../guides/database-separation-migration.md)
- [Database Commands Reference](./database-commands.md)
- [Testing Architecture](../../architecture/08-tests.md)
