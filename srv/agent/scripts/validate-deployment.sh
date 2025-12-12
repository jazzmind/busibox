#!/bin/bash
#
# Deployment Validation Script for Agent-Server API Enhancements
#
# Usage: bash scripts/validate-deployment.sh <base-url> <auth-token>
# Example: bash scripts/validate-deployment.sh http://localhost:8000 "Bearer eyJ..."
#
# Exit codes:
#   0 - All tests passed
#   1 - One or more tests failed
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
BASE_URL="${1:-http://localhost:8000}"
AUTH_TOKEN="${2:-}"

if [ -z "$AUTH_TOKEN" ]; then
    echo -e "${RED}Error: AUTH_TOKEN required${NC}"
    echo "Usage: $0 <base-url> <auth-token>"
    exit 1
fi

# Test counters
PASSED=0
FAILED=0

# Helper functions
test_endpoint() {
    local name="$1"
    local method="$2"
    local endpoint="$3"
    local expected_status="$4"
    local data="${5:-}"
    
    echo -n "Testing $name... "
    
    if [ -n "$data" ]; then
        response=$(curl -s -w "\n%{http_code}" -X "$method" \
            "$BASE_URL$endpoint" \
            -H "Authorization: $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$data")
    else
        response=$(curl -s -w "\n%{http_code}" -X "$method" \
            "$BASE_URL$endpoint" \
            -H "Authorization: $AUTH_TOKEN")
    fi
    
    status_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | sed '$d')
    
    if [ "$status_code" = "$expected_status" ]; then
        echo -e "${GREEN}✓ PASS${NC} (HTTP $status_code)"
        ((PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC} (Expected HTTP $expected_status, got $status_code)"
        echo "  Response: $body"
        ((FAILED++))
        return 1
    fi
}

echo "========================================="
echo "Agent-Server Deployment Validation"
echo "========================================="
echo "Base URL: $BASE_URL"
echo ""

# Test 1: Health Check
echo "1. Health Check"
test_endpoint "Health endpoint" "GET" "/health" "200"
echo ""

# Test 2: List Agents (Personal Filtering)
echo "2. Personal Agent Management"
test_endpoint "List agents" "GET" "/agents" "200"
echo ""

# Test 3: Dispatcher Routing
echo "3. Intelligent Query Routing"
test_endpoint "Route document query" "POST" "/dispatcher/route" "200" '{
  "query": "What does our Q4 report say?",
  "available_tools": ["doc_search", "web_search"],
  "available_agents": [],
  "user_settings": {
    "enabled_tools": ["doc_search", "web_search"],
    "enabled_agents": []
  }
}'

test_endpoint "Route web query" "POST" "/dispatcher/route" "200" '{
  "query": "What is the weather today?",
  "available_tools": ["doc_search", "web_search"],
  "available_agents": [],
  "user_settings": {
    "enabled_tools": ["doc_search", "web_search"],
    "enabled_agents": []
  }
}'

test_endpoint "Route with no tools" "POST" "/dispatcher/route" "200" '{
  "query": "Help me",
  "available_tools": [],
  "available_agents": [],
  "user_settings": {
    "enabled_tools": [],
    "enabled_agents": []
  }
}'
echo ""

# Test 4: Tool CRUD
echo "4. Tool CRUD Operations"
test_endpoint "List tools" "GET" "/agents/tools" "200"
test_endpoint "List workflows" "GET" "/agents/workflows" "200"
test_endpoint "List evaluators" "GET" "/agents/evals" "200"
echo ""

# Test 5: Create Personal Agent
echo "5. Create Personal Agent"
create_response=$(curl -s -w "\n%{http_code}" -X POST \
    "$BASE_URL/agents/definitions" \
    -H "Authorization: $AUTH_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
        "name": "test-validation-agent-'$(date +%s)'",
        "display_name": "Test Validation Agent",
        "model": "anthropic:claude-3-5-sonnet",
        "instructions": "Test agent for validation",
        "tools": {"names": []},
        "scopes": []
    }')

create_status=$(echo "$create_response" | tail -n1)
create_body=$(echo "$create_response" | sed '$d')

if [ "$create_status" = "201" ]; then
    echo -e "${GREEN}✓ PASS${NC} Create personal agent (HTTP 201)"
    ((PASSED++))
    
    # Extract agent ID
    agent_id=$(echo "$create_body" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
    
    if [ -n "$agent_id" ]; then
        # Test 6: Get Agent
        echo -n "Testing Get agent by ID... "
        get_response=$(curl -s -w "\n%{http_code}" -X GET \
            "$BASE_URL/agents/$agent_id" \
            -H "Authorization: $AUTH_TOKEN")
        get_status=$(echo "$get_response" | tail -n1)
        
        if [ "$get_status" = "200" ]; then
            echo -e "${GREEN}✓ PASS${NC} (HTTP 200)"
            ((PASSED++))
        else
            echo -e "${RED}✗ FAIL${NC} (Expected HTTP 200, got $get_status)"
            ((FAILED++))
        fi
    fi
else
    echo -e "${RED}✗ FAIL${NC} Create personal agent (Expected HTTP 201, got $create_status)"
    echo "  Response: $create_body"
    ((FAILED++))
fi
echo ""

# Test 7: Database Checks
echo "6. Database Schema Validation"
echo -n "Checking database schema... "

# Try to connect to database (requires psql and connection info)
if command -v psql &> /dev/null; then
    # Check if we can connect
    if psql -U busibox_user -d busibox -c "SELECT 1" &> /dev/null; then
        # Check for new columns
        has_is_builtin=$(psql -U busibox_user -d busibox -tAc \
            "SELECT COUNT(*) FROM information_schema.columns 
             WHERE table_name='agent_definitions' AND column_name='is_builtin'")
        
        has_dispatcher_log=$(psql -U busibox_user -d busibox -tAc \
            "SELECT COUNT(*) FROM information_schema.tables 
             WHERE table_name='dispatcher_decision_log'")
        
        if [ "$has_is_builtin" = "1" ] && [ "$has_dispatcher_log" = "1" ]; then
            echo -e "${GREEN}✓ PASS${NC} (Schema updated)"
            ((PASSED++))
        else
            echo -e "${RED}✗ FAIL${NC} (Schema not updated)"
            echo "  is_builtin column: $has_is_builtin"
            echo "  dispatcher_decision_log table: $has_dispatcher_log"
            ((FAILED++))
        fi
    else
        echo -e "${YELLOW}⊘ SKIP${NC} (Cannot connect to database)"
    fi
else
    echo -e "${YELLOW}⊘ SKIP${NC} (psql not available)"
fi
echo ""

# Summary
echo "========================================="
echo "Validation Summary"
echo "========================================="
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
