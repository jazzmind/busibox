---
title: Viewing Application Logs
created: 2025-01-13
updated: 2025-01-13
status: active
category: guides
tags: [logs, monitoring, pm2, troubleshooting]
---

# Viewing Application Logs

This guide explains how to view logs for applications running on the Busibox platform.

## Overview

Applications deployed to the `apps-lxc` container are managed by PM2, which captures and stores application logs. Logs can be viewed in three ways:

1. **AI Portal Admin UI** - Web-based log viewer for admins
2. **Command-line scripts** - For viewing logs from host or container
3. **Direct PM2 commands** - When logged into the container

## Methods

### 1. AI Portal Admin UI (Recommended for Non-Technical Users)

The AI Portal provides a web-based interface for viewing application logs.

**Access:**
1. Log in to AI Portal as an admin
2. Navigate to **Admin** → **Apps**
3. Click on an internal application (e.g., `ai-portal`, `agent-manager`)
4. Scroll down to the **Application Logs** section

**Features:**
- View last 50-1000 lines of logs
- Auto-refresh every 5 seconds (optional)
- Color-coded log levels (error, warning, info)
- Separate stdout and stderr streams
- Timestamped entries
- One-click refresh

**Limitations:**
- Only works when AI Portal runs in the same container as the apps (current setup)
- Cannot follow logs in real-time (use auto-refresh instead)
- Historical logs limited to PM2's buffer

### 2. Command-Line Scripts

Two scripts are provided for viewing logs from the command line.

#### view-app-logs.sh - View Historical Logs

Display the last N lines of logs for an application.

**From Admin Workstation:**

```bash
# View last 100 lines (default)
bash scripts/view-app-logs.sh ai-portal production

# View last 50 lines
bash scripts/view-app-logs.sh ai-portal production 50

# View test environment
bash scripts/view-app-logs.sh agent-manager test 100
```

**From apps-lxc Container:**

```bash
# View last 100 lines (default)
view-app-logs.sh ai-portal

# View last 200 lines
view-app-logs.sh ai-portal 200
```

**Usage:**

```
From host:    bash scripts/view-app-logs.sh <app-name> [environment] [lines]
In container: view-app-logs.sh <app-name> [lines]

Arguments:
  app-name      Name of the application (e.g., ai-portal, agent-manager)
  environment   Environment to connect to: production or test (host only)
  lines         Number of log lines to display (default: 100)
```

#### tail-app-logs.sh - Follow Logs in Real-Time

Stream logs as they are generated (like `tail -f`).

**From Admin Workstation:**

```bash
# Follow production logs
bash scripts/tail-app-logs.sh ai-portal production

# Follow test environment logs
bash scripts/tail-app-logs.sh agent-manager test
```

**From apps-lxc Container:**

```bash
# Follow logs
tail-app-logs.sh ai-portal
```

**Usage:**

```
From host:    bash scripts/tail-app-logs.sh <app-name> [environment]
In container: tail-app-logs.sh <app-name>

Arguments:
  app-name      Name of the application
  environment   Environment to connect to: production or test (host only)
```

Press `Ctrl+C` to stop following logs.

### 3. Direct PM2 Commands

When SSH'd into the `apps-lxc` container, you can use PM2 commands directly.

**SSH to Container:**

```bash
# Production
ssh root@10.96.200.201

# Test
ssh root@10.96.201.201
```

**PM2 Commands:**

```bash
# List all applications
pm2 list

# View logs for specific app (real-time)
pm2 logs ai-portal

# View last 100 lines
pm2 logs ai-portal --lines 100

# View only stdout
pm2 logs ai-portal --out

# View only stderr (errors)
pm2 logs ai-portal --err

# View all apps
pm2 logs

# Clear logs
pm2 flush ai-portal
```

## Log Locations

Logs are stored in `/var/log/pm2/` on the `apps-lxc` container:

```
/var/log/pm2/
├── ai-portal-out.log      # Standard output
├── ai-portal-error.log    # Standard error
├── agent-manager-out.log
├── agent-manager-error.log
└── ...
```

**Direct File Access:**

