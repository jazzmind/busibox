---
created: 2025-01-18
updated: 2025-01-18
status: completed
category: development
---

# Status Display - Understanding the Output

## Display Format

Each service line shows:
```
  ● ServiceName      ✓ up │ deployed → current  sync_indicator │ health (time)
```

## Reading the Display

### Status Symbol (Left)

| Symbol | Meaning |
|--------|---------|
| ● | Service container/process is running |
| ○ | Service container/process is not running |
| ◷ | Status check in progress |

### Health Indicator

| Indicator | Meaning |
|-----------|---------|
| ✓ up | Service is running |
| ✗ down | Service is not running |
| - unknown | Cannot determine status |

### Version Information

The version display differs based on service type:

#### Busibox Services (AuthZ, Ingest API, Search API, Agent API, Docs API)

Shows git commit hashes:

**`local → 0b90e27  ◆ local`**
- **`local`** = Container was built without version tracking
- **`0b90e27`** = Current git commit in busibox repo
- **`◆ local`** = Indicator meaning "rebuild needed to track version"

**`0b90e27  ✓ synced`**
- **`0b90e27`** = Both deployed and current are this commit
- **`✓ synced`** = Container is up-to-date

**`a1b2c3d → 0b90e27  ⚠ behind`**
- **`a1b2c3d`** = Container was built from this commit
- **`0b90e27`** = Current HEAD of busibox repo
- **`⚠ behind`** = Need to rebuild to get latest code

#### External Services (Milvus, MinIO, LiteLLM, PostgreSQL, Redis)

Shows Docker image tags:

**`v2.6.5  ✓ synced`** (Milvus)
- **`v2.6.5`** = Running container version matches docker-compose.yml
- **`✓ synced`** = No update needed

**`v2.6.4 → v2.6.5  ⚠ behind`** (if behind)
- **`v2.6.4`** = Currently running version
- **`v2.6.5`** = Version specified in docker-compose.yml
- **`⚠ behind`** = Need to pull new image and restart

**`latest  ✓ synced`** (MinIO)
- **`latest`** = Using latest tag (always shows as synced)

#### Host Services (AI Portal, Agent Manager)

Shows git commit from their respective repos:

**`f7bbad9  ✓ synced`**
- **`f7bbad9`** = Current commit in ai-portal repo
- **`✓ synced`** = Always synced (running current code via `npm run dev`)

### Sync Indicators

| Indicator | Meaning | Action Needed |
|-----------|---------|---------------|
| ✓ synced | Deployed version matches current | None |
| ⚠ behind | Deployed version is older | Rebuild/redeploy |
| ◆ local | Local build without version tracking | Run `make docker-build` |
| - unknown | Cannot determine version | Check logs |

### Health & Response Time

**`✓ healthy (67ms)`**
- Service health endpoint responded successfully
- Response time was 67 milliseconds

**`⚠ slow (850ms)`**
- Service is responding but slowly (>500ms)

**`✗ down`**
- Service health endpoint not responding

**`-`**
- Service doesn't have a health endpoint (e.g., PostgreSQL, Redis)

## Example Displays

### Example 1: Fresh Build, Everything Synced

```
Core Services
─────────────
  ● AuthZ           ✓ up │ 0b90e27  ✓ synced │ ✓ healthy (45ms)
  ● PostgreSQL      ✓ up │ 16-alpine  ✓ synced │ ✓ healthy
  ● Redis           ✓ up │ 7-alpine  ✓ synced │ ✓ healthy
  ● Milvus          ✓ up │ v2.6.5  ✓ synced │ ✓ healthy (123ms)
  ● MinIO           ✓ up │ latest  ✓ synced │ ✓ healthy (32ms)

API Services
────────────
  ● Ingest API      ✓ up │ 0b90e27  ✓ synced │ ✓ healthy (67ms)
  ● Search API      ✓ up │ 0b90e27  ✓ synced │ ✓ healthy (54ms)
  ● Agent API       ✓ up │ 0b90e27  ✓ synced │ ✓ healthy (43ms)
  ● LiteLLM         ✓ up │ main-latest  ✓ synced │ ✓ healthy (89ms)

App Services
────────────
  ● Nginx           ✓ up │ alpine  ✓ synced │ ✓ healthy (12ms)
  ● AI Portal       ✓ up │ f7bbad9  ✓ synced │ ✓ healthy (234ms)
  ● Agent Manager   ✓ up │ 0975c17  ✓ synced │ ✓ healthy (198ms)
```

**Interpretation**: Everything is running and up-to-date. No action needed.

