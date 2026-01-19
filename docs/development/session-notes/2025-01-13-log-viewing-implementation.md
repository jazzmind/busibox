---
title: Log Viewing Implementation
created: 2025-01-13
updated: 2025-01-13
status: completed
category: session-notes
tags: [logs, monitoring, admin-ui, pm2]
---

# Log Viewing Implementation - Session Notes

**Date:** January 13, 2025  
**Status:** Completed

## Overview

Implemented comprehensive log viewing capabilities for applications running in the Busibox platform, providing three access methods: Admin UI, CLI scripts, and direct PM2 commands.

## What Was Implemented

### 1. CLI Scripts (Busibox)

Two bash scripts for viewing application logs from either the admin workstation or from within the apps container.

**Scripts:**
- `scripts/view-app-logs.sh` - View last N lines of logs
- `scripts/tail-app-logs.sh` - Follow logs in real-time (tail -f style)

**Features:**
- Auto-detects execution context (host vs container)
- Supports production and test environments
- Color-coded output
- Error handling and validation
- Helpful usage examples
- SSH integration for remote access

**Installation:**
- Scripts will be deployed to `/usr/local/bin` on apps container via Ansible
- Executable permissions set
- Can be run from host or container

### 2. Admin UI (AI Portal)

Web-based log viewer integrated into the admin interface.

**Components:**
- `AppLogsViewer` - React component for log display
- API endpoint: `GET /api/admin/apps/[appId]/logs`
- Integrated into app detail pages

**Features:**
- View 50-1000 lines of logs
- Auto-refresh every 5 seconds (optional)
- Color-coded log levels (error in red, warn in yellow, info in gray)
- Separate stdout and stderr display
- Terminal-style dark interface
- One-click refresh
- CLI command hints

**Location:** Admin → Apps → [Internal App] → Application Logs section

### 3. Documentation

Comprehensive documentation in multiple formats:

**Busibox Documentation:**
- `docs/guides/viewing-application-logs.md` - Complete user guide
  - All three viewing methods
  - Usage examples
  - Troubleshooting
  - Best practices
  - Log locations and formats

- `docs/reference/log-viewing-commands.md` - Quick reference
  - Command cheat sheet
  - Common app names
  - Container IPs
  - PM2 commands

**AI Portal Documentation:**
- `LOG_VIEWING.md` - Implementation details
  - API documentation
  - Component usage
  - Security considerations
  - Future enhancements

### 4. Ansible Integration

Created Ansible task for deploying log scripts:

**File:** `provision/ansible/roles/apps/tasks/log-scripts.yml`

**Actions:**
- Creates `/usr/local/bin` directory
- Copies scripts to container
- Sets proper permissions (0755)
- Creates `/var/log/pm2` directory

**Integration:** Can be included in apps role main.yml

## Technical Details

### Log Sources

Applications use PM2 process manager which captures logs:

```
/var/log/pm2/
├── <app-name>-out.log    # stdout
└── <app-name>-error.log  # stderr
```

### Script Behavior

**view-app-logs.sh:**
- Detects if PM2 is available (in container) or needs SSH (on host)
- Validates app exists in PM2 before fetching logs
- Shows combined stdout + stderr
- Displays last N lines (default: 100)

**tail-app-logs.sh:**
- Follows logs in real-time
- Ctrl+C to stop
- Uses `pm2 logs` under the hood

### API Implementation

**Endpoint:** `/api/admin/apps/[appId]/logs?lines=N`

**Process:**
1. Verify admin authentication
2. Check app is INTERNAL type
3. Verify PM2 is available locally
4. Fetch logs using `pm2 logs --json --nostream --raw`
5. Parse JSON or fall back to text parsing
6. Return structured log entries

**Log Entry Format:**
```typescript
{
  timestamp: string;  // ISO 8601
  level: string;      // 'info', 'warn', 'error'
  message: string;    // Log message
  type: 'stdout' | 'stderr';
}
```

### Security

- Admin role required for all log access
- Only works for INTERNAL apps
- Logs streamed through API (not direct file access)
- SSH keys required for remote access
- No authentication bypass

## Usage Examples

### From Admin Workstation

```bash
# View last 100 lines from production
bash scripts/view-app-logs.sh ai-portal production 100

# Follow logs in real-time
bash scripts/tail-app-logs.sh ai-portal production

# View test environment
bash scripts/view-app-logs.sh agent-manager test 50
```

