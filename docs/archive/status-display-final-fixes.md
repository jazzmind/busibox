---
created: 2025-01-18
updated: 2025-01-18
status: completed
category: development
---

# Status Display - Final Fixes

> **Note**: This document references `scripts/docker-rebuild-with-version.sh` which has been removed. Version tracking is now automatic in the Makefile - just use `make docker-build`.

## Issues Found

After initial implementation, two critical issues remained:

### 1. Version Labels Not Applied

**Symptom**: All services showed "unknown" for deployed version

**Root Cause**: The `GIT_COMMIT` environment variable wasn't being passed to Docker Compose during build. Docker Compose substitutes undefined variables with empty strings, so `${GIT_COMMIT:-unknown}` became just "unknown".

**Evidence**:
```bash
$ docker inspect local-ingest-api --format '{{.Config.Labels.version}}'
<no value>

$ echo $GIT_COMMIT
# (empty - not set)
```

### 2. Status Changed to "configured" After Deployment

**Symptom**: After starting Docker containers, status bar showed "configured ◐" instead of "deployed" or "healthy"

**Root Cause**: The `run_quick_health_check` function was using old logic that:
1. Checked if Docker containers were running → set status to "deployed"
2. Then checked for healthy services using old mechanism → found none
3. Downgraded status to "configured"

The old health check didn't know about the new status cache system.

## Fixes Applied

### Fix 1: Properly Pass GIT_COMMIT to Docker Compose

**File**: `scripts/docker-rebuild-with-version.sh`

**Before**:
```bash
export GIT_COMMIT=$(git rev-parse --short HEAD)
docker compose build --build-arg GIT_COMMIT="${GIT_COMMIT}" ...
```

**After**:
```bash
GIT_COMMIT=$(git rev-parse --short HEAD)
export GIT_COMMIT
GIT_COMMIT="${GIT_COMMIT}" docker compose build ...
```

**Why**: Docker Compose needs the variable in the environment at runtime, not as a build arg. The `${GIT_COMMIT}` in docker-compose.yml is substituted during parsing, so it must be in the environment when `docker compose` is invoked.

### Fix 2: Update Quick Health Check to Use Status Cache

**File**: `scripts/lib/health.sh`

**Change**: Modified `run_quick_health_check` to check the status cache and determine health status:

```bash
# Check if we have cached status data to determine if services are healthy
local cache_dir="${HOME}/.busibox/status-cache"
local healthy_count=0
local total_count=0

if [[ -d "$cache_dir" ]]; then
    for cache_file in "$cache_dir"/*.json; do
        [[ -f "$cache_file" ]] || continue
        total_count=$((total_count + 1))
        
        # Check if service is healthy
        local health=$(jq -r '.health // "unknown"' "$cache_file" 2>/dev/null)
        if [[ "$health" == "healthy" ]]; then
            healthy_count=$((healthy_count + 1))
        fi
    done
fi

# If we have cache data and most services are healthy, status is healthy
# Otherwise, status is deployed (containers running but health unknown)
if [[ $total_count -gt 0 && $healthy_count -ge $((total_count * 2 / 3)) ]]; then
    HEALTH_STATUS="$STATUS_HEALTHY"
else
    HEALTH_STATUS="$STATUS_DEPLOYED"
fi
```

**Logic**:
- Reads status cache files
- Counts how many services are healthy
- If ≥66% of services are healthy → status is "healthy"
- Otherwise → status is "deployed" (containers running but health uncertain)

### Fix 3: Updated Documentation

**File**: `docker-compose.local.yml`

Added usage notes about version tracking:

```yaml
# Version Tracking:
#   To enable version tracking in the status display, rebuild containers with:
#   GIT_COMMIT=$(git rev-parse --short HEAD) to set version labels.
```

## How to Apply Fixes

### Step 1: Rebuild Containers with Version Labels

```bash
# Use the helper script (recommended)
bash scripts/docker-rebuild-with-version.sh

# Or manually
export GIT_COMMIT=$(git rev-parse --short HEAD)
docker compose -f docker-compose.local.yml down
GIT_COMMIT="${GIT_COMMIT}" docker compose -f docker-compose.local.yml build \
    authz-api ingest-api search-api agent-api docs-api
docker compose -f docker-compose.local.yml up -d
```

### Step 2: Verify Version Labels

