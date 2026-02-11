---
title: Database Commands - Quick Reference
created: 2025-01-13
updated: 2026-01-16
status: active
category: reference
tags: [database, commands, quick-reference]
---

# Database Commands - Quick Reference

## Database Architecture

Each service has its own dedicated PostgreSQL database:

| Service | Database | Owner | Description |
|---------|----------|-------|-------------|
| Agent API | `agent_server` | `busibox_user` | Agent definitions, conversations, workflows |
| AuthZ | `authz` | `busibox_user` | Users, roles, sessions, audit logs |
| Ingest | `files` | `busibox_user` | File metadata, chunks, processing history |
| AI Portal | `ai_portal` | `busibox_user` | Portal-specific data (Prisma managed) |
| Legacy | `busibox` | `busibox_user` | Legacy shared database (being deprecated) |

### Test Databases

For pytest isolation, a separate test user owns identical databases:

| Test Database | Owner | Purpose |
|---------------|-------|---------|
| `test_agent_server` | `busibox_test_user` | Agent service tests |
| `test_authz` | `busibox_test_user` | AuthZ service tests |
| `test_files` | `busibox_test_user` | Ingest service tests |

## From Admin Workstation

```bash
# Connect to database
bash scripts/psql-connect.sh <database> <environment>
bash scripts/psql-connect.sh ai_portal production

# Check database
bash scripts/check-database.sh <database> <environment>
bash scripts/check-database.sh ai_portal production

# Initialize app database
bash scripts/init-app-database.sh <app-name> <environment>
bash scripts/init-app-database.sh ai-portal production
```

## Service Database Connections

```bash
# Agent API
psql -h 10.96.200.203 -U busibox_user -d agent_server

# AuthZ
psql -h 10.96.200.203 -U busibox_user -d authz

# Ingest/Files
psql -h 10.96.200.203 -U busibox_user -d files

# Test databases (for pytest)
psql -h localhost -U busibox_test_user -d test_agent_server
```

## Container IPs

- **Production**: 10.96.200.203
- **Staging**: 10.96.201.203

## psql Commands

```sql
\dt              -- List tables
\d TableName     -- Describe table
\l               -- List databases
\du              -- List users
\q               -- Quit
```

## Database Migration

To migrate from the legacy shared `busibox` database to separate service databases:

```bash
# Preview migration (dry run)
python scripts/migrations/migrate_to_separate_databases.py --all --dry-run

# Run migration
python scripts/migrations/migrate_to_separate_databases.py --all

# Verify only
python scripts/migrations/migrate_to_separate_databases.py --verify-only

# Cleanup source after verification
python scripts/migrations/migrate_to_separate_databases.py --all --cleanup
```

See `docs/guides/database-separation-migration.md` for complete migration guide.

## Quick Fixes

### Missing Tables

```bash
bash scripts/init-app-database.sh ai-portal production
ssh root@10.96.200.201 'pm2 restart ai-portal'
```

### Check Tables

```bash
bash scripts/check-database.sh ai_portal production
```

### Query Data

```bash
bash scripts/psql-connect.sh ai_portal production
```

```sql
SELECT * FROM "User" LIMIT 10;
```

## Schema Management

Services use the shared `busibox_common` library for schema management:

- **Agent**: Alembic migrations in `srv/agent/alembic/`
- **AuthZ**: SchemaManager in `srv/authz/src/schema.py`
- **Ingest**: SchemaManager in `srv/data/src/schema.py`

Schema is applied idempotently on every service startup.