### From apps-lxc Container

```bash
# SSH to container first
ssh root@10.96.200.201

# Use scripts
view-app-logs.sh ai-portal 100
tail-app-logs.sh ai-portal

# Or use PM2 directly
pm2 logs ai-portal
pm2 logs ai-portal --lines 100
pm2 logs ai-portal --err  # stderr only
```

### From Admin UI

1. Log in as admin
2. Navigate to Admin → Apps
3. Click on internal app (e.g., ai-portal)
4. Scroll to "Application Logs" section
5. Select line count (50-1000)
6. Toggle auto-refresh if desired
7. Click Refresh to update

## Limitations

### Current Limitations

1. **Admin UI:** Only works when AI Portal runs in same container as apps (current setup)
2. **No Real-Time Streaming:** Admin UI uses polling, not WebSocket/SSE
3. **PM2 Buffer:** Historical logs limited to PM2's buffer size
4. **No Log Search:** Cannot search/filter logs in UI (use CLI + grep)
5. **No Export:** Cannot download logs from UI

### Future Enhancements

Potential improvements for future:
- Real-time log streaming via WebSockets or SSE
- Remote log fetching via SSH for cross-container setups
- Log search and filtering in UI
- Export logs to file
- Integration with log aggregation services
- Log level filtering
- Multi-app log viewing
- Log rotation configuration UI

## Files Changed

### Busibox Repository

**New Files:**
- `scripts/view-app-logs.sh`
- `scripts/tail-app-logs.sh`
- `provision/ansible/roles/apps/tasks/log-scripts.yml`
- `docs/guides/viewing-application-logs.md`
- `docs/reference/log-viewing-commands.md`

**Commit:** 91e1526 - Add application log viewing infrastructure

### AI Portal Repository

**New Files:**
- `src/app/api/admin/apps/[appId]/logs/route.ts`
- `src/components/admin/AppLogsViewer.tsx`
- `LOG_VIEWING.md`

**Modified Files:**
- `src/app/admin/apps/[appId]/page.tsx` - Integrated AppLogsViewer component

**Commits:**
- 5e25e40 - Fix build errors in deployment system
- 01c46f6 - Add admin log viewer UI for applications

## Testing Checklist

Before deploying to production:

- [ ] Test view-app-logs.sh from host
- [ ] Test tail-app-logs.sh from host
- [ ] Test scripts from inside container
- [ ] Verify PM2 log files exist
- [ ] Test admin UI log viewer
- [ ] Test auto-refresh functionality
- [ ] Test with different line counts
- [ ] Verify admin-only access
- [ ] Test with non-existent app
- [ ] Verify error handling

## Deployment Steps

### 1. Deploy to Busibox

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox
git push origin 004-updated-ingestion-service

# Deploy Ansible changes
cd provision/ansible
make production  # or make test

# Verify scripts installed
ssh root@10.96.200.201
ls -la /usr/local/bin/view-app-logs.sh
ls -la /usr/local/bin/tail-app-logs.sh
```

### 2. Deploy AI Portal

```bash
cd /Users/wessonnenreich/Code/sonnenreich/ai-portal
git push origin main

# Use deployment system to deploy v0.1.0
# Or manual deployment if needed
```

### 3. Verify

```bash
# Test CLI scripts
bash scripts/view-app-logs.sh ai-portal production 50

# Test Admin UI
# Navigate to https://<domain>/admin/apps/<app-id>
# Check Application Logs section
```

## Quick Reference

**View logs from host:**
```bash
bash scripts/view-app-logs.sh <app-name> <env> [lines]
```

**Follow logs from host:**
```bash
bash scripts/tail-app-logs.sh <app-name> <env>
```

**Inside container:**
```bash
view-app-logs.sh <app-name> [lines]
tail-app-logs.sh <app-name>
pm2 logs <app-name>
```

**Container IPs:**
- Production: 10.96.200.201
- Test: 10.96.201.201

**Common Apps:**
- ai-portal
- agent-manager
- agent-server (if same container)

## Notes

- PM2 log format includes timestamps automatically
- Logs are in `/var/log/pm2/` directory
- PM2 automatically rotates logs (can be configured)
- Scripts use `set -euo pipefail` for safety
- Color output improves readability
- Auto-detection of execution context simplifies usage

## Related

- Deployment System: Already implemented in v0.1.0
- Application Management: Existing admin functionality
- PM2 Process Management: Used for all Node.js apps

