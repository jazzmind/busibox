#!/bin/bash
# Comprehensive health check for all Busibox services
# Execution Context: Proxmox host
# Usage: bash scripts/check-all-services.sh
# Created: 2025-11-06
# Status: Active
# Category: Diagnostics

set -euo pipefail

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROXY_IP="10.96.200.200"
APPS_IP="10.96.200.201"
AGENT_IP="10.96.200.202"
PG_IP="10.96.200.203"
MILVUS_IP="10.96.200.204"
MINIO_IP="10.96.200.205"
INGEST_IP="10.96.200.206"
LITELLM_IP="10.96.200.207"
VLLM_IP="10.96.200.208"

# Counters
TOTAL=0
PASSED=0
FAILED=0
WARNINGS=0

# Test result tracking
declare -a FAILED_TESTS
declare -a WARNING_TESTS

echo "==================================="
echo "Busibox Comprehensive Health Check"
echo "==================================="
echo "$(date)"
echo ""

# Helper function to test HTTP endpoint
test_http() {
    local name=$1
    local url=$2
    local expected_status=${3:-200}
    local timeout=${4:-3}
    
    TOTAL=$((TOTAL + 1))
    echo -n "Testing ${name}... "
    
    local status_code
    status_code=$(curl -sfk -o /dev/null -w "%{http_code}" --max-time "$timeout" "$url" 2>/dev/null || echo "000")
    
    if [[ "$status_code" == "$expected_status" ]] || [[ "$status_code" == "2"* ]]; then
        echo -e "${GREEN}✓${NC} (HTTP $status_code)"
        PASSED=$((PASSED + 1))
        return 0
    else
        echo -e "${RED}✗${NC} (HTTP $status_code, expected $expected_status)"
        FAILED=$((FAILED + 1))
        FAILED_TESTS+=("$name: HTTP $status_code (expected $expected_status)")
        return 1
    fi
}

# Helper function to test TCP port
test_port() {
    local name=$1
    local host=$2
    local port=$3
    local timeout=${4:-3}
    
    TOTAL=$((TOTAL + 1))
    echo -n "Testing ${name} (${host}:${port})... "
    
    if timeout "$timeout" bash -c "cat < /dev/null > /dev/tcp/${host}/${port}" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} (port open)"
        PASSED=$((PASSED + 1))
        return 0
    else
        echo -e "${RED}✗${NC} (port closed/timeout)"
        FAILED=$((FAILED + 1))
        FAILED_TESTS+=("$name: Port ${host}:${port} not accessible")
        return 1
    fi
}

# Helper function to test PostgreSQL
test_postgres() {
    local name=$1
    local host=$2
    local db=$3
    
    TOTAL=$((TOTAL + 1))
    echo -n "Testing ${name} (db: ${db})... "
    
    if ssh root@${host} "PGPASSWORD='0f7806b26ec51d4884ea1fa74cb0e58b4cb6cf396249ce2f95c793554019a833' psql -U busibox_user -h localhost -d ${db} -c 'SELECT 1;' > /dev/null 2>&1"; then
        echo -e "${GREEN}✓${NC} (database accessible)"
        PASSED=$((PASSED + 1))
        return 0
    else
        echo -e "${RED}✗${NC} (connection failed)"
        FAILED=$((FAILED + 1))
        FAILED_TESTS+=("$name: Database '$db' not accessible")
        return 1
    fi
}

# Helper function to test PM2 process
test_pm2() {
    local name=$1
    local host=$2
    local app_name=$3
    
    TOTAL=$((TOTAL + 1))
    echo -n "Testing ${name} PM2 process... "
    
    local status
    status=$(ssh root@${host} "pm2 jlist 2>/dev/null | jq -r '.[] | select(.name==\"${app_name}\") | .pm2_env.status' 2>/dev/null" || echo "not_found")
    
    if [[ "$status" == "online" ]]; then
        echo -e "${GREEN}✓${NC} (online)"
        PASSED=$((PASSED + 1))
        return 0
    elif [[ "$status" == "not_found" ]]; then
        echo -e "${YELLOW}⚠${NC} (not running)"
        WARNINGS=$((WARNINGS + 1))
        WARNING_TESTS+=("$name: PM2 process not found")
        return 1
    else
        echo -e "${RED}✗${NC} (status: $status)"
        FAILED=$((FAILED + 1))
        FAILED_TESTS+=("$name: PM2 process status '$status'")
        return 1
    fi
}

echo -e "${BLUE}=== Infrastructure Services ===${NC}"
echo ""

# PostgreSQL
test_port "PostgreSQL" "$PG_IP" "5432"
test_postgres "PostgreSQL - agent_server" "$PG_IP" "agent_server"
test_postgres "PostgreSQL - ai_portal" "$PG_IP" "ai_portal"
test_postgres "PostgreSQL - agent_client" "$PG_IP" "agent_client"

echo ""

# Milvus
test_port "Milvus" "$MILVUS_IP" "19530"
test_http "Milvus Health" "http://${MILVUS_IP}:9091/healthz" "200" 5

