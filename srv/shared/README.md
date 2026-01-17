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

## Test Mode Support

Services can support isolated integration testing via the `X-Test-Mode` header.
When enabled, requests with this header are routed to a separate test database.

### Setup

```python
from busibox_common import TestModeConfig, init_database_router

# Load config from environment
config = TestModeConfig.from_env("authz")  # or "ingest", "search", "agent"

# Initialize router during startup
init_database_router(
    prod_pool=pg_service,
    test_pool=pg_test_service if config.enabled else None,
    config=config,
)
```

### Usage in Routes

```python
from busibox_common import get_database

@app.post("/admin/users")
async def create_user(request: Request, data: UserCreate):
    pg = get_database(request)  # Returns test DB if X-Test-Mode: true
    async with pg.acquire() as conn:
        # Operations go to the correct database
        ...
```

### Environment Variables

```bash
# Enable test mode (default: false)
AUTHZ_TEST_MODE_ENABLED=true

# Test database credentials
TEST_DB_NAME=test_authz
TEST_DB_USER=busibox_test_user
TEST_DB_PASSWORD=testpassword
```

### Running Tests

Tests should set the header to use isolated test data:

```python
async with httpx.AsyncClient() as client:
    resp = await client.post(
        f"{API_URL}/admin/users",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Test-Mode": "true",  # Route to test database
        },
        json={"email": "test@example.com"},
    )
```

## Authentication & Token Exchange

Provides shared JWT authentication, token exchange, and RLS (Row-Level Security) utilities:

```python
from busibox_common import (
    # JWT validation and user context
    JWTAuthMiddleware,
    UserContext,
    Role,
    
    # Token exchange for service-to-service calls
    TokenExchangeClient,
    
    # RLS helpers
    set_rls_session_vars,
    WorkerRLSContext,
    
    # Scope checking
    ScopeChecker,
    require_scope,
)

# Add middleware to FastAPI app
app.add_middleware(JWTAuthMiddleware, audience="search-api")

# Use scope checking in routes
@router.post("/upload")
async def upload(request: Request, _: None = Depends(ScopeChecker("ingest.write"))):
    user = request.state.user_context
    ...

# Service-to-service token exchange
client = TokenExchangeClient()
token = await client.get_token_for_service(
    user_id="user-uuid",
    target_audience="ingest-api",
)
```

## LLM Access (LiteLLM)

Provides a unified interface for LLM access via LiteLLM proxy:

```python
from busibox_common import (
    LiteLLMClient,
    get_model_registry,
    ensure_openai_env,
)

# Direct LiteLLM calls
client = LiteLLMClient()
response = await client.chat_completion("cleanup", [
    {"role": "user", "content": "Fix this text"}
])

# Configure environment for PydanticAI
ensure_openai_env()

# Get model config from registry
registry = get_model_registry()
config = registry.get_config("cleanup")
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
