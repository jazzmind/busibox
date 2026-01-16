# Busibox Common

Shared utilities for all Busibox services.

## Database Initialization

Provides a unified approach to database schema management:

### Option 1: SchemaManager Only (like authz)

For services that define schema in code and create tables on startup:

```python
from busibox_common import SchemaManager

# Define schema
schema = SchemaManager()
schema.add_extension("pgcrypto")
schema.add_table('''
    CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email TEXT NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
''')
schema.add_migration('''
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'users' AND column_name = 'status'
        ) THEN
            ALTER TABLE users ADD COLUMN status TEXT;
        END IF;
    END $$;
''')

# Apply on startup
async with pool.acquire() as conn:
    await schema.apply(conn)
```

### Option 2: Alembic Only (like agent)

For services using Alembic migrations:

```python
from busibox_common import DatabaseInitializer

db_init = DatabaseInitializer(
    database_url="postgresql+asyncpg://user:pass@localhost/db",
    alembic_config_path="/app/alembic.ini",
    service_name="agent",
)

# On startup
await db_init.ensure_ready()
```

### Option 3: Combined (Recommended)

Use SchemaManager for initial tables and Alembic for migrations:

```python
from busibox_common import DatabaseInitializer, SchemaManager

schema = SchemaManager()
schema.add_extension("uuid-ossp")
schema.add_table("CREATE TABLE IF NOT EXISTS ...")

db_init = DatabaseInitializer(
    database_url=settings.database_url,
    alembic_config_path="/app/alembic.ini",
    schema_manager=schema,
    service_name="my-service",
)

await db_init.ensure_ready()
```

## Installation

Add to your service's requirements:

```
# In requirements.txt or pyproject.toml
../shared  # or absolute path during development
```

Or for production:
```
busibox-common @ file:///srv/shared
```
