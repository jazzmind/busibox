---
created: 2025-01-18
updated: 2025-01-18
status: completed
category: development
---

# Status Display - Version Tracking and App Detection

> **Note**: This document references `scripts/docker-rebuild-with-version.sh` which has been removed. Version tracking is now automatic in the Makefile - just use `make docker-build`.

## Issues

After implementing the status display system, two issues remained:

1. **Version Info Not Working** - Docker containers showed "unknown" for deployed version
2. **Apps Not Detected** - AI Portal and Agent Manager not showing as "up" despite running

## Root Causes

### 1. Docker Containers Missing Version Labels

Local Docker containers built via `docker-compose.local.yml` didn't have version labels. The status system was looking for:
- Docker label: `version`
- Container file: `/opt/{service}/.deploy_version`

Neither existed because:
- Labels weren't set in docker-compose.yml
- `.deploy_version` files are only written by Ansible (for Proxmox deployments)

### 2. Apps Run on Host, Not in Docker

In hybrid mode (default), Next.js apps run directly on the host:
- **AI Portal**: Runs on port 3000 via `npm run dev`
- **Agent Manager**: Runs on port 3001 via `npm run dev`

The status checker was looking for Docker containers `local-ai-portal` and `local-agent-manager`, which don't exist in hybrid mode.

## Solutions

### 1. Add Version Labels to Docker Compose

Updated `docker-compose.local.yml` to add version labels to all API services:

```yaml
authz-api:
  build:
    context: ./srv
    dockerfile: authz/Dockerfile
    labels:
      - "version=${GIT_COMMIT:-unknown}"
  container_name: local-authz-api
  labels:
    - "version=${GIT_COMMIT:-unknown}"
```

Applied to:
- `authz-api`
- `ingest-api`
- `search-api`
- `agent-api`
- `docs-api`

### 2. Detect Host-Based Services

Updated `scripts/lib/status.sh` to detect services running on the host:

**Status Check:**
```bash
case "$service" in
    ai-portal)
        # Check if something is listening on port 3000
        if lsof -i :3000 -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo "up"
        else
            echo "down"
        fi
        return
        ;;
    agent-manager)
        # Check if something is listening on port 3001
        if lsof -i :3001 -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo "up"
        else
            echo "down"
        fi
        return
        ;;
esac
```

**Version Check:**
```bash
case "$service" in
    ai-portal)
        # Get version from ai-portal repo
        if [[ -d "${REPO_ROOT}/../ai-portal" ]]; then
            (cd "${REPO_ROOT}/../ai-portal" && git rev-parse --short HEAD 2>/dev/null) || echo "unknown"
        else
            echo "unknown"
        fi
        return
        ;;
    agent-manager)
        # Get version from agent-manager repo
        if [[ -d "${REPO_ROOT}/../agent-manager" ]]; then
            (cd "${REPO_ROOT}/../agent-manager" && git rev-parse --short HEAD 2>/dev/null) || echo "unknown"
        else
            echo "unknown"
        fi
        return
        ;;
esac
```

### 3. Created Rebuild Script

Created `scripts/docker-rebuild-with-version.sh` to rebuild containers with version labels:

```bash
#!/usr/bin/env bash
# Get current git commit
GIT_COMMIT=$(git rev-parse --short HEAD)
export GIT_COMMIT

# Rebuild with version labels
docker compose -f docker-compose.local.yml build \
    --build-arg GIT_COMMIT="${GIT_COMMIT}" \
    authz-api ingest-api search-api agent-api docs-api

docker compose -f docker-compose.local.yml up -d
```

## How to Use

### Rebuild Containers with Version Labels

```bash
# Option 1: Use the helper script
bash scripts/docker-rebuild-with-version.sh

# Option 2: Manual rebuild
export GIT_COMMIT=$(git rev-parse --short HEAD)
docker compose -f docker-compose.local.yml down
docker compose -f docker-compose.local.yml build authz-api ingest-api search-api agent-api docs-api
docker compose -f docker-compose.local.yml up -d
```

### Start Apps (Hybrid Mode)

```bash
# Terminal 1: Start backend services
make docker-up

# Terminal 2: Start AI Portal
cd ../ai-portal
npm run dev

# Terminal 3: Start Agent Manager
cd ../agent-manager
npm run dev
```

### Verify Status Display

```bash
make

# Should now show:
# - Correct versions for all Docker services
# - AI Portal and Agent Manager as "up" if running
# - Sync indicators comparing deployed vs current git hash
```

