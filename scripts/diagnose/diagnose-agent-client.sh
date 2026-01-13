#!/bin/bash
# Agent Manager Diagnostic Script
# Execution Context: Proxmox host
# Usage: bash scripts/diagnose/diagnose-agent-client.sh
# Created: 2025-11-06
# Status: Active
# Category: Diagnostics

set -euo pipefail

APPS_IP="10.96.200.201"
AGENT_CLIENT_PATH="/srv/apps/agent-client"
FAILED_PATH="/srv/apps/agent-client.failed.20251106-205132"

echo "==================================="
echo "Agent Manager Diagnostic"
echo "==================================="
echo ""

echo "=== Current Deployment Directory ==="
ssh root@${APPS_IP} "ls -la ${AGENT_CLIENT_PATH}/ 2>/dev/null || echo 'Directory empty or missing'"
echo ""

echo "=== Failed Deployment Directory ==="
ssh root@${APPS_IP} "ls -la ${FAILED_PATH}/ 2>/dev/null || echo 'Failed deployment not found'"
echo ""

echo "=== Systemd Service Status ==="
ssh root@${APPS_IP} "systemctl status agent-client.service"
echo ""

echo "=== Service Logs (last 50 lines) ==="
ssh root@${APPS_IP} "journalctl -u agent-client.service -n 50 --no-pager 2>/dev/null || echo 'No logs found'"
echo ""

echo "=== Check if port 3001 is listening ==="
ssh root@${APPS_IP} "netstat -tuln | grep :3001 || echo 'Nothing listening on port 3001'"
echo ""

echo "=== Try manual health check ==="
ssh root@${APPS_IP} "curl -s http://localhost:3001/api/health 2>/dev/null || echo 'Health endpoint not responding'"
echo ""

echo "=== Check .env file ==="
ssh root@${APPS_IP} "head -20 ${AGENT_CLIENT_PATH}/.env 2>/dev/null || echo '.env not found'"
echo ""

echo "=== Check package.json ==="
ssh root@${APPS_IP} "cat ${AGENT_CLIENT_PATH}/package.json 2>/dev/null | jq '.scripts' || echo 'package.json not found'"
echo ""

echo "=== Check if standalone server exists ==="
ssh root@${APPS_IP} "ls -lh ${AGENT_CLIENT_PATH}/.next/standalone/server.js 2>/dev/null || echo 'Standalone server not found'"
echo ""

echo "=== Check Next.js output mode ==="
ssh root@${APPS_IP} "cat ${AGENT_CLIENT_PATH}/next.config.mjs 2>/dev/null | grep -A2 output || echo 'next.config not found'"
echo ""

echo "==================================="
echo "Diagnostic complete"
echo "==================================="

