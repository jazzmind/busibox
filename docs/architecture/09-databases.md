---
title: "Database Architecture"
category: "developer"
order: 10
description: "PostgreSQL database architecture, schema management, and test isolation"
published: true
---

# Database Architecture

**Created**: 2026-01-16  
**Last Updated**: 2026-01-16  
**Status**: Active  
**Category**: Architecture  
**Related Docs**:  
- `architecture/00-overview.md`  
- `guides/database-separation-migration.md`  
- `development/reference/database-commands.md`  
- `development/reference/shared-library-busibox-common.md`

## Overview

Busibox uses PostgreSQL as its primary relational database, hosted in the `pg-lxc` container. Each service has its own dedicated database for isolation and independent scaling.

## Database Layout

### Production/Staging Databases

| Database | Owner | Service | Description |
|----------|-------|---------|-------------|
| `agent_server` | `busibox_user` | Agent API | Agent definitions, conversations, workflows, tools |
| `authz` | `busibox_user` | AuthZ | Users, roles, sessions, OAuth clients, audit logs |
| `files` | `busibox_user` | Ingest | File metadata, chunks, processing history |
| `ai_portal` | `busibox_user` | AI Portal | Portal-specific data (Prisma managed) |
| `busibox` | `busibox_user` | Legacy | Deprecated shared database |

### Test Databases

For pytest isolation, a dedicated test user owns identical databases:

| Database | Owner | Purpose |
|----------|-------|---------|
| `test_agent_server` | `busibox_test_user` | Agent service pytest |
| `test_authz` | `busibox_test_user` | AuthZ service pytest |
| `test_files` | `busibox_test_user` | Ingest service pytest |

This architecture ensures:
- **Isolation**: Tests never pollute production data
- **Schema parity**: Test databases have identical schemas to production
- **Safety**: Tests can run any time without risk
- **Speed**: Transaction rollback cleans up test data automatically

## Schema Management

### Patterns by Service

| Service | Pattern | Location |
|---------|---------|----------|
| Agent API | Alembic migrations | `srv/agent/alembic/` |
| AuthZ | SchemaManager | `srv/authz/src/schema.py` |
| Ingest | SchemaManager | `srv/ingest/src/schema.py` |
| AI Portal | Prisma | `prisma/schema.prisma` |

### SchemaManager (AuthZ, Ingest)

The `SchemaManager` class in `srv/shared/busibox_common/` provides idempotent schema creation:

```python
from busibox_common import SchemaManager

def get_service_schema() -> SchemaManager:
    schema = SchemaManager()
    
    # Extensions (CREATE EXTENSION IF NOT EXISTS)
    schema.add_extension("pgcrypto")
    
    # Tables (CREATE TABLE IF NOT EXISTS)
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS my_table (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL
        )
    """)
    
    # Indexes (CREATE INDEX IF NOT EXISTS)
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_my_table_name ON my_table(name)")
    
    # Inline migrations (for column additions)
    schema.add_migration("""
        DO $$ BEGIN
            IF NOT EXISTS (...) THEN
                ALTER TABLE my_table ADD COLUMN new_col TEXT;
            END IF;
        END $$
    """)
    
    return schema
```

Schema is applied on every service startup, ensuring:
- Fresh installs get complete schema
- Existing installs get only missing parts
- No manual migration tracking needed

### Alembic (Agent API)

The Agent API uses SQLAlchemy with Alembic for migrations:

```bash
cd srv/agent

# Create new migration
alembic revision --autogenerate -m "Add new table"

# Apply migrations
alembic upgrade head

# Check current revision
alembic current
```

Migrations are applied during Ansible deployment to both production and test databases.

## Ansible Deployment

The `postgres` role in `provision/ansible/roles/postgres/` manages:

1. **User creation**: `busibox_user` and `busibox_test_user`
2. **Database creation**: All service databases
3. **Extension setup**: pgcrypto, uuid-ossp
4. **Permission grants**: Role-specific access

Service roles apply their own schemas:

