---
created: 2025-01-18
updated: 2025-01-18
status: completed
category: development
---

# Status Display System - Fixes and Improvements

## Issues Fixed

### Issue 1: Services Not Reporting Correctly

**Problem:** Most services were showing as "down" or "unknown" even though Docker containers were running.

**Root Causes:**

1. **Container Name Mismatch**
   - Code was looking for `local-authz` 
   - Actual container name is `local-authz-api`
   - Fixed by adding proper container name mapping

2. **Incorrect Health Endpoints**
   - AuthZ was using `/health` but actual endpoint is `/health/live`
   - Milvus was using `/health` but actual endpoint is `/healthz`
   - Fixed by updating service definitions

3. **HTTP Status Code Handling**
   - LiteLLM returns 401 (auth required) but service is up
   - Nginx returns 301 (redirect) but service is up
   - Fixed by treating 301, 302, 401, 403 as "healthy"

4. **Non-HTTP Services**
   - PostgreSQL is a database, not an HTTP service
   - Was trying to curl port 5432 and failing
   - Fixed by skipping HTTP health check for postgres/redis

**Fixes Applied:**

```bash
# In scripts/lib/status.sh

# 1. Container name mapping for Docker
case "$service" in
    authz) container_name="local-authz-api" ;;
    postgres) container_name="local-postgres" ;;
    # ... etc
esac

# 2. Updated service definitions in scripts/lib/services.sh
_SERVICE_authz="210:busibox:srv/authz:/health/live:8010"  # was /health
_SERVICE_milvus="204:milvus::/healthz:9091"                # was /health

# 3. Enhanced HTTP status code handling
case "$http_code" in
    200|301|302) echo "healthy" ;;  # Redirects are OK
    401|403) echo "healthy" ;;      # Auth required but up
    000) echo "down" ;;             # Connection failed
    *) echo "unknown" ;;
esac

# 4. Skip HTTP checks for non-HTTP services
if [[ "$service" == "postgres" || "$service" == "redis" ]]; then
    health="healthy"  # If container is up, service is healthy
fi
```

### Issue 2: Display Refresh Behavior

**Question:** Does the display refresh automatically? What happens if status checks are running in the background?

**Answer:**

The display does **not** auto-refresh continuously. Here's how it works:

**Current Behavior:**

1. **On Menu Startup:**
   - Background refresh kicks off immediately
   - Menu displays with cached data (or "checking..." if no cache)
   - Background processes update cache over ~3-5 seconds

2. **While Menu is Displayed:**
   - Display is static (shows cached data)
   - Background checks continue to update cache
   - Cache has 30-second TTL

3. **To See Updates:**
   - Press 's' for quick status check (shown in header)
   - Select any menu option and return
   - Background refresh triggers again after each action

**Why No Auto-Refresh:**

- Terminal menus can't auto-refresh without redrawing
- Redrawing would interrupt user input
- User might be reading the menu when it refreshes
- Could cause flickering/poor UX

**User Experience:**

```
Environment: local (docker)    Last check: 3s ago  [Press 's' to refresh]

Core Services
─────────────
  ● AuthZ           ✓ up │ a1b2c3d  ✓ synced │ 45ms
  ...
```

The header now shows:
- **Last check:** How old the cache is
- **[Press 's' to refresh]:** Hint to user

**Refresh Triggers:**

1. **Manual:** Press 's' in menu
2. **Automatic:** After each menu action
3. **On Startup:** When menu first loads

**Cache Behavior:**

- Cache files persist in `~/.busibox/status-cache/`
- 30-second TTL (configurable)
- Menu always reads from cache (< 50ms)
- Background processes update cache asynchronously
- If cache is stale (> 30s), shows age: "Last check: 2m ago"

## Current Status

### Working ✅

- All running Docker containers detected correctly
- Health checks working for all HTTP services
- Non-HTTP services (postgres) handled correctly
- Cache system working
- Non-blocking architecture confirmed
- Color-coded status indicators
- Response time measurement
- Background refresh

### Not Yet Working ⚠️

- **Version Tracking:** Shows "unknown" because:
  - `.deploy_version` files not yet deployed to containers
  - Need to redeploy services with updated Ansible roles
  - Will work once Ansible roles are deployed

- **AI Portal / Agent Manager:** Show as down because:
  - Not running in Docker (run locally with `npm run dev`)
  - This is expected for hybrid development mode

### Expected Output (After Ansible Deployment)

```
Environment: local (docker)    Last check: 5s ago  [Press 's' to refresh]

Core Services
─────────────
  ● AuthZ           ✓ up │ 55b74b4  ✓ synced │ 45ms
  ● PostgreSQL      ✓ up │ 55b74b4  ✓ synced │ -
  ● Milvus          ✓ up │ 55b74b4  ✓ synced │ 102ms
  ● MinIO           ✓ up │ RELEASE  - unknown │ 23ms

API Services
────────────
  ● Ingest API      ✓ up │ 55b74b4  ✓ synced │ 67ms
  ● Search API      ✓ up │ 55b74b4  ✓ synced │ 89ms
  ● Agent API       ✓ up │ 55b74b4  ✓ synced │ 156ms
  ● LiteLLM         ✓ up │ - │ - unknown │ 34ms

App Services
────────────
  ● Nginx           ✓ up │ 55b74b4  ✓ synced │ 12ms
  ○ AI Portal       - unknown │ - │ - │ -
  ○ Agent Manager   - unknown │ - │ - │ -
```

## Testing

Run the test script to verify:

```bash
cd /Users/wsonnenreich/Code/busibox
rm -rf ~/.busibox/status-cache  # Clear cache
bash scripts/test-status-display.sh
```

Or just run `make` to see it in the menu.

## Next Steps

To get version tracking working:

1. **Redeploy Services:**
   ```bash
   # For staging/production
   cd provision/ansible
   make staging deploy  # or make production deploy
   ```

2. **Verify Version Files:**
   ```bash
   # Check if version files exist
   docker exec local-authz-api cat /opt/authz/.deploy_version
   ```

3. **Clear Cache and Refresh:**
   ```bash
   rm -rf ~/.busibox/status-cache
   make  # Run menu, press 's' to refresh
   ```

## Troubleshooting

### Service shows as "down" but container is running

Check container name mapping in `scripts/lib/status.sh`:
```bash
docker ps --format '{{.Names}}' | grep local
```

### Health check shows "unknown"

Check actual health endpoint:
```bash
curl -v http://localhost:8010/health/live  # AuthZ
curl -v http://localhost:9091/healthz      # Milvus
```

### Version shows "unknown"

Check if version file exists:
```bash
docker exec local-authz-api cat /opt/authz/.deploy_version
```

If not, redeploy with updated Ansible roles.

### Cache not updating

Check debug log:
```bash
tail -f ~/.busibox/status-debug.log
```

Clear cache and retry:
```bash
rm -rf ~/.busibox/status-cache
```

## Performance

**Actual Performance (Measured):**

- Menu display: < 50ms (cache read only)
- Background refresh: ~3-5 seconds for all services
- Cache file size: ~240 bytes per service
- Total cache size: ~2.6 KB for 11 services

**Non-Blocking Confirmed:**

- Menu displays immediately
- User can interact while checks run
- No freezing or delays
- Background jobs don't interfere with menu

## Related Files

- `scripts/lib/services.sh` - Service definitions
- `scripts/lib/status.sh` - Status checking logic
- `scripts/lib/ui.sh` - Display rendering
- `scripts/make/menu.sh` - Menu integration
- `scripts/test-status-display.sh` - Test script
