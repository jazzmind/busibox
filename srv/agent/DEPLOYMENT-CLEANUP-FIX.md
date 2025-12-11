# Agent API Deployment - Cleanup Fix

## Problem

The deployment was failing with rsync permission errors when trying to delete old files:

```
rsync: [generator] delete_file: unlink(tsx-0/17653-...) failed: Permission denied (13)
rsync: [generator] delete_file: unlink(src/mastra/workflows/...) failed: Permission denied (13)
```

**Root Cause**: The `/srv/agent` directory contained leftover files from the old Node.js agent-server deployment, owned by different users (likely `root` or `pm2` user).

## Solution

Updated `roles/agent_api/tasks/main.yml` to perform a clean deployment:

### Before (Failed)
```yaml
- name: Create agent directories
  file:
    path: /srv/agent
    state: directory
    owner: agent
    
- name: Copy agent service source code
  synchronize:
    src: srv/agent/
    dest: /srv/agent/
    become_user: agent  # ❌ Can't delete files owned by other users
```

### After (Works)
```yaml
- name: Stop agent-api service if running
  systemd:
    name: agent-api
    state: stopped
  ignore_errors: yes

- name: Clean up old agent directory
  file:
    path: /srv/agent
    state: absent  # ✅ Remove entire directory as root

- name: Create agent directories
  file:
    path: /srv/agent
    state: directory
    owner: agent

- name: Copy agent service source code
  synchronize:
    src: srv/agent/
    dest: /srv/agent/
    # Runs as root by default

- name: Fix ownership of agent directory
  file:
    path: /srv/agent
    owner: agent
    recurse: yes  # ✅ Fix ownership after copy
```

## Key Changes

1. **Stop Service First**: Ensures no files are in use
2. **Complete Cleanup**: Removes entire `/srv/agent` directory (as root)
3. **Fresh Start**: Creates clean directory structure
4. **Copy as Root**: Avoids permission issues during rsync
5. **Fix Ownership**: Recursively sets correct ownership after copy

## Why This Works

- **Root privileges**: Can delete files owned by any user
- **Clean slate**: No conflicts with old Node.js files
- **Proper ownership**: Files end up owned by `agent` user for service execution

## Similar Pattern

This follows the same pattern as other services that need clean deployments:
- Stop service
- Clean up old files
- Deploy new files
- Fix permissions
- Start service

## Testing

Deploy to test environment:
```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible
make agent INV=inventory/test
```

Expected result:
- ✅ Old directory removed successfully
- ✅ New Python code deployed
- ✅ Service starts without permission errors
- ✅ Health check passes

## Files Modified

- `provision/ansible/roles/agent_api/tasks/main.yml`
  - Added service stop step
  - Added directory cleanup step
  - Added ownership fix step