```yaml
# provision/ansible/roles/agent_api/tasks/main.yml
- name: Run database migrations
  command: alembic upgrade head
  environment:
    DATABASE_URL: "postgresql+asyncpg://busibox_user:{{ password }}@{{ host }}/agent_server"

- name: Run test database migrations
  command: alembic upgrade head
  environment:
    DATABASE_URL: "postgresql+asyncpg://busibox_test_user:{{ password }}@{{ host }}/test_agent_server"
  when: enable_pytest_databases | default(false)
```

## Migration from Legacy

To migrate from the shared `busibox` database to separate databases:

```bash
# Preview changes
python scripts/migrations/migrate_to_separate_databases.py --all --dry-run

# Run migration
python scripts/migrations/migrate_to_separate_databases.py --all

# Verify migration
python scripts/migrations/migrate_to_separate_databases.py --verify-only

# Clean up source (after verification)
python scripts/migrations/migrate_to_separate_databases.py --all --cleanup
```

See [Database Separation Migration Guide](../guides/database-separation-migration.md) for complete instructions.

## Connection Strings

### Production/Staging

```bash
# Agent API
DATABASE_URL=postgresql+asyncpg://busibox_user:${PASSWORD}@${POSTGRES_HOST}:5432/agent_server

# AuthZ
POSTGRES_DB=authz
POSTGRES_USER=busibox_user
POSTGRES_PASSWORD=${PASSWORD}
POSTGRES_HOST=${HOST}

# Ingest
POSTGRES_DB=files
POSTGRES_USER=busibox_user
POSTGRES_PASSWORD=${PASSWORD}
POSTGRES_HOST=${HOST}
```

### Test Databases

```bash
# Agent API (pytest)
TEST_DATABASE_URL=postgresql+asyncpg://busibox_test_user:${PASSWORD}@${HOST}:5432/test_agent_server

# AuthZ (pytest)
POSTGRES_DB=test_authz
POSTGRES_USER=busibox_test_user

# Ingest (pytest)
POSTGRES_DB=test_files
POSTGRES_USER=busibox_test_user
```

## Tables Reference

### Agent Server (~15 tables)

- `agent_definitions` - Agent configurations and LLM settings
- `conversations` - Chat sessions
- `messages` - Individual messages
- `tools` - Available tools
- `workflows` - Workflow definitions
- `runs` - Execution history
- `run_outputs` - Streaming outputs
- `run_tool_calls` - Tool invocations
- Plus Alembic version tracking

### AuthZ (~17 tables)

- `authz_users` - User accounts
- `authz_roles` - Role definitions
- `authz_user_roles` - User-role assignments
- `authz_sessions` - Active sessions
- `authz_oauth_clients` - OAuth2 clients
- `authz_signing_keys` - JWT signing keys
- `authz_magic_links` - Passwordless login
- `authz_passkeys` - WebAuthn credentials
- `authz_totp_*` - TOTP codes and secrets
- `authz_delegation_tokens` - API tokens
- `authz_role_bindings` - RBAC bindings
- `audit_logs` - Security audit trail

### Ingest/Files (~8 tables)

- `ingestion_files` - Uploaded file metadata
- `ingestion_status` - Processing status
- `ingestion_chunks` - Text chunks
- `document_roles` - RBAC for documents
- `groups` - Document groups
- `group_memberships` - Group members
- `processing_history` - Stage tracking
- `processing_strategy_results` - Strategy outcomes

## Backup and Recovery

```bash
# Backup all service databases
pg_dump -h ${HOST} -U postgres agent_server > agent_server_$(date +%Y%m%d).sql
pg_dump -h ${HOST} -U postgres authz > authz_$(date +%Y%m%d).sql
pg_dump -h ${HOST} -U postgres files > files_$(date +%Y%m%d).sql

# Restore
psql -h ${HOST} -U postgres -d agent_server < agent_server_backup.sql
```

## Troubleshooting

### Check database exists

```bash
psql -h ${HOST} -U postgres -c "\l" | grep agent_server
```

### Check tables

```bash
psql -h ${HOST} -U busibox_user -d authz -c "\dt"
```

### Verify test isolation

```bash
# Should show 0 rows in test databases
psql -U busibox_test_user -d test_authz -c "SELECT COUNT(*) FROM authz_users;"
```

### Re-run schema

Schema is idempotent - restart the service to re-apply:

```bash
systemctl restart authz-api
systemctl restart ingest-api
```
