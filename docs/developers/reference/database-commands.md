---
title: "Database Commands"
category: "developer"
order: 136
description: "Quick reference for PostgreSQL database commands per service"
published: true
---

# Database Commands - Quick Reference

## Database Architecture

Each service has its own dedicated PostgreSQL database:

| Service | Database | Owner | Description |
|---------|----------|-------|-------------|
| Agent API | `agent` | `busibox_user` | Agent definitions, conversations, workflows |
| AuthZ | `authz` | `busibox_user` | Users, roles, sessions, audit logs |
| Data API | `data` | `busibox_user` | File metadata, chunks, processing history |
| AI Portal | `ai_portal` | `busibox_user` | Portal-specific data (Prisma managed) |
| Legacy | `busibox` | `busibox_user` | Legacy shared database (being deprecated) |

### Test Databases

For pytest isolation, a separate test user owns identical databases:

| Test Database | Owner | Purpose |
|---------------|-------|---------|
| `test_agent` | `busibox_test_user` | Agent service tests |
| `test_authz` | `busibox_test_user` | AuthZ service tests |
| `test_data` | `busibox_test_user` | Data service tests |

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
psql -h 10.96.200.203 -U busibox_user -d agent

# AuthZ
psql -h 10.96.200.203 -U busibox_user -d authz

# Data API
psql -h 10.96.200.203 -U busibox_user -d data

# Test databases (for pytest)
psql -h localhost -U busibox_test_user -d test_agent
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

See [09-databases](../architecture/09-databases.md) for database architecture and migration details.

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
- **Data API**: SchemaManager in `srv/data/src/schema.py`

Schema is applied idempotently on every service startup.
