#!/bin/bash
# Check PM2 application status on apps-lxc
# Execution Context: Proxmox host
# Usage: bash scripts/check-pm2-apps.sh
# Created: 2025-11-06
# Status: Active
# Category: Diagnostics

set -euo pipefail

APPS_IP="10.96.200.201"

echo "==================================="
echo "PM2 Applications Status"
echo "==================================="
echo ""

echo "=== PM2 Process List ==="
ssh root@${APPS_IP} "pm2 list"
echo ""

echo "=== Check which ports are listening ==="
ssh root@${APPS_IP} "netstat -tuln | grep LISTEN | grep -E ':(3000|3001|3002|3003)'"
echo ""

# Check each expected app
for app in ai-portal agent-client doc-intel innovation; do
    echo "=== Logs for: $app ==="
    ssh root@${APPS_IP} "pm2 logs $app --lines 20 --nostream 2>/dev/null || echo 'No logs for $app'"
    echo ""
done

echo "==================================="

