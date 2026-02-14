---
created: 2025-01-18
updated: 2025-01-18
status: completed
category: development
---

# Status Display - Enhanced Version Tracking

## Issues Fixed

### 1. Status Reverts to "configured" After Deploy

**Problem**: After deploying Docker images via the menu, the status bar incorrectly changed from "healthy" to "configured".

**Root Cause**: Line 787 in `menu.sh` explicitly set status to "configured" after building:
```bash
(cd "$REPO_ROOT" && make docker-build)
set_install_status "configured"  # ← Wrong!
```

**Fix**: Removed the hardcoded status update and let the health check system determine the correct status:
```bash
(cd "$REPO_ROOT" && make docker-build)
# Don't set status - let health check determine it
# Refresh status cache after build
refresh_all_services_async "$env" "$backend" &
```

### 2. Version Information Not Displayed

**Problem**: All services showed "unknown" for both deployed and current versions.

**Root Causes**:
1. Docker containers built without `GIT_COMMIT` environment variable
2. No fallback for local builds without version labels
3. Current version not being retrieved from busibox repo

**Fixes**:

**a) Better Fallback for Local Builds**:
```bash
# If no version label or .deploy_version file, return "local"
if [[ -n "$version" ]]; then
    echo "$version"
else
    echo "local"  # Indicates local build without version tracking
fi
```

**b) Enhanced Version Display**:
- Shows deployed version → current version
- Handles "local" builds specially
- Compares versions to show sync status

### 3. No Branch Tracking

**Problem**: System didn't track which branch was deployed, making it impossible to know if services were up-to-date with their branch.

**Solution**: Enhanced version tracking to show both deployed and current commits:

**Display Format**:
```
# When versions match
a5eb49a  ✓ synced

# When behind
a1b2c3d → a5eb49a  ⚠ behind

# Local build (no version label)
local → a5eb49a  ◆ local
```

## Implementation Details

### Version Detection Logic

#### For Docker Services (busibox repo)

1. **Check Docker label** (set during build with `GIT_COMMIT`):
   ```bash
   docker inspect local-ingest-api --format '{{.Config.Labels.version}}'
   ```

2. **Check .deploy_version file** (for Ansible deployments):
   ```bash
   docker exec local-ingest-api cat /opt/ingest-api/.deploy_version
   ```

3. **Fallback to "local"** if neither exists

#### For Host Services (ai-portal, agent-manager)

Get current git hash from their respective repos:
```bash
cd ../ai-portal && git rev-parse --short HEAD
cd ../agent-manager && git rev-parse --short HEAD
```

#### Current Version

Get current git hash from service's repo:
```bash
# For busibox services
cd /path/to/busibox && git rev-parse --short HEAD

# For ai-portal
cd /path/to/ai-portal && git rev-parse --short HEAD

# For agent-manager
cd /path/to/agent-manager && git rev-parse --short HEAD
```

### Sync State Logic

```bash
compare_versions() {
    local deployed=$1
    local current=$2
    
    if [[ "$deployed" == "local" ]]; then
        echo "local"      # Local build, no version tracking
    elif [[ "$deployed" == "unknown" || "$current" == "unknown" ]]; then
        echo "unknown"    # Can't determine
    elif [[ "$deployed" == "$current" ]]; then
        echo "synced"     # Up to date
    else
        echo "behind"     # Needs rebuild/redeploy
    fi
}
```

### Display Indicators

| Sync State | Symbol | Color | Meaning |
|------------|--------|-------|---------|
| `synced` | ✓ synced | Green | Deployed version matches current commit |
| `behind` | ⚠ behind | Yellow | Deployed version is older than current commit |
| `local` | ◆ local | Blue | Local build without version tracking |
| `unknown` | - unknown | Dim | Cannot determine version |

## Usage

### For Local Docker Development

The Makefile automatically includes version tracking when building:

```bash
# Build all services with version labels
make docker-build

# Build specific service with version labels
make docker-build SERVICE=ingest-api

# The Makefile automatically:
# 1. Gets current git commit: GIT_COMMIT=$(git rev-parse --short HEAD)
# 2. Passes it to docker compose: GIT_COMMIT=a5eb49a docker compose build
```

**Result**:
```
Ingest API      ✓ up │ a5eb49a  ✓ synced │ ✓ healthy (67ms)
```

**Note**: Version tracking is now automatic - no separate script needed!

### Understanding the Display

#### Example 1: Everything Synced
```
Core Services
─────────────
  ● AuthZ           ✓ up │ a5eb49a  ✓ synced │ ✓ healthy (45ms)
  ● PostgreSQL      ✓ up │ -        - │ ✓ healthy
```

