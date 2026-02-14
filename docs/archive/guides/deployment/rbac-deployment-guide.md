---
title: RBAC Deployment Guide
created: 2025-11-24
updated: 2025-11-24
status: ready
category: deployment
---

# RBAC Deployment Guide

## Overview

This guide covers deploying the new Row-Level Security (RLS) and group-based access control system to production.

## What's Being Deployed

### 1. PostgreSQL Migrations

**`add_rbac_schema.sql`**:
- Creates `groups` table for group definitions
- Creates `group_memberships` table for user-group relationships
- Adds `owner_id`, `visibility`, `group_id` columns to `ingestion_files`
- Backfills `owner_id` from existing `user_id` values
- Adds constraints and indexes

**`add_rls_policies.sql`**:
- Enables Row-Level Security on `ingestion_files`, `ingestion_chunks`, `processing_history`
- Creates policies for SELECT, INSERT, UPDATE, DELETE operations
- Uses session variables: `app.user_id`, `app.user_groups`
- Enforces access control at database level

### 2. Milvus Partition Support

**`milvus_init_partitions.py`**:
- Verifies Milvus collection supports partitions
- Creates example partitions to demonstrate structure
- Documents partition naming convention:
  - `personal_{user_id}` for personal documents
  - `group_{group_id}` for group documents

### 3. Ansible Integration

**Updated Tasks**:
- `provision/ansible/roles/ingest/tasks/migrations.yml` - Runs PostgreSQL migrations
- `provision/ansible/roles/milvus/tasks/main.yml` - Runs Milvus partition init

## Pre-Deployment Checklist

- [ ] Review architecture docs:
  - `docs/architecture/rbac-and-group-permissions.md`
  - `docs/architecture/database-level-access-control.md`
- [ ] Backup PostgreSQL database
- [ ] Backup Milvus data directory
- [ ] Verify test environment is working
- [ ] Confirm all existing tests pass

## Deployment Steps

### Step 1: Deploy to Test Environment

```bash
cd /root/busibox/provision/ansible

# Pull latest changes
cd /root/busibox
git fetch origin
git checkout 004-updated-ingestion-service
git pull origin 004-updated-ingestion-service

# Deploy PostgreSQL migrations
cd provision/ansible
make postgres INV=inventory/test

# Deploy Milvus partition support
make milvus INV=inventory/test

# Deploy updated ingest service
make ingest INV=inventory/test
```

### Step 2: Verify Migrations

**Check PostgreSQL**:
```bash
ssh root@<test-pg-ip>

# Connect to database
psql -U busibox_user -d files

# Verify tables exist
\dt groups
\dt group_memberships

# Verify RLS is enabled
SELECT relname, relrowsecurity 
FROM pg_class 
WHERE relname IN ('ingestion_files', 'ingestion_chunks', 'processing_history');

# Should show 't' (true) for all three tables

# Verify policies exist
SELECT schemaname, tablename, policyname 
FROM pg_policies 
WHERE tablename IN ('ingestion_files', 'ingestion_chunks', 'processing_history');

# Should show multiple policies for each table

# Verify columns exist
\d ingestion_files

# Should show owner_id, visibility, group_id columns
```

**Check Milvus**:
```bash
ssh root@<test-milvus-ip>

# Check partition initialization logs
cat /root/milvus_init_partitions.log

# Or run manually
/opt/milvus-tools/bin/python /root/milvus_init_partitions.py
```

### Step 3: Test Access Control

**Test RLS Policies**:
```bash
# On PostgreSQL container
psql -U busibox_user -d files

-- Test with user context
BEGIN;
SET LOCAL app.user_id = '00000000-0000-0000-0000-000000000001';
SET LOCAL app.user_groups = '';

-- Should only see documents owned by this user
SELECT file_id, owner_id, visibility FROM ingestion_files;

ROLLBACK;
```

**Test Existing Documents**:
```bash
# On ingest container
ssh root@<test-ingest-ip>

# Upload a test document
curl -X POST http://localhost:8000/upload \
  -H "X-User-Id: test-user-id" \
  -F "file=@/srv/ingest/samples/sample.pdf"

# Verify it was created with owner_id
# Check logs: journalctl -u ingest-worker -n 50
```

### Step 4: Run Tests

```bash
# On ingest container
ssh root@<test-ingest-ip>

# Run all tests
cd /srv/ingest
source venv/bin/activate
pytest tests/ -v

# Run integration tests
pytest tests/integration/ -v
```

### Step 5: Deploy to Production

**Only after test environment is verified!**

```bash
cd /root/busibox/provision/ansible

# Merge to main
cd /root/busibox
git checkout main
git merge 004-updated-ingestion-service
git push origin main

# Deploy to production
cd provision/ansible
make postgres
make milvus
make ingest
```

### Step 6: Verify Production