## Expected Display

After fixes, the status display should show:

```
Core Services
─────────────
  ● AuthZ           ✓ up │ 55b74b4  ✓ synced │ ✓ healthy (45ms)
  ● PostgreSQL      ✓ up │ -        - │ ✓ healthy
  ● Redis           ✓ up │ -        - │ ✓ healthy
  ● Milvus          ✓ up │ -        - │ ✓ healthy (123ms)
  ● MinIO           ✓ up │ RELEASE  - │ ✓ healthy (32ms)

API Services
────────────
  ● Ingest API      ✓ up │ 55b74b4  ✓ synced │ ✓ healthy (67ms)
  ● Search API      ✓ up │ 55b74b4  ✓ synced │ ✓ healthy (54ms)
  ● Agent API       ✓ up │ 55b74b4  ✓ synced │ ✓ healthy (43ms)
  ● LiteLLM         ✓ up │ -        - │ ✓ healthy (89ms)

App Services
────────────
  ● Nginx           ✓ up │ -        - │ ✓ healthy (12ms)
  ● AI Portal       ✓ up │ a2c3d4e  ✓ synced │ ✓ healthy (234ms)
  ● Agent Manager   ✓ up │ f5e6d7c  ✓ synced │ ✓ healthy (198ms)
```

## Technical Details

### Version Detection Logic

1. **Docker Services**:
   - Check `docker inspect {container} --format '{{.Config.Labels.version}}'`
   - If label exists and not "<no value>", use it
   - Otherwise, try reading `/opt/{service}/.deploy_version` from container
   - Fall back to "unknown"

2. **Host Services (ai-portal, agent-manager)**:
   - Get git hash from their respective repositories
   - `cd ../{service} && git rev-parse --short HEAD`

3. **External Services (postgres, redis, milvus, minio, litellm)**:
   - Don't track versions (use "-" in display)
   - These are third-party images

### Status Detection Logic

1. **Docker Services**:
   - Check if container is running: `docker ps --filter "name={container}" --filter "status=running"`

2. **Host Services**:
   - Check if port is listening: `lsof -i :{port} -sTCP:LISTEN`
   - AI Portal: port 3000
   - Agent Manager: port 3001

3. **Proxmox Services**:
   - SSH to container and check systemd: `systemctl is-active {service}`

## Files Modified

- `docker-compose.local.yml` - Added version labels to API services
- `scripts/lib/status.sh` - Added host service detection and version tracking
- `scripts/lib/services.sh` - Added missing service definitions (redis, docs-api, authz_api)
- `scripts/docker-rebuild-with-version.sh` - New helper script for rebuilding with versions

## Testing

### Test Version Labels

```bash
# After rebuild, check labels
docker inspect local-ingest-api --format '{{.Config.Labels.version}}'
# Should output: 55b74b4 (or current git hash)
```

### Test App Detection

```bash
# Start ai-portal
cd ../ai-portal && npm run dev &

# Check status
make
# Should show AI Portal as "up"

# Stop ai-portal (Ctrl+C)
# Check status again
make
# Should show AI Portal as "down"
```

### Test Version Sync

```bash
# Make a commit
git commit --allow-empty -m "test"

# Check status
make
# Docker services should show "⚠ behind" (deployed version != current)

# Rebuild
bash scripts/docker-rebuild-with-version.sh

# Check status again
make
# Should show "✓ synced"
```

## Limitations

### Local Docker Development

- **Version labels require rebuild** - Changing code doesn't update version automatically
- **Hot-reload doesn't update version** - Only rebuilding the image updates the label
- **Manual rebuild needed** - Use `scripts/docker-rebuild-with-version.sh` after commits

### Host-Based Apps

- **Port-based detection only** - Any process on port 3000/3001 shows as "up"
- **No health check validation** - Doesn't verify it's actually the correct app
- **Version from git repo** - Shows current git hash, not deployed version

### Workarounds

For more accurate tracking:
1. Use full Docker mode with `--profile full` (slower, but containers have proper labels)
2. Manually track versions in a file (similar to Ansible's `.deploy_version`)
3. Accept "behind" status during development (it's expected)

## Future Improvements

Potential enhancements:
1. Auto-rebuild on git commit (git hook)
2. PID file tracking for host services (more accurate than port checking)
3. Version file for host services (write `.deploy_version` on `npm run dev`)
4. Health endpoint validation for host services (not just port check)
5. Docker Compose watch mode integration (auto-rebuild on changes)
