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

# Or use journalctl directly
journalctl -u <app-name>.service -f
journalctl -u ai-portal.service -n 100 --no-pager
```

## Systemd/Journalctl Commands

```bash
# List running services
systemctl list-units --type=service --state=running | grep -E '(ai-portal|agent-client|doc-intel|innovation)'

# View logs (real-time)
journalctl -u <app-name>.service -f

# View last N lines
journalctl -u <app-name>.service -n <N> --no-pager

# View logs since specific time
journalctl -u <app-name>.service --since "1 hour ago"
journalctl -u <app-name>.service --since "2025-01-13 10:00:00"

# View logs with priority level
journalctl -u <app-name>.service -p err  # errors only
journalctl -u <app-name>.service -p warning  # warnings and above

# Search logs
journalctl -u <app-name>.service | grep "error"
journalctl -u <app-name>.service | grep -i "database"
```

## Direct File Access

```bash
# Logs are stored in journald, access via journalctl
# For persistent logs, check:
/var/log/journal/

# Export logs to file
journalctl -u <app-name>.service > /tmp/app-logs.txt
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