```bash
# View log files directly
tail -f /var/log/pm2/ai-portal-out.log
tail -f /var/log/pm2/ai-portal-error.log

# View last 100 lines
tail -n 100 /var/log/pm2/ai-portal-out.log

# Search logs for specific text
grep "error" /var/log/pm2/ai-portal-error.log

# View logs for specific time period (if timestamps present)
grep "2025-01-13" /var/log/pm2/ai-portal-out.log
```

## Log Format

PM2 logs include timestamps and are formatted as:

```
2025-01-13 14:30:45 Z: [INFO] Application started
2025-01-13 14:30:46 Z: [DEBUG] Connecting to database
2025-01-13 14:30:47 Z: [ERROR] Connection failed: timeout
```

## Troubleshooting

### "Application not found in PM2"

**Cause:** The application name doesn't match a running PM2 process.

**Solution:**
```bash
# List all PM2 processes
pm2 list

# Use the exact name shown in the list
```

### "PM2 not available"

**Cause:** Running script from a location where PM2 isn't installed.

**Solution:** Use the host scripts which will SSH to the container, or SSH to the container first.

### "Connection refused" when using host scripts

**Cause:** SSH access to the container is not configured or firewall is blocking.

**Solution:**
```bash
# Test SSH connection
ssh root@10.96.200.201

# Check if container is running
pct status 202  # Production apps-lxc
pct status 2202 # Test apps-lxc
```

### Logs are empty or missing

**Cause:** Application hasn't logged anything, or logs were cleared.

**Solution:**
```bash
# Check if application is running
pm2 describe ai-portal

# Check application status
pm2 list

# Restart application if needed
pm2 restart ai-portal

# Check recent logs
pm2 logs ai-portal --lines 10
```

## Log Rotation

PM2 automatically manages log file sizes. To configure log rotation:

```bash
# Install PM2 log rotate module
pm2 install pm2-logrotate

# Configure rotation (if needed)
pm2 set pm2-logrotate:max_size 10M
pm2 set pm2-logrotate:retain 7
pm2 set pm2-logrotate:compress true
```

## Best Practices

1. **Regular Monitoring**: Check logs regularly for errors and warnings
2. **Use Appropriate Method**: 
   - Quick checks → Admin UI
   - Troubleshooting → Command-line scripts
   - Deep investigation → Direct PM2 or file access
3. **Search Before Scrolling**: Use `grep` to find specific errors
4. **Clear Old Logs**: Use `pm2 flush` to clear logs when troubleshooting
5. **Save Important Logs**: Copy critical error logs before clearing

## Related Documentation

- [Deployment System](./deployment-system.md) - Application deployment
- [Application Management](../configuration/application-configuration.md) - Managing apps
- [Troubleshooting Guide](../troubleshooting/common-issues.md) - Common issues

## Script Reference

### view-app-logs.sh

- **Location (host)**: `scripts/view-app-logs.sh`
- **Location (container)**: `/usr/local/bin/view-app-logs.sh`
- **Purpose**: View last N lines of application logs
- **Execution Context**: Admin workstation OR apps-lxc container

### tail-app-logs.sh

- **Location (host)**: `scripts/tail-app-logs.sh`
- **Location (container)**: `/usr/local/bin/tail-app-logs.sh`
- **Purpose**: Follow application logs in real-time
- **Execution Context**: Admin workstation OR apps-lxc container

## Examples

### Example 1: Debugging a Startup Issue

```bash
# View recent logs to see startup errors
bash scripts/view-app-logs.sh ai-portal production 50

# If error found, follow logs during restart
bash scripts/tail-app-logs.sh ai-portal production &
ssh root@10.96.200.201 'pm2 restart ai-portal'
```

### Example 2: Monitoring Deployment

```bash
# Open terminal 1: Follow logs
bash scripts/tail-app-logs.sh ai-portal production

# Terminal 2: Trigger deployment via AI Portal
# (Navigate to Admin → Apps → ai-portal → Deploy)

# Watch logs in terminal 1 for deployment progress
```

### Example 3: Finding Specific Errors

```bash
# SSH to container
ssh root@10.96.200.201

# Search for database errors
grep -i "database" /var/log/pm2/ai-portal-error.log

# View context around error
grep -C 5 "ECONNREFUSED" /var/log/pm2/ai-portal-error.log

# Find when error first occurred
grep "ECONNREFUSED" /var/log/pm2/ai-portal-error.log | head -1
```

