---
created: 2026-01-18
updated: 2026-01-18
status: resolved
category: troubleshooting
---

# Proxmox Service Detection Issue

## Problem

Status display was showing Milvus, MinIO, and Agent Manager as "unknown" on Proxmox/staging, even though they were deployed and running:

```
Core Services
─────────────
  ○ Milvus         	- unknown		│ unknown
  ○ MinIO          	- unknown		│ unknown

App Services
────────────
  ○ Agent Manager  	- unknown		│ unknown
```

## Root Cause

The `check_service_status()` function in `scripts/lib/status.sh` was only checking for **systemd services** on Proxmox containers, but:

1. **Milvus** runs in Docker container `milvus-standalone` (not systemd)
2. **MinIO** runs in Docker container `minio-minio-1` (not systemd)
3. **Agent Manager** systemd service is still named `agent-manager.service` (legacy name)

## Verification

Checked actual container status:

```bash
# Milvus container
ssh root@10.96.201.204 "docker ps" 
milvus-standalone   Up 31 minutes (healthy)
milvus-minio        Up 31 minutes (healthy)
milvus-etcd         Up 31 minutes (healthy)

# MinIO container
ssh root@10.96.201.205 "docker ps"
minio-minio-1   Up 47 hours

# Agent Manager service
ssh root@10.96.201.201 "systemctl list-units --type=service | grep agent"
agent-manager.service  loaded active running  agent-manager Application
```

## Solution

Updated `scripts/lib/status.sh` `check_service_status()` function for Proxmox backend to:

### 1. Check Docker for Milvus

```bash
docker ps --filter 'name=milvus-standalone' --filter 'status=running'
```

### 2. Check Docker for MinIO

```bash
docker ps --filter 'name=minio' --filter 'status=running'
```

### 3. Check Both Names for Agent Manager

```bash
systemctl is-active agent-manager 2>/dev/null || systemctl is-active agent-manager 2>/dev/null
```

This handles the legacy `agent-manager` service name that's still deployed on staging.

## Changes Made

**File:** `scripts/lib/status.sh`

**Modified:** `check_service_status()` function, `proxmox` case

**Logic:**
- Services like `milvus`, `minio` → Check Docker containers
- Service `agent-manager` → Check systemd with fallback to `agent-manager`
- Service `postgres` → Check systemd `postgresql` service
- Other services → Check systemd with default name mapping

## Testing

After the fix, status display should show:

```
Core Services
─────────────
  ● Milvus         	✓ up		│ v2.6.5@9f55bd3  	✓ synced
  ● MinIO          	✓ up		│ latest@9f55bd3 	✓ synced

App Services
────────────
  ● Agent Manager  	✓ up		│ 9f55bd3         	✓ synced
```

## Future Considerations

### Rename agent-manager Service

When redeploying apps to staging, the systemd service should be renamed:

```bash
# On TEST-apps-lxc (10.96.201.201)
systemctl stop agent-manager
systemctl disable agent-manager
rm /etc/systemd/system/agent-manager.service
systemctl daemon-reload

# Then redeploy via Ansible (will create agent-manager.service)
```

### Document Docker vs Systemd Services

Create a reference document listing which services use Docker vs systemd on Proxmox:

**Docker-based:**
- Milvus (standalone + etcd + minio)
- MinIO (file storage)
- LiteLLM (proxy)

**Systemd-based:**
- PostgreSQL
- AuthZ API
- Ingest API
- Search API
- Agent API
- AI Portal
- Agent Manager
- Nginx

## Related Files

- `scripts/lib/status.sh` - Service status checking logic
- `scripts/lib/services.sh` - Service registry and metadata
- `provision/ansible/roles/milvus/` - Milvus Docker Compose deployment
- `provision/ansible/roles/minio/` - MinIO Docker Compose deployment
- `provision/ansible/roles/app_deployer/` - App systemd service deployment
