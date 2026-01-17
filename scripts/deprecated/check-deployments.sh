#!/bin/bash
# Check deployment status of all applications
# Created: $(date -u +"%Y-%m-%d")
# Status: Active
# Category: Diagnostics

set -euo pipefail

echo "=================================="
echo "Busibox Application Status Check"
echo "=================================="
echo ""

# Configuration
PROXY_IP="10.96.200.200"
APPS_IP="10.96.200.201"
AGENT_IP="10.96.200.202"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

check_service() {
    local name=$1
    local ip=$2
    local port=$3
    local health_path=$4
    
    echo -n "Checking ${name} (${ip}:${port})... "
    
    if timeout 3 curl -sf "http://${ip}:${port}${health_path}" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Running${NC}"
        return 0
    else
        echo -e "${RED}✗ Not responding${NC}"
        return 1
    fi
}

check_nginx() {
    local url=$1
    local name=$2
    
    echo -n "Checking NGINX route ${name} (${url})... "
    
    if timeout 3 curl -sfk "${url}" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Routed${NC}"
        return 0
    else
        echo -e "${RED}✗ Not routed${NC}"
        return 1
    fi
}

echo "=== Direct Service Checks ==="
check_service "agent-api" "${AGENT_IP}" "8000" "/auth/health"
check_service "ai-portal" "${APPS_IP}" "3000" "/api/health"
check_service "agent-client" "${APPS_IP}" "3001" "/api/health"
check_service "doc-intel" "${APPS_IP}" "3002" "/api/health"
check_service "innovation" "${APPS_IP}" "3003" "/api/health"

echo ""
echo "=== NGINX Proxy Routes ==="
check_nginx "https://${PROXY_IP}" "ai-portal (IP access)"
check_nginx "https://ai.jaycashman.com" "ai-portal (domain)"
check_nginx "https://agents.ai.jaycashman.com" "agent-client"
check_nginx "https://docs.ai.jaycashman.com" "doc-intel"
check_nginx "https://innovation.ai.jaycashman.com" "innovation"

echo ""
echo "=== NGINX Configuration ==="
echo "Checking enabled sites on proxy..."
ssh root@${PROXY_IP} "ls -la /etc/nginx/sites-enabled/ | grep -v 'total\|^\.$\|^\.\.'" || echo "Could not list NGINX sites"

echo ""
echo "=== Systemd Services on apps-lxc ==="
ssh root@${APPS_IP} "systemctl list-units --type=service --state=running | grep -E '(ai-portal|agent-client|doc-intel|innovation)'" || echo "Could not get service status"

echo ""
echo "=== Service Logs (last 10 lines) ==="
echo ""
echo "--- agent-server ---"
ssh root@${AGENT_IP} "journalctl -u agent-server -n 10 --no-pager" 2>/dev/null || echo "No logs or service not found"

echo ""
echo "--- ai-portal ---"
ssh root@${APPS_IP} "journalctl -u ai-portal -n 10 --no-pager" 2>/dev/null || echo "No logs or service not found"

echo ""
echo "==================================="
echo "Check complete"
echo "==================================="