```bash
# Check PostgreSQL migrations
ssh root@<prod-pg-ip>
psql -U busibox_user -d files -c "\dt groups"

# Check Milvus partitions
ssh root@<prod-milvus-ip>
docker logs milvus-standalone | grep partition

# Check ingest service
ssh root@<prod-ingest-ip>
systemctl status ingest-api
systemctl status ingest-worker
journalctl -u ingest-worker -n 50 --no-pager
```

## Migration Behavior

### Idempotency

All migrations are **idempotent** - safe to run multiple times:

- PostgreSQL migrations check if tables/columns exist before creating
- RLS policies are dropped and recreated (safe)
- Milvus partition script checks if partitions exist

### Existing Data

**Backward Compatibility**:
- ✅ Existing documents get `owner_id` backfilled from `user_id`
- ✅ Existing documents default to `visibility='personal'`
- ✅ Existing documents have `group_id=NULL`
- ✅ All existing documents remain accessible to their owners

**No Data Loss**:
- RLS policies allow owners to see their documents
- Chunks and processing history inherit permissions
- Search continues to work for personal documents

## Rollback Plan

If issues occur:

### Rollback PostgreSQL

```bash
ssh root@<pg-ip>
psql -U busibox_user -d files

-- Disable RLS (emergency only)
ALTER TABLE ingestion_files DISABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_chunks DISABLE ROW LEVEL SECURITY;
ALTER TABLE processing_history DISABLE ROW LEVEL SECURITY;

-- Drop RLS policies
DROP POLICY IF EXISTS ingestion_files_owner_select ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_group_select ON ingestion_files;
-- (etc. for all policies)

-- Optionally drop RBAC tables (only if no groups created)
DROP TABLE IF EXISTS group_memberships CASCADE;
DROP TABLE IF EXISTS groups CASCADE;

-- Remove columns (only if needed)
ALTER TABLE ingestion_files DROP COLUMN IF EXISTS group_id;
ALTER TABLE ingestion_files DROP COLUMN IF EXISTS visibility;
-- Keep owner_id, it's useful
```

### Rollback Milvus

```bash
# Partitions don't need rollback - they're just organizational
# Existing data in default partition is unaffected
```

### Rollback Code

```bash
cd /root/busibox
git checkout main  # or previous stable branch
cd provision/ansible
make ingest
```

## Post-Deployment

### Monitoring

**Check Logs**:
```bash
# Ingest API
journalctl -u ingest-api -f

# Ingest Worker
journalctl -u ingest-worker -f

# PostgreSQL
ssh root@<pg-ip>
tail -f /var/log/postgresql/postgresql-15-main.log
```

**Watch for**:
- RLS policy violations (should not occur with proper JWT)
- Partition creation messages in worker logs
- Performance issues (RLS adds minimal overhead)

### Performance

**Expected Impact**:
- RLS adds ~1-5ms per query (negligible)
- Indexes on `owner_id`, `group_id` mitigate overhead
- Milvus partition pruning improves search performance

**Monitor**:
```bash
# PostgreSQL query performance
psql -U busibox_user -d files -c "
SELECT query, mean_exec_time, calls 
FROM pg_stat_statements 
WHERE query LIKE '%ingestion_files%' 
ORDER BY mean_exec_time DESC 
LIMIT 10;
"
```

## Troubleshooting

### Issue: "permission denied for table ingestion_files"

**Cause**: RLS is enabled but session variables not set

**Fix**: Ensure JWT middleware sets `app.user_id` and `app.user_groups`

### Issue: "User can't see their own documents"

**Cause**: Session variable not set correctly

**Debug**:
```sql
-- Check current session variables
SHOW app.user_id;
SHOW app.user_groups;

-- Manually set and test
SET LOCAL app.user_id = 'actual-user-uuid';
SELECT * FROM ingestion_files;
```

### Issue: "Milvus partition not found"

**Cause**: Worker hasn't created partition yet

**Fix**: Partitions are created on-demand. Upload a document to trigger creation.

### Issue: "Migration fails with 'column already exists'"

**Cause**: Migration partially applied

**Fix**: Migrations use `IF NOT EXISTS` - safe to re-run

## Next Steps

After successful deployment:

1. **Implement JWT Authentication** (see `docs/architecture/jwt-authentication.md`)
2. **Add Group Management UI** to AI Portal
3. **Add RBAC Tests** (see `docs/architecture/rbac-and-group-permissions.md`)
4. **Update API Documentation** with group parameters
5. **Train Users** on group document sharing

## Support

For issues:
1. Check logs (see Monitoring section)
2. Review architecture docs
3. Test RLS policies manually (see Test Access Control)
4. Rollback if necessary (see Rollback Plan)

## References

- Architecture: `docs/architecture/rbac-and-group-permissions.md`
- Database RLS: `docs/architecture/database-level-access-control.md`
- JWT Auth: `docs/architecture/jwt-authentication.md`
- Migrations: `srv/ingest/migrations/`

