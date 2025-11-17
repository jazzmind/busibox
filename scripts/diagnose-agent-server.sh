#!/bin/bash
# Diagnose agent-server deployment issues
# Created: $(date -u +"%Y-%m-%d")
# Status: Active
# Category: Diagnostics

set -euo pipefail

AGENT_IP="10.96.200.202"
AGENT_PORT="4111"
DEPLOY_PATH="/srv/agent"

echo "==================================="
echo "Agent Server Diagnostic"
echo "==================================="
echo ""

echo "=== Checking Container Accessibility ==="
if ping -c 1 -W 2 ${AGENT_IP} > /dev/null 2>&1; then
    echo "✓ agent-lxc (${AGENT_IP}) is reachable"
else
    echo "✗ agent-lxc (${AGENT_IP}) is NOT reachable"
    exit 1
fi

echo ""
echo "=== Checking Deployment Directory ==="
ssh root@${AGENT_IP} "ls -la ${DEPLOY_PATH}" || {
    echo "✗ Deploy path ${DEPLOY_PATH} does not exist or is not accessible"
    exit 1
}

echo ""
echo "=== Checking .env File ==="
ssh root@${AGENT_IP} "test -f ${DEPLOY_PATH}/.env && echo '✓ .env exists' || echo '✗ .env missing'"
ssh root@${AGENT_IP} "test -f ${DEPLOY_PATH}/.env && wc -l ${DEPLOY_PATH}/.env"

echo ""
echo "=== Checking Node.js/Python Files ==="
ssh root@${AGENT_IP} "cd ${DEPLOY_PATH} && { \
    test -f package.json && echo '✓ package.json found'; \
    test -f main.py && echo '✓ main.py found'; \
    test -f app.py && echo '✓ app.py found'; \
    test -f server.js && echo '✓ server.js found'; \
    test -f index.js && echo '✓ index.js found'; \
    true; \
}"

echo ""
echo "=== Checking PM2 Process ==="
ssh root@${AGENT_IP} "pm2 list" || echo "✗ PM2 not installed or not running"

echo ""
echo "=== Checking if Port ${AGENT_PORT} is Listening ==="
ssh root@${AGENT_IP} "netstat -tulpn | grep :${AGENT_PORT} || echo '✗ Nothing listening on port ${AGENT_PORT}'"

echo ""
echo "=== Checking Deploywatch Script ==="
ssh root@${AGENT_IP} "test -f /srv/deploywatch/apps/agent-server.sh && echo '✓ Deploywatch script exists' || echo '✗ Deploywatch script missing'"

echo ""
echo "=== Recent Deployment Logs (if any) ==="
ssh root@${AGENT_IP} "test -d ${DEPLOY_PATH}/logs && ls -lt ${DEPLOY_PATH}/logs/ | head -5 || echo 'No logs directory'"

echo ""
echo "=== PM2 Logs for agent-server (last 20 lines) ==="
ssh root@${AGENT_IP} "pm2 logs agent-server --lines 20 --nostream 2>&1 || echo 'No PM2 logs for agent-server'"

echo ""
echo "=== Try Running Deploywatch Script Manually ==="
echo "Run this command on the Proxmox host:"
echo "  ssh root@${AGENT_IP} 'bash /srv/deploywatch/apps/agent-server.sh'"
echo ""

echo "=== Check Package.json Scripts (if exists) ==="
ssh root@${AGENT_IP} "test -f ${DEPLOY_PATH}/package.json && jq -r '.scripts' ${DEPLOY_PATH}/package.json || echo 'No package.json or jq not installed'"

echo ""
echo "==================================="
echo "Diagnostic complete"
echo "==================================="

