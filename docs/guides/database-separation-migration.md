# Database Separation Migration Guide

This guide explains how to migrate services from the shared `busibox` database to their own dedicated databases.

## Overview

### Current Architecture (Legacy)
All services share the `busibox` database:
- Authz tables (authz_users, authz_roles, etc.)
- Ingest tables (ingestion_files, ingestion_chunks, etc.)
- Agent tables (agent_definitions, etc.) - already separate

### Target Architecture
Each service has its own database:
| Service | Database | Owner |
|---------|----------|-------|
| Agent API | `agent_server` | `busibox_user` |
| Authz | `authz` | `busibox_user` |
| Ingest/Files | `files` | `busibox_user` |

Additionally, test databases exist for pytest:
| Test Database | Owner |
|---------------|-------|
| `test_agent_server` | `busibox_test_user` |
| `test_authz` | `busibox_test_user` |
| `test_files` | `busibox_test_user` |

## Migration Tool

The migration tool is located at:
```
scripts/migrations/migrate_to_separate_databases.py
```

### Prerequisites

1. **Python 3.11+** with asyncpg installed
2. **PostgreSQL admin access** (postgres user or busibox_user with CREATEDB)
3. **Services stopped** (recommended during migration)

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `POSTGRES_HOST` | PostgreSQL host | `localhost` |
| `POSTGRES_PORT` | PostgreSQL port | `5432` |
| `POSTGRES_PASSWORD` | PostgreSQL admin password | (required) |
| `SOURCE_PASSWORD` | Source database password | `devpassword` |

## Running the Migration

### Step 1: Dry Run (Preview)

Always start with a dry run to see what will be migrated:

```bash
# Local development
cd /path/to/busibox
docker run --rm --network host \
  -v "$(pwd):/app" \
  -w /app \
  -e POSTGRES_HOST="host.docker.internal" \
  -e POSTGRES_PASSWORD="devpassword" \
  -e SOURCE_PASSWORD="devpassword" \
  python:3.11-slim \
  bash -c "pip install -q asyncpg && python scripts/migrations/migrate_to_separate_databases.py --all --dry-run"
```

### Step 2: Run Migration

```bash
# Migrate all services
python scripts/migrations/migrate_to_separate_databases.py --all

# Or migrate specific service
python scripts/migrations/migrate_to_separate_databases.py --service authz
python scripts/migrations/migrate_to_separate_databases.py --service ingest
```

### Step 3: Verify Migration

The tool automatically verifies row counts match. You can also run:

```bash
python scripts/migrations/migrate_to_separate_databases.py --verify-only
```

### Step 4: Update Service Configuration

After migration, update each service to use its new database:

**Authz Service (`docker-compose.yml` or environment):**
```yaml
environment:
  POSTGRES_DB: authz  # Changed from busibox
  POSTGRES_USER: busibox_user
  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
```

**Ingest Service:**
```yaml
environment:
  POSTGRES_DB: files  # Changed from busibox
  POSTGRES_USER: busibox_user
  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
```

### Step 5: Cleanup Source (Optional)

Once verified and services are running on new databases:

```bash
python scripts/migrations/migrate_to_separate_databases.py --all --cleanup
```

⚠️ **Warning:** This permanently removes tables from the source database!

## Staging/Production Migration

### Preparation

1. **Schedule maintenance window** (15-30 minutes)
2. **Create database backup**
   ```bash
   pg_dump -h <host> -U postgres busibox > busibox_backup_$(date +%Y%m%d).sql
   ```
3. **Notify users** of expected downtime

### Migration Steps

1. **Stop services**
   ```bash
   systemctl stop busibox-authz busibox-ingest
   ```

2. **Create new databases**
   ```sql
   -- Run as postgres superuser
   CREATE DATABASE authz OWNER busibox_user;
   CREATE DATABASE files OWNER busibox_user;
   
   -- Enable extensions
   \c authz
   CREATE EXTENSION IF NOT EXISTS pgcrypto;
   CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
   
   \c files
   CREATE EXTENSION IF NOT EXISTS pgcrypto;
   CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
   ```

3. **Run migration**
   ```bash
   cd /opt/busibox
   python scripts/migrations/migrate_to_separate_databases.py --all
   ```

4. **Update Ansible inventory**
   ```yaml
   # inventory/production/group_vars/all/databases.yml
   authz_postgres_db: authz
   ingest_postgres_db: files
   ```

5. **Redeploy services**
   ```bash
   ansible-playbook -i inventory/production playbooks/deploy-authz.yml
   ansible-playbook -i inventory/production playbooks/deploy-ingest.yml
   ```

6. **Verify services**
   ```bash
   curl https://your-domain/authz/health
   curl https://your-domain/ingest/health
   ```

7. **Cleanup (after verification)**
   ```bash
   python scripts/migrations/migrate_to_separate_databases.py --all --cleanup
   ```

## Rollback

If migration fails:

1. **Stop services**
2. **Restore from backup** (if source was modified)
   ```bash
   psql -h <host> -U postgres busibox < busibox_backup_YYYYMMDD.sql
   ```
3. **Revert service configuration** to use `busibox` database
4. **Restart services**

## Tables Reference

### Authz Tables (17 tables)
- `audit_logs`
- `authz_delegation_tokens`
- `authz_email_domain_config`
- `authz_key_encryption_keys`
- `authz_magic_links`
- `authz_oauth_clients`
- `authz_passkey_challenges`
- `authz_passkeys`
- `authz_role_bindings`
- `authz_roles`
- `authz_sessions`
- `authz_signing_keys`
- `authz_totp_codes`
- `authz_totp_secrets`
- `authz_user_roles`
- `authz_users`
- `authz_wrapped_data_keys`

### Ingest Tables (8 tables)
- `document_roles`
- `group_memberships`
- `groups`
- `ingestion_chunks`
- `ingestion_files`
- `ingestion_status`
- `processing_history`
- `processing_strategy_results`

## Troubleshooting

### "Database already exists"
This is normal - the migration is idempotent and will skip existing databases.

### "Table already has N rows - skipping"
Data was already migrated. The tool won't duplicate data.

### Foreign key constraint errors
Ensure tables are migrated in dependency order. The tool handles this automatically.

### Permission denied
Ensure `busibox_user` has CREATEDB privilege:
```sql
ALTER ROLE busibox_user CREATEDB;
```
