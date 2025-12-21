---
created: 2025-12-16
updated: 2025-12-16
status: completed
category: deployment
---

# Ansible Service Restart Fix

## Problem

Python services (agent-api, authz, search-api, ingest-api, ingest-worker) were not restarting on deployment, causing old code to continue running even after new code was deployed.

### Root Cause

The Ansible tasks used `state: started` which has this behavior:
- **If service is running**: Do nothing (no restart)
- **If service is stopped**: Start it

This meant:
1. Code gets updated on disk
2. Service keeps running with old code loaded in memory
3. Tests fail because they're hitting the old code
4. Manual restart required to load new code

### Why Handlers Weren't Enough

While the roles had `notify: Restart service-name` handlers:
- Handlers only run at the END of the playbook
- Handlers only run if a task reports `changed`
- If files haven't changed (or Ansible doesn't detect changes), handlers don't run
- This made restarts unreliable

## Solution

Split the service management into two explicit tasks:

### Before (Unreliable)
```yaml
- name: Enable and start agent service
  systemd:
    name: agent-api
    enabled: yes
    state: started  # ❌ Only starts if stopped
    daemon_reload: yes
```

### After (Reliable)
```yaml
- name: Enable agent service
  systemd:
    name: agent-api
    enabled: yes
    daemon_reload: yes

- name: Restart agent service (always restart on deploy)
  systemd:
    name: agent-api
    state: restarted  # ✅ Always restarts
```

## Services Fixed

All Python services now restart on every deployment:

1. **agent-api** (`roles/agent_api/tasks/main.yml`)
   - FastAPI service for AI agent operations
   - Critical for chat functionality

2. **authz** (`roles/authz/tasks/main.yml`)
   - Authentication and authorization service
   - Manages OAuth tokens and RBAC

3. **search-api** (`roles/search_api/tasks/main.yml`)
   - Semantic search service
   - Handles vector search queries

4. **ingest-api** (`roles/ingest_api/tasks/main.yml`)
   - Document ingestion API
   - Processes uploaded documents

5. **ingest-worker** (`roles/ingest_worker/tasks/main.yml`)
   - Background worker for document processing
   - Handles embedding generation

## Impact

### Before Fix
- ❌ Services run old code after deployment
- ❌ Tests fail with confusing errors
- ❌ Manual SSH + restart required
- ❌ Inconsistent behavior between deployments

### After Fix
- ✅ Services always load new code
- ✅ Tests pass immediately after deployment
- ✅ No manual intervention needed
- ✅ Predictable, reliable deployments

## Testing

After deploying with this fix:

```bash
cd /path/to/busibox/provision/ansible

# Deploy agent-api
make agent INV=inventory/test

# Verify service restarted
ssh root@<test-agent-ip> "systemctl status agent-api | grep 'Active:'"
# Should show recent timestamp

# Run tests
cd /path/to/busibox-app
npm test
# Should pass with new code
```

## Related Issues

This fix resolves:
- Chat tests failing with `'model' is an invalid keyword argument`
- Services running outdated code after deployment
- Need for manual service restarts after code changes

## Best Practices

### For New Services

When creating new service deployment roles, always use:

```yaml
- name: Enable <service> service
  systemd:
    name: <service-name>
    enabled: yes
    daemon_reload: yes

- name: Restart <service> service (always restart on deploy)
  systemd:
    name: <service-name>
    state: restarted
```

**Don't use**:
```yaml
- name: Enable and start <service>
  systemd:
    name: <service-name>
    enabled: yes
    state: started  # ❌ Unreliable for deployments
```

### When to Use `state: started`

`state: started` is appropriate for:
- Infrastructure services (PostgreSQL, Redis, nginx)
- Services that rarely change
- Initial setup/provisioning (not deployments)

For application services that get code updates, always use `state: restarted`.

## Commit

**Commit**: `fa72634` - "fix: always restart Python services on deployment"

## Files Modified

```
provision/ansible/roles/agent_api/tasks/main.yml
provision/ansible/roles/authz/tasks/main.yml
provision/ansible/roles/search_api/tasks/main.yml
provision/ansible/roles/ingest_api/tasks/main.yml
provision/ansible/roles/ingest_worker/tasks/main.yml
```

## Verification

To verify services restart on deployment:

```bash
# Before deployment, note the service start time
ssh root@<server-ip> "systemctl show -p ActiveEnterTimestamp <service-name>"

# Deploy
make <service> INV=inventory/test

# After deployment, check the start time again
ssh root@<server-ip> "systemctl show -p ActiveEnterTimestamp <service-name>"

# The timestamp should be newer (within last minute)
```

## References

- Ansible systemd module: https://docs.ansible.com/ansible/latest/collections/ansible/builtin/systemd_module.html
- Related issue: Chat tests failing after agent-api deployment
- Migration fix: `docs/development/tasks/dispatcher-timezone-fix-deployment.md`
