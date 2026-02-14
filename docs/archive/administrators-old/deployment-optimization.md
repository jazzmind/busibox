---
title: "Deployment Optimization"
category: "administrator"
order: 24
description: "Version checking, app cleanup, and deployment performance improvements"
published: true
---

# Deployment Optimization and App Cleanup

## Summary of Changes

### 1. Agent-Client Renamed to Agent-Manager

**Changed Files:**
- `provision/ansible/group_vars/all/apps.yml` - Updated app name from `agent-manager` to `agent-manager`
- `scripts/lib/services.sh` - Already using `agent_manager` (correct)
- `scripts/lib/status.sh` - Already detecting `agent-manager` correctly

**Result:** Naming is now consistent across all systems.

### 2. Removed Add-On Apps from Ansible/Make

**Changed Files:**
- `provision/ansible/group_vars/all/apps.yml` - Removed these app definitions:
  - `doc-intel`
  - `foundation`
  - `project-analysis`
  - `innovation`

**Rationale:** These are add-on applications that will be managed through the web UI's deployment interface. Removing them from Ansible:
- Reduces initial provisioning time
- Simplifies infrastructure deployment
- Prevents add-on app issues from blocking core infrastructure
- Moves add-on management to user-friendly web interface

**Core Apps Remaining:**
- `ai-portal` (`auto_deploy: true`)
- `agent-manager` (`auto_deploy: true`)

### 3. Skip Already-Deployed Services (Version Checking)

**Changed Files:**
- `provision/ansible/roles/agent_api/tasks/main.yml` - Added version checking logic

**New Logic:**
1. **Check Current Version:** Read `.deploy_version` file if it exists
2. **Get New Version:** Determine git commit hash to be deployed  
3. **Compare:** Skip deployment if versions match
4. **Conditional Execution:** All deployment tasks now use `when: agent_deployment_needed`

**Tasks That Now Check Version:**
- Stop service
- Clean up directories
- Copy source code
- Install dependencies
- Create venv
- Install packages

**Tasks That Always Run:**
- Environment file deployment (secrets may change)
- Database migrations (always safe to run)
- systemd unit deployment (config may change)
- Health checks
- Service restart (if changes detected)

**Benefits:**
- Dramatically faster "deploy all" when nothing has changed
- Reduces unnecessary service restarts
- Saves time during iterative development
- Still ensures config/secret updates are applied

**Example Output:**
```
Agent API Version Check:
- Current deployed: 0b90e27
- New version: 0b90e27
- Deployment needed: false

TASK [agent_api : Copy agent service source code] ****
skipping: deployment not needed
```

### 4. NPM Dependency Resolution Fix

**Changed Files:**
- `provision/ansible/roles/app_deployer/templates/deploywatch-app.sh.j2`

**Change:** Added `--legacy-peer-deps` flag to all `npm install` commands

**Why:** Modern npm strict peer dependency resolution can fail on minor version mismatches. This flag uses more permissive npm v4-v6 behavior while remaining secure.

### 5. Milvus/MinIO Detection on Proxmox

**Current Status:** Service definitions are correct:
- Milvus: port 9091, endpoint `/healthz` ✓
- MinIO: port 9000, endpoint `/minio/health/live` ✓

**Detection Methods:**
1. **Status Check:** Uses `systemctl is-active` via SSH
   - Milvus: checks `milvus-standalone` service
   - MinIO: checks `minio` service

2. **Health Check:** HTTP GET to health endpoint
   - Milvus: `http://{ip}:9091/healthz`
   - MinIO: `http://{ip}:9000/minio/health/live`

**If Issues Persist:**
- Check if services are actually running: `ssh root@{container_ip} systemctl status milvus-standalone`
- Check if health endpoints are accessible: `curl http://{container_ip}:9091/healthz`
- Check firewall rules on containers
- Verify SSH connectivity from workstation to containers

## Implementation Status

✅ Agent-client → agent-manager rename
✅ Removed add-on apps from Ansible
✅ Added version checking to agent_api role
✅ Added NPM --legacy-peer-deps flag
⚠️  Milvus/MinIO detection - code is correct, may need environment-specific debugging

## Next Steps

### For Other Service Roles

The version checking pattern from `agent_api` should be applied to other service roles:
- `ingest_api`
- `search_api`
- `milvus`
- `litellm`
- etc.

**Pattern:**
```yaml
- name: Check current deployed version
  stat:
    path: /opt/{service}/.deploy_version
  register: {service}_deploy_version_file

- name: Read and parse current version
  # ... (see agent_api/tasks/main.yml for full pattern)

- name: Set deployment_needed flag
  set_fact:
    {service}_deployment_needed: "{{ not version_file.stat.exists or current != new }}"

- name: Display version check
  debug:
    msg: |
      {Service} Version Check:
      - Current: {{ current | default('none') }}
      - New: {{ new }}
      - Deployment needed: {{ deployment_needed }}

- name: Deploy tasks
  # ... tasks ...
  when: {service}_deployment_needed
```

### For Debugging Proxmox Detection

If Milvus/MinIO still show as down on Proxmox:

1. **SSH Test:**
   ```bash
   ssh root@10.96.201.204 systemctl status milvus-standalone
   ssh root@10.96.201.205 systemctl status minio
   ```

2. **Health Endpoint Test:**
   ```bash
   curl http://10.96.201.204:9091/healthz
   curl http://10.96.201.205:9000/minio/health/live
   ```

3. **Check Status Script:**
   ```bash
   # From workstation
   cd /Users/wsonnenreich/Code/busibox
   bash -x scripts/lib/status.sh  # Run with debug output
   ```

## Related Documentation

- `docs/deployment/app-auto-deploy.md` - Auto-deploy feature documentation
- `provision/ansible/roles/agent_api/tasks/main.yml` - Version checking implementation
- `scripts/lib/services.sh` - Service registry
- `scripts/lib/status.sh` - Status checking logic