**Meaning**: AuthZ container was built from commit `a5eb49a`, which is the current HEAD of the busibox repo.

#### Example 2: Behind Current
```
API Services
────────────
  ● Ingest API      ✓ up │ a1b2c3d → a5eb49a  ⚠ behind │ ✓ healthy (67ms)
```

**Meaning**: Ingest API container was built from commit `a1b2c3d`, but current HEAD is `a5eb49a`. Need to rebuild.

#### Example 3: Local Build
```
API Services
────────────
  ● Search API      ✓ up │ local → a5eb49a  ◆ local │ ✓ healthy (54ms)
```

**Meaning**: Search API was built locally without version tracking. Current HEAD is `a5eb49a`.

#### Example 4: Host Services
```
App Services
────────────
  ● AI Portal       ✓ up │ f7bbad9  ✓ synced │ ✓ healthy (234ms)
  ● Agent Manager   ✓ up │ 0975c17  ✓ synced │ ✓ healthy (198ms)
```

**Meaning**: Apps running via `npm run dev` show their current git hash (always "synced" since they're running the current code).

### Workflow Examples

#### Scenario 1: Made Changes, Need to Rebuild

```bash
# 1. Make code changes
vim srv/ingest/src/api/main.py

# 2. Commit changes
git add .
git commit -m "fix: improve ingestion performance"

# 3. Check status
make
# Shows: Ingest API ✓ up │ a1b2c3d → b2c3d4e  ⚠ behind

# 4. Rebuild with new version
make docker-build

# 5. Check status again
make
# Shows: Ingest API ✓ up │ b2c3d4e  ✓ synced
```

#### Scenario 2: Working on Feature Branch

```bash
# 1. Create feature branch
git checkout -b feature/new-search

# 2. Make changes and commit
# ... code changes ...
git commit -m "feat: add new search algorithm"

# 3. Rebuild containers
make docker-build

# 4. Check status
make
# Shows: Search API ✓ up │ c3d4e5f  ✓ synced
# (c3d4e5f is HEAD of feature/new-search branch)

# 5. Switch back to main
git checkout main

# 6. Check status
make
# Shows: Search API ✓ up │ c3d4e5f → a5eb49a  ⚠ behind
# (Container still has feature branch code)
```

## Files Modified

1. **`scripts/make/menu.sh`**:
   - Removed hardcoded `set_install_status "configured"` after build
   - Added background status refresh after build

2. **`scripts/lib/status.sh`**:
   - Enhanced `get_deployed_version()` to return "local" for untracked builds
   - Updated `compare_versions()` to handle "local" state
   - Already had `current_version` tracking in cache

3. **`scripts/lib/ui.sh`**:
   - Updated `get_sync_indicator()` to show "◆ local" for local builds
   - Enhanced `render_service_line()` to display both deployed and current versions
   - Format: `deployed → current` when different, just `version` when same

## Troubleshooting

### All Services Show "local → ..."

**Cause**: This shouldn't happen with the updated Makefile, which automatically sets `GIT_COMMIT`.

**If it does happen**:
```bash
# Rebuild to get version labels
make docker-build
```

### Services Show "behind" After Commit

**Expected**: This is correct! You made changes but haven't rebuilt containers.

**Solution**:
```bash
# Rebuild to sync
make docker-build
```

### Services Show "unknown → unknown"

**Causes**:
1. Git repository not initialized
2. Not in a git repository
3. Git command failed

**Solution**:
```bash
# Check git status
cd /path/to/busibox
git status

# Ensure you're in a git repo
git rev-parse --short HEAD
```

### AI Portal/Agent Manager Show Wrong Version

**Cause**: These are host services running via `npm run dev`, so they always show the current git hash of their repos.

**Expected**: They should always show "synced" since they're running the current code.

## Future Enhancements

Potential improvements:

1. **Branch Name Display**: Show which branch is deployed
   ```
   Ingest API  ✓ up │ a5eb49a (main)  ✓ synced │ ✓ healthy
   ```

2. **Commit Message**: Show first line of commit message on hover
   ```
   Ingest API  ✓ up │ a5eb49a "fix: improve performance"  ✓ synced
   ```

3. **Time Since Deploy**: Show how long ago the version was deployed
   ```
   Ingest API  ✓ up │ a5eb49a (2h ago)  ✓ synced │ ✓ healthy
   ```

4. **Auto-Rebuild**: Offer to rebuild when services are behind
   ```
   [!] 3 services are behind. Press 'r' to rebuild all.
   ```

5. **Diff Link**: Show GitHub/GitLab diff link for behind services
   ```
   Ingest API  ⚠ behind │ a1b2c3d → a5eb49a [view diff]
   ```
