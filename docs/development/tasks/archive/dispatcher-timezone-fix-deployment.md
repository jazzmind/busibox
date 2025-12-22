---
created: 2025-12-16
updated: 2025-12-16
status: ready-to-deploy
category: deployment
---

# Dispatcher Timezone Fix - Deployment Guide

## Overview

Fixed a database timezone mismatch in the `dispatcher_decision_log` table that was causing chat message operations to fail.

## What Was Fixed

### Code Changes
1. **Model** (`srv/agent/app/models/dispatcher_log.py`):
   - Changed `DateTime` to `DateTime(timezone=True)`
   - Allows timezone-aware datetime values

2. **Migration** (`srv/agent/alembic/versions/20251216_0000_004_fix_dispatcher_log_timezone.py`):
   - Alters column from `TIMESTAMP WITHOUT TIME ZONE` to `TIMESTAMP WITH TIME ZONE`

3. **Ansible** (`provision/ansible/roles/agent_api/tasks/main.yml`):
   - Added `PYTHONPATH: "/srv/agent"` to migration environment
   - Ensures Alembic can import app modules during migration

## Automatic Deployment ✅

### The migration runs automatically when you deploy agent-api:

```bash
cd /path/to/busibox/provision/ansible

# Deploy to test environment:
make agent INV=inventory/test

# Deploy to production:
make agent
```

### What Happens During Deployment

1. **Code Sync** (line 46-60 in `roles/agent_api/tasks/main.yml`):
   ```yaml
   - name: Copy agent service source code
     synchronize:
       src: "{{ playbook_dir }}/../../srv/agent/"
       dest: "{{ agent_api_src_path }}/"
   ```
   - Copies all source code including `alembic/versions/20251216_0000_004_fix_dispatcher_log_timezone.py`

2. **Run Migrations** (line 160-170):
   ```yaml
   - name: Run database migrations
     shell: |
       cd /srv/agent
       . .venv/bin/activate
       alembic upgrade head
     environment:
       DATABASE_URL: "{{ secrets['agent-server'].database_url }}"
       PYTHONPATH: "/srv/agent"
   ```
   - Runs `alembic upgrade head`
   - Sets PYTHONPATH so Alembic can import models
   - Uses DATABASE_URL from secrets

3. **Restart Service**:
   - Systemd service is restarted with new code

## Verification

### 1. Check Migration Applied

```bash
ssh root@<agent-lxc-ip>
cd /srv/agent
source .venv/bin/activate
export PYTHONPATH=/srv/agent
alembic current
# Should show: 20251216_0000_004 (head)
```

### 2. Verify Database Schema

```bash
ssh root@<pg-lxc-ip>
psql -U busibox_user -d busibox
\d dispatcher_decision_log
# timestamp column should show: timestamp with time zone
\q
```

### 3. Run Tests

```bash
cd /path/to/busibox-app
npm test
# Should see: Tests: 112 passed, 112 total ✅
```

### 4. Test Chat Functionality

```bash
# Create a conversation and send a message
curl -X POST http://<agent-api-url>/chat/conversations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Test"}'

# Should succeed without database errors
```

## Manual Deployment (If Needed)

### Option 1: Direct SQL (Fastest)

```bash
ssh root@<pg-lxc-ip>
psql -U busibox_user -d busibox -c "ALTER TABLE dispatcher_decision_log ALTER COLUMN timestamp TYPE TIMESTAMP WITH TIME ZONE;"
```

### Option 2: Run Migration Manually

```bash
ssh root@<agent-lxc-ip>
cd /srv/agent
source .venv/bin/activate
export PYTHONPATH=/srv/agent
export DATABASE_URL="postgresql+asyncpg://busibox_user:busibox_pass@<pg-ip>:5432/busibox"
alembic upgrade head
systemctl restart agent-api
```

## Rollback (If Needed)

If you need to rollback:

```bash
ssh root@<agent-lxc-ip>
cd /srv/agent
source .venv/bin/activate
export PYTHONPATH=/srv/agent
alembic downgrade -1
systemctl restart agent-api
```

Note: This will lose timezone information in existing timestamps.

## Expected Results

### Before Fix
- ❌ Chat message operations fail with database error
- ❌ 5 tests failing in `busibox-app`
- ❌ Error: "can't subtract offset-naive and offset-aware datetimes"

### After Fix
- ✅ Chat messages work correctly
- ✅ All 112 tests pass
- ✅ Dispatcher logging works properly
- ✅ Timezone information preserved in database

## Files Modified

### busibox/srv/agent
- `app/models/dispatcher_log.py` - Model fix
- `alembic/versions/20251216_0000_004_fix_dispatcher_log_timezone.py` - New migration
- `scripts/fix-dispatcher-timezone.py` - Helper script (optional)

### busibox/provision/ansible
- `roles/agent_api/tasks/main.yml` - Added PYTHONPATH to migration task

### busibox/scripts
- `fix-dispatcher-timezone.sh` - Deployment helper (optional)

## Related Documentation

- Test results: `docs/development/tasks/chat-tests-final-status.md`
- Auth fix details: Same document
- Chat architecture: `docs/development/tasks/chat-architecture-refactor.md`

## Deployment Checklist

- [x] Model updated with `DateTime(timezone=True)`
- [x] Migration file created
- [x] Ansible playbook updated with PYTHONPATH
- [x] Documentation created
- [ ] Deploy to test environment
- [ ] Verify tests pass (112/112)
- [ ] Test chat functionality manually
- [ ] Deploy to production
- [ ] Verify production tests pass

## Notes

- Migration is idempotent - safe to run multiple times
- PostgreSQL automatically converts existing timestamps during ALTER
- No data loss occurs during the schema change
- Service downtime is minimal (only during restart)