### Example 2: After Making Code Changes

```
API Services
────────────
  ● Ingest API      ✓ up │ a1b2c3d → 0b90e27  ⚠ behind │ ✓ healthy (67ms)
  ● Search API      ✓ up │ 0b90e27  ✓ synced │ ✓ healthy (54ms)
  ● Agent API       ✓ up │ 0b90e27  ✓ synced │ ✓ healthy (43ms)
```

**Interpretation**: 
- You made changes to Ingest API and committed them
- Container still has old code (a1b2c3d)
- Current code is 0b90e27
- **Action**: Run `make docker-build SERVICE=ingest-api`

### Example 3: Local Build Without Version Tracking

```
API Services
────────────
  ● Ingest API      ✓ up │ local → 0b90e27  ◆ local │ ✓ healthy (67ms)
  ● Search API      ✓ up │ local → 0b90e27  ◆ local │ ✓ healthy (54ms)
  ● Agent API       ✓ up │ local → 0b90e27  ◆ local │ ✓ healthy (43ms)
```

**Interpretation**:
- Containers were built without `GIT_COMMIT` environment variable
- Can't track exact version, but they work
- **Action**: Run `make docker-build` to enable version tracking

### Example 4: External Service Update Available

```
Core Services
─────────────
  ● Milvus          ✓ up │ v2.6.4 → v2.6.5  ⚠ behind │ ✓ healthy (123ms)
```

**Interpretation**:
- docker-compose.yml specifies v2.6.5
- Container is running v2.6.4
- **Action**: 
  1. Update docker-compose.yml if v2.6.4 is desired, OR
  2. Pull new image: `docker compose -f docker-compose.local.yml pull milvus`
  3. Restart: `docker compose -f docker-compose.local.yml up -d milvus`

### Example 5: Service Down

```
API Services
────────────
  ○ Ingest API      ✗ down │ unknown  - unknown │ -
```

**Interpretation**:
- Container is not running
- **Action**: Start it with `docker compose -f docker-compose.local.yml up -d ingest-api`

## Common Workflows

### After Committing Code Changes

```bash
# 1. Check status
make
# Shows: Ingest API ✓ up │ old → new  ⚠ behind

# 2. Rebuild affected service
make docker-build SERVICE=ingest-api

# 3. Restart service
docker compose -f docker-compose.local.yml up -d ingest-api

# 4. Check status again
make
# Shows: Ingest API ✓ up │ new  ✓ synced
```

### Updating External Service

```bash
# 1. Update version in docker-compose.local.yml
vim docker-compose.local.yml
# Change: image: milvusdb/milvus:v2.6.4
# To:     image: milvusdb/milvus:v2.6.5

# 2. Pull new image
docker compose -f docker-compose.local.yml pull milvus

# 3. Restart service
docker compose -f docker-compose.local.yml up -d milvus

# 4. Check status
make
# Shows: Milvus ✓ up │ v2.6.5  ✓ synced
```

### Enabling Version Tracking

```bash
# If services show "local → hash  ◆ local"

# Rebuild all services with version tracking
make docker-build

# Or rebuild specific service
make docker-build SERVICE=ingest-api
```

## Troubleshooting

### Q: Why does it show "local → hash"?

**A**: Containers were built without the `GIT_COMMIT` environment variable. The updated Makefile now automatically sets this, so just run `make docker-build` to rebuild with version tracking.

### Q: Why is AI Portal always "synced"?

**A**: AI Portal runs via `npm run dev` on your host machine, so it's always running the current code from its repo. The version shown is the current git hash of the ai-portal repo.

### Q: What does "latest" mean for MinIO?

**A**: The docker-compose.yml specifies `minio/minio:latest`, which means "always use the newest version". It will always show as synced because there's no specific version to compare against.

### Q: Service shows "behind" but I didn't change anything

**A**: Someone else may have pushed changes to the repo, or you pulled changes. Run `make docker-build` to rebuild with the latest code.

### Q: How do I know which services need rebuilding?

**A**: Look for the `⚠ behind` indicator. Any service showing this needs to be rebuilt.

## Quick Reference

| Display | Meaning | Action |
|---------|---------|--------|
| `hash  ✓ synced` | Up to date | None |
| `old → new  ⚠ behind` | Out of date | `make docker-build SERVICE=name` |
| `local → hash  ◆ local` | No version tracking | `make docker-build` |
| `✗ down` | Not running | Start the service |
| `⚠ slow (>500ms)` | Responding slowly | Check logs, may need restart |
