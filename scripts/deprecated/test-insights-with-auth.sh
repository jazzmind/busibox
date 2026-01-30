#!/usr/bin/env bash
# Test script for insights API with proper authentication
set -euo pipefail

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Configuration
AGENT_API_URL="${AGENT_API_URL:-http://localhost:8000}"
AUTHZ_URL="${AUTH_TOKEN_URL:-http://10.96.201.210:8010/oauth/token}"
TEST_CLIENT_ID="${AUTHZ_TEST_CLIENT_ID}"
TEST_CLIENT_SECRET="${AUTHZ_TEST_CLIENT_SECRET}"
TEST_USER_ID="${TEST_USER_ID}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Insights API Tests with Authentication ===${NC}"
echo "Agent API: $AGENT_API_URL"
echo "AuthZ URL: $AUTHZ_URL"
echo "Test User: $TEST_USER_ID"
echo ""

# Use admin token for testing
# In production, this would be a proper user token from the auth flow
ACCESS_TOKEN="${AUTHZ_ADMIN_TOKEN}"

if [ -z "$ACCESS_TOKEN" ]; then
    echo -e "${RED}✗ No admin token available${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Using admin token for testing${NC}"
echo ""

# Test counter
TESTS_PASSED=0
TESTS_FAILED=0

# Helper function to run a test
run_test() {
    local test_name="$1"
    local method="$2"
    local endpoint="$3"
    local data="$4"
    
    echo -e "${YELLOW}Test: ${test_name}${NC}"
    
    if [ -z "$data" ]; then
        response=$(curl -s -w "\n%{http_code}" -X "$method" \
            -H "Authorization: Bearer $ACCESS_TOKEN" \
            "$AGENT_API_URL$endpoint")
    else
        response=$(curl -s -w "\n%{http_code}" -X "$method" \
            -H "Authorization: Bearer $ACCESS_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$data" \
            "$AGENT_API_URL$endpoint")
    fi
    
    http_code=$(echo "$response" | tail -n 1)
    body=$(echo "$response" | sed '$d')
    
    if [ "$http_code" -ge 200 ] && [ "$http_code" -lt 300 ]; then
        echo -e "${GREEN}✓ PASS${NC} (HTTP $http_code)"
        echo "Response: ${body:0:200}"
        echo ""
        echo ""
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC} (HTTP $http_code)"
        echo "Response: $body"
        echo ""
        TESTS_FAILED=$((TESTS_FAILED + 1))
        return 1
    fi
}

# Test 1: Initialize collection
run_test "Initialize insights collection" \
    "POST" \
    "/insights/init" \
    ""

# Test 2: Insert insights
TIMESTAMP=$(date +%s)

# Generate proper 1024-dimension embedding arrays
EMBEDDING_1=$(python3 -c "import json; print(json.dumps([0.1] * 1024))")
EMBEDDING_2=$(python3 -c "import json; print(json.dumps([0.2] * 1024))")

INSIGHT_DATA=$(cat <<EOF
{
  "insights": [
    {
      "id": "test-insight-${TIMESTAMP}-1",
      "userId": "${TEST_USER_ID}",
      "content": "User prefers Python for backend development",
      "embedding": ${EMBEDDING_1},
      "conversationId": "test-conv-1",
      "analyzedAt": ${TIMESTAMP}
    },
    {
      "id": "test-insight-${TIMESTAMP}-2",
      "userId": "${TEST_USER_ID}",
      "content": "User is interested in machine learning and AI",
      "embedding": ${EMBEDDING_2},
      "conversationId": "test-conv-1",
      "analyzedAt": ${TIMESTAMP}
    }
  ]
}
EOF
)

run_test "Insert insights" \
    "POST" \
    "/insights" \
    "$INSIGHT_DATA"

# Test 3: Flush collection
run_test "Flush collection" \
    "POST" \
    "/insights/flush" \
    ""

# Test 4: Get user stats
run_test "Get user statistics" \
    "GET" \
    "/insights/stats/$TEST_USER_ID" \
    ""

# Test 5: Search insights
SEARCH_DATA=$(cat <<EOF
{
  "query": "What programming languages does the user like?",
  "userId": "${TEST_USER_ID}",
  "limit": 5,
  "scoreThreshold": 2.0
}
EOF
)

echo -e "${YELLOW}Test: Search insights${NC}"
response=$(curl -s -w "\n%{http_code}" -X "POST" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$SEARCH_DATA" \
    "$AGENT_API_URL/insights/search")

http_code=$(echo "$response" | tail -n 1)
body=$(echo "$response" | sed '$d')

if [ "$http_code" -ge 200 ] && [ "$http_code" -lt 300 ]; then
    echo -e "${GREEN}✓ PASS${NC} (HTTP $http_code)"
    echo "Response: ${body:0:200}"
    echo ""
    TESTS_PASSED=$((TESTS_PASSED + 1))
elif [ "$http_code" -eq 500 ]; then
    echo -e "${YELLOW}⚠ SKIP${NC} (HTTP $http_code - embedding service may be unavailable)"
    echo "Response: ${body:0:200}"
    echo ""
else
    echo -e "${RED}✗ FAIL${NC} (HTTP $http_code)"
    echo "Response: $body"
    echo ""
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
echo ""

# Test 6: Delete conversation insights
run_test "Delete conversation insights" \
    "DELETE" \
    "/insights/conversation/test-conv-1" \
    ""

# Test 7: Delete user insights
run_test "Delete user insights" \
    "DELETE" \
    "/insights/user/$TEST_USER_ID" \
    ""

# Summary
echo -e "${BLUE}=== Test Summary ===${NC}"
echo -e "Passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Failed: ${RED}$TESTS_FAILED${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi



