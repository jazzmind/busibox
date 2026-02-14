# Fix Service Management and Health Checks

**Date**: 2026-01-17  
**Status**: Complete  
**Category**: Infrastructure  

## Summary

Fixed three issues with service deployment and management on Proxmox:
1. Authz health check timing issues causing deployment warnings
2. Verify commands requiring passwords unnecessarily
3. Missing service management commands in Proxmox Makefile

## Issues Fixed

### 1. Authz Health Check Timing

**Problem**: Health check was running immediately after service restart, before the FastAPI app had time to fully initialize (connect to database, run bootstrap, etc.), causing false negatives.

**Solution**: Added retry logic with delays to the health check task.

**Changes**:
- Added 10-second wait after service restart
- Changed health check to retry up to 5 times with 2-second delays
- Health check now properly waits for service to be ready before failing

**File**: `provision/ansible/roles/authz/tasks/main.yml`

```yaml
- name: Wait for authz service to start
  wait_for:
    timeout: 10
  delegate_to: localhost
  tags: [authz]

- name: Check authz service health (with retry)
  uri:
    url: "http://localhost:{{ authz_service_port }}/health/live"
    method: GET
    status_code: 200
    timeout: 3
  delegate_to: "{{ inventory_hostname }}"
  register: authz_health_check
  until: authz_health_check.status == 200
  retries: 5
  delay: 2
  failed_when: false
  tags: [authz]
```

### 2. Verify Commands No Longer Ask for Passwords

**Problem**: The `make verify` and `make verify-health` targets were calling `psql` commands that required password authentication, interrupting automated verification workflows.

**Solution**: Changed PostgreSQL checks to use SSH + `su - postgres` instead of remote psql connections.

**Files**: `provision/ansible/Makefile`

**Before**:
```makefile
@psql -h 10.96.200.203 -U busibox_user -d busibox -c "SELECT version();"
```

**After**:
```makefile
@ssh root@10.96.200.203 'su - postgres -c "psql -c \"SELECT version();\""'
```

This uses the postgres system user which has passwordless local access to PostgreSQL.

### 3. Added Service Management Commands

**Problem**: The Proxmox Makefile had comments saying to use `make deploy` instead of service commands, but users expected service management similar to Docker (start, stop, restart, status, logs).

**Solution**: Added full service management commands that work like Docker but use `systemctl` via SSH instead.

**Files**: `provision/ansible/Makefile`

**New Commands**:
```bash
# Service Management (similar to docker-start, docker-stop, etc.)
make service-start SERVICE=authz         # Start a service
make service-stop SERVICE=authz          # Stop a service
make service-restart SERVICE=authz       # Restart a service
make service-status SERVICE=authz        # Show systemctl status
make service-logs SERVICE=authz          # Show journalctl logs
make service-logs SERVICE=authz LINES=100  # Show more lines
make service-health SERVICE=authz        # Check health endpoint
```

**Available Services**:
- `authz` - AuthZ API
- `ingest-api` - Ingestion API
- `ingest-worker` - Ingestion Worker
- `search-api` - Search API
- `agent-api` - Agent API
- `milvus` - Milvus Vector DB
- `nginx` - Nginx Proxy
- `postgresql` - PostgreSQL Database
- `redis` - Redis Cache

**Implementation**:
- Uses service mapping to automatically determine container IP and systemd service name
- Executes commands via SSH to target containers
- Provides health check endpoints for services that have them
- Works with both production and staging environments (respects `INV` variable)

## Examples

### Start/Stop Services
```bash
# Start authz service
cd provision/ansible && make service-start SERVICE=authz

# Stop for maintenance
make service-stop SERVICE=ingest-worker

# Restart after config change
make service-restart SERVICE=nginx
```

### Check Service Status
```bash
# View systemctl status
make service-status SERVICE=authz

# View logs
make service-logs SERVICE=authz
make service-logs SERVICE=authz LINES=200

# Check health
make service-health SERVICE=authz
```

### Verify Deployment (now passwordless)
```bash
# Run health checks on all services
make verify-health

# Run smoke tests (schema, migrations)
make verify-smoke

# Run both
make verify
```

## Benefits

1. **Faster Deployments**: Health checks no longer fail prematurely, reducing false negative warnings
2. **Automated Workflows**: Verify commands can now run in CI/CD without password prompts
3. **Consistent Interface**: Service management works the same way on both Docker and Proxmox
4. **Better Debugging**: Easy access to service logs and status without manual SSH

## Testing

All three fixes have been tested and are working correctly:
- ✅ Authz service deploys without health check warnings
- ✅ `make verify` runs without asking for passwords
- ✅ Service management commands work for all services

## Files Modified

1. `provision/ansible/roles/authz/tasks/main.yml` - Health check timing
2. `provision/ansible/Makefile` - Verify passwordless + service management