```bash
# Check a container
docker inspect local-ingest-api --format '{{.Config.Labels.version}}'
# Should output: 55b74b4 (current git hash)

# Check all API containers
for svc in authz-api ingest-api search-api agent-api docs-api; do
    echo -n "$svc: "
    docker inspect "local-$svc" --format '{{.Config.Labels.version}}' 2>/dev/null || echo "not found"
done
```

### Step 3: Refresh Status Display

```bash
make
# Press 's' to refresh status
```

## Expected Results

After applying fixes, you should see:

### Status Display

```
Environment: local (docker)                    Last check: 2s ago  [Press 's' to refresh]

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
  ● AI Portal       ✓ up │ f7bbad9  ✓ synced │ ✓ healthy (234ms)
  ● Agent Manager   ✓ up │ 0975c17  ✓ synced │ ✓ healthy (198ms)


  Environment: local (docker)                 Status: healthy ●
──────────────────────────────────────────────────────────────────────
```

### Status Bar

- **Before**: `Status: configured ◐` (wrong)
- **After**: `Status: healthy ●` (correct)

## Technical Details

### Why GIT_COMMIT Must Be in Environment

Docker Compose performs variable substitution during parsing:

```yaml
labels:
  - "version=${GIT_COMMIT:-unknown}"
```

When `docker compose build` runs:
1. Parses docker-compose.yml
2. Substitutes `${GIT_COMMIT}` with value from environment
3. If not in environment, uses default "unknown"
4. Passes resolved value to Docker build

So `GIT_COMMIT` must be:
- Exported to environment: `export GIT_COMMIT=...`
- Or prefixed to command: `GIT_COMMIT=... docker compose ...`

### Why Quick Health Check Needed Update

The menu system calls `run_quick_health_check` on:
- Initial load
- After starting Docker
- After certain operations

This function was using old logic that didn't understand the new status cache. It would:
1. See containers running → "deployed"
2. Check for services using old mechanism → none found
3. Downgrade to "configured"

By making it check the status cache, it now:
1. Sees containers running
2. Checks cache for healthy services
3. If ≥66% healthy → "healthy"
4. Otherwise → "deployed"

This aligns with the new status dashboard system.

## Verification Commands

### Check Container Labels

```bash
# Single container
docker inspect local-ingest-api --format '{{.Config.Labels.version}}'

# All API containers
for svc in authz-api ingest-api search-api agent-api docs-api; do
    echo "$svc: $(docker inspect local-$svc --format '{{.Config.Labels.version}}' 2>/dev/null)"
done
```

### Check Status Cache

```bash
# List cache files
ls -lh ~/.busibox/status-cache/

# View a cache entry
cat ~/.busibox/status-cache/ingest-api.json | jq .

# Count healthy services
grep -l '"health":"healthy"' ~/.busibox/status-cache/*.json | wc -l
```

### Check Status File

```bash
# View current status
cat .busibox-state | grep INSTALL_STATUS

# Should show: INSTALL_STATUS=healthy (or deployed)
```

## Files Modified

1. `scripts/docker-rebuild-with-version.sh` - Fixed GIT_COMMIT passing
2. `scripts/lib/health.sh` - Updated quick health check to use status cache
3. `docker-compose.local.yml` - Added documentation about version tracking

## Related Documentation

- `docs/development/status-display-implementation.md` - Original implementation
- `docs/development/status-display-fixes.md` - Initial fixes for macOS compatibility
- `docs/development/status-display-refresh-fix.md` - Fix for 's' refresh key
- `docs/development/status-display-version-and-apps.md` - App detection and version tracking
- `docs/development/status-display-final-fixes.md` - This document

## Troubleshooting

### Version Still Shows "unknown"

```bash
# Check if GIT_COMMIT was set during build
docker inspect local-ingest-api --format '{{.Config.Labels}}'

# If empty, rebuild with:
bash scripts/docker-rebuild-with-version.sh
```

### Status Shows "configured" Instead of "healthy"

```bash
# Check if status cache exists
ls ~/.busibox/status-cache/

# If empty, refresh status:
make
# Press 's'

# Wait 3 seconds for cache to populate
```

### Services Show "down" But Containers Are Running

```bash
# Check container names
docker ps --format "{{.Names}}"

# Should see: local-authz-api, local-ingest-api, etc.
# Not: local-authz, local-ingest

# If wrong names, rebuild:
docker compose -f docker-compose.local.yml down
docker compose -f docker-compose.local.yml up -d
```
