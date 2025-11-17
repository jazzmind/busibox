---
title: Log Viewing Commands - Quick Reference
created: 2025-01-13
updated: 2025-01-13
status: active
category: reference
tags: [logs, commands, quick-reference]
---

# Log Viewing Commands - Quick Reference

## From Admin Workstation

```bash
# View logs
bash scripts/view-app-logs.sh <app-name> <environment> [lines]
bash scripts/view-app-logs.sh ai-portal production 100

# Follow logs (real-time)
bash scripts/tail-app-logs.sh <app-name> <environment>
bash scripts/tail-app-logs.sh ai-portal production
```

## From apps-lxc Container

```bash
# View logs
view-app-logs.sh <app-name> [lines]
view-app-logs.sh ai-portal 100

# Follow logs (real-time)
tail-app-logs.sh <app-name>
tail-app-logs.sh ai-portal

# Or use PM2 directly
pm2 logs <app-name>
pm2 logs ai-portal --lines 100
```

## PM2 Commands

```bash
# List applications
pm2 list

# View logs (real-time)
pm2 logs <app-name>

# View last N lines
pm2 logs <app-name> --lines <N>

# View stdout only
pm2 logs <app-name> --out

# View stderr only
pm2 logs <app-name> --err

# Clear logs
pm2 flush <app-name>
```

## Direct File Access

```bash
# Log files location
/var/log/pm2/<app-name>-out.log
/var/log/pm2/<app-name>-error.log

# View log file
tail -f /var/log/pm2/ai-portal-out.log
tail -n 100 /var/log/pm2/ai-portal-error.log

# Search logs
grep "error" /var/log/pm2/ai-portal-error.log
grep -i "database" /var/log/pm2/*.log
```

## Common App Names

- `ai-portal` - AI Portal application
- `agent-client` - Agent Client application
- `agent-server` - Agent Server (if on same container)

## Container IPs

- **Production**: 10.96.200.201
- **Test**: 10.96.201.201

## Web UI

AI Portal Admin → Apps → [Select Internal App] → Application Logs section