echo ""

# MinIO
test_port "MinIO API" "$MINIO_IP" "9000"
test_port "MinIO Console" "$MINIO_IP" "9001"
test_http "MinIO Health" "http://${MINIO_IP}:9000/minio/health/live" "200"

echo ""

# Redis (on ingest-lxc) - check from inside container since it may bind to localhost only
TOTAL=$((TOTAL + 1))
echo -n "Testing Redis (${INGEST_IP}:6379)... "
if ssh root@${INGEST_IP} "redis-cli ping 2>/dev/null | grep -q PONG"; then
    echo -e "${GREEN}✓${NC} (responding to PING)"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗${NC} (not responding)"
    FAILED=$((FAILED + 1))
    FAILED_TESTS+=("Redis: redis-cli ping failed")
fi

echo ""
echo -e "${BLUE}=== LLM Services ===${NC}"
echo ""

# LiteLLM
test_port "LiteLLM" "$LITELLM_IP" "4000"
test_http "LiteLLM Health" "http://${LITELLM_IP}:4000/health" "200"

# vLLM
test_port "vLLM" "$VLLM_IP" "8000"
test_http "vLLM Health" "http://${VLLM_IP}:8000/health" "200" 5

echo ""
echo -e "${BLUE}=== Application Services ===${NC}"
echo ""

# Agent Server
test_port "agent-server" "$AGENT_IP" "4111"
test_http "agent-server Health" "http://${AGENT_IP}:4111/auth/health" "200"

# Ingest Services
test_port "ingest-api" "$INGEST_IP" "8002"
test_http "ingest-api Health" "http://${INGEST_IP}:8002/health" "200"

# Ingest Worker (systemd service)
TOTAL=$((TOTAL + 1))
echo -n "Testing ingest-worker service... "
if ssh root@${INGEST_IP} "systemctl is-active ingest-worker 2>/dev/null | grep -q active"; then
    echo -e "${GREEN}✓${NC} (active)"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗${NC} (not active)"
    FAILED=$((FAILED + 1))
    FAILED_TESTS+=("ingest-worker: systemd service not active")
fi

echo ""
echo -e "${BLUE}=== Web Applications (PM2) ===${NC}"
echo ""

# Check if jq is available
if ! ssh root@${APPS_IP} "command -v jq >/dev/null 2>&1"; then
    echo -e "${YELLOW}⚠${NC} jq not installed on apps-lxc, skipping PM2 detailed checks"
    WARNINGS=$((WARNINGS + 1))
else
    test_pm2 "ai-portal" "$APPS_IP" "ai-portal"
    test_pm2 "agent-client" "$APPS_IP" "agent-client"
    test_pm2 "doc-intel" "$APPS_IP" "doc-intel"
    test_pm2 "innovation" "$APPS_IP" "innovation"
fi

# HTTP health checks for web apps
test_http "ai-portal" "http://${APPS_IP}:3000/api/health" "200"
test_http "agent-client" "http://${APPS_IP}:3001/api/health" "200"
test_http "doc-intel" "http://${APPS_IP}:3002/api/health" "200" 5
test_http "innovation" "http://${APPS_IP}:3003/api/health" "200" 5

echo ""
echo -e "${BLUE}=== NGINX Proxy Routes ===${NC}"
echo ""

# NGINX routing tests
test_http "NGINX - IP access" "https://${PROXY_IP}" "200"
test_http "NGINX - ai.jaycashman.com" "https://ai.jaycashman.com" "200"
test_http "NGINX - agents subdomain" "https://agents.ai.jaycashman.com" "200"
test_http "NGINX - docs subdomain" "https://docs.ai.jaycashman.com" "200"
test_http "NGINX - innovation subdomain" "https://innovation.ai.jaycashman.com" "200"

echo ""
echo "==================================="
echo "Summary"
echo "==================================="
echo ""
echo "Total Tests: $TOTAL"
echo -e "${GREEN}Passed: $PASSED${NC}"
echo -e "${RED}Failed: $FAILED${NC}"
echo -e "${YELLOW}Warnings: $WARNINGS${NC}"

if [[ ${#FAILED_TESTS[@]} -gt 0 ]]; then
    echo ""
    echo -e "${RED}Failed Tests:${NC}"
    printf '%s\n' "${FAILED_TESTS[@]}" | sed 's/^/  ❌ /'
fi

if [[ ${#WARNING_TESTS[@]} -gt 0 ]]; then
    echo ""
    echo -e "${YELLOW}Warnings:${NC}"
    printf '%s\n' "${WARNING_TESTS[@]}" | sed 's/^/  ⚠️  /'
fi

echo ""
echo "==================================="

# Exit with appropriate code
if [[ $FAILED -gt 0 ]]; then
    echo -e "${RED}Health check FAILED${NC}"
    exit 1
elif [[ $WARNINGS -gt 0 ]]; then
    echo -e "${YELLOW}Health check completed with warnings${NC}"
    exit 0
else
    echo -e "${GREEN}All services healthy!${NC}"
    exit 0
fi

