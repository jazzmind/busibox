#!/bin/bash
set -euo pipefail

#==============================================================================
# Test Signal Bot Integration
#
# EXECUTION CONTEXT: 
#   - Agent container (where signal-bot runs)
#   - Or any machine with access to signal-cli-rest-api
#
# DESCRIPTION:
#   Tests the Signal bot integration:
#   - Check signal-cli-rest-api health
#   - Check Signal registration status
#   - Check Agent API connectivity
#   - Send a test message (optional)
#
# USAGE:
#   bash scripts/test/test-signal-bot.sh [test|production]
#
# OPTIONS:
#   --send-test    Send a test message to verify full integration
#==============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
ENV="${1:-test}"
SEND_TEST=false

for arg in "$@"; do
    case "$arg" in
        --send-test)
            SEND_TEST=true
            ;;
    esac
done

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Signal Bot Integration Tests${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Environment-specific configuration
if [ "$ENV" = "test" ]; then
    AGENT_HOST="10.96.201.202"
    SIGNAL_CLI_PORT=8080
    AGENT_API_PORT=8000
    echo -e "${GREEN}Environment: TEST${NC}"
elif [ "$ENV" = "production" ]; then
    AGENT_HOST="10.96.200.202"
    SIGNAL_CLI_PORT=8080
    AGENT_API_PORT=8000
    echo -e "${YELLOW}Environment: PRODUCTION${NC}"
else
    # Running locally on agent container
    AGENT_HOST="localhost"
    SIGNAL_CLI_PORT=8080
    AGENT_API_PORT=8000
    echo -e "${BLUE}Environment: LOCAL (on agent container)${NC}"
fi

SIGNAL_CLI_URL="http://${AGENT_HOST}:${SIGNAL_CLI_PORT}"
AGENT_API_URL="http://${AGENT_HOST}:${AGENT_API_PORT}"

TESTS_PASSED=0
TESTS_FAILED=0

#==============================================================================
# Test Functions
#==============================================================================

test_pass() {
    echo -e "${GREEN}✓ $1${NC}"
    ((TESTS_PASSED++))
}

test_fail() {
    echo -e "${RED}✗ $1${NC}"
    ((TESTS_FAILED++))
}

test_warn() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

#==============================================================================
# Test 1: Signal CLI REST API Health
#==============================================================================

echo ""
echo -e "${BLUE}Test 1: Signal CLI REST API Health${NC}"

if curl -s -f "${SIGNAL_CLI_URL}/v1/about" > /dev/null 2>&1; then
    test_pass "Signal CLI REST API is running"
    
    # Get more details
    ABOUT=$(curl -s "${SIGNAL_CLI_URL}/v1/about")
    echo -e "  Response: $(echo "$ABOUT" | head -c 100)..."
else
    test_fail "Signal CLI REST API not responding at ${SIGNAL_CLI_URL}"
fi

#==============================================================================
# Test 2: Signal Registration Status
#==============================================================================

echo ""
echo -e "${BLUE}Test 2: Signal Registration Status${NC}"

ACCOUNTS=$(curl -s "${SIGNAL_CLI_URL}/v1/about" 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('accounts', [])))" 2>/dev/null || echo "0")

if [ "$ACCOUNTS" -gt 0 ]; then
    test_pass "Signal account registered (${ACCOUNTS} account(s))"
else
    test_fail "No Signal accounts registered"
    echo -e "  ${YELLOW}Run: register-signal register +1234567890${NC}"
fi

#==============================================================================
# Test 3: Agent API Health
#==============================================================================

echo ""
echo -e "${BLUE}Test 3: Agent API Health${NC}"

if curl -s -f "${AGENT_API_URL}/health" > /dev/null 2>&1; then
    test_pass "Agent API is running"
else
    test_fail "Agent API not responding at ${AGENT_API_URL}"
fi

#==============================================================================
# Test 4: Signal Bot Service Status
#==============================================================================

echo ""
echo -e "${BLUE}Test 4: Signal Bot Service Status${NC}"

if [ "$AGENT_HOST" = "localhost" ]; then
    # Running on agent container - check systemd
    if systemctl is-active --quiet signal-bot 2>/dev/null; then
        test_pass "Signal bot service is running"
    else
        STATUS=$(systemctl is-active signal-bot 2>/dev/null || echo "not found")
        test_fail "Signal bot service is ${STATUS}"
    fi
else
    # Remote check - try to SSH
    test_warn "Skipping service check (run on agent container)"
fi

#==============================================================================
# Test 5: Docker Container Status
#==============================================================================

echo ""
echo -e "${BLUE}Test 5: Docker Container (signal-cli-rest-api)${NC}"

if [ "$AGENT_HOST" = "localhost" ]; then
    if docker ps | grep -q signal-cli-rest-api 2>/dev/null; then
        test_pass "signal-cli-rest-api container is running"
    else
        test_fail "signal-cli-rest-api container not running"
        echo -e "  ${YELLOW}Check: docker ps -a${NC}"
    fi
else
    test_warn "Skipping Docker check (run on agent container)"
fi

#==============================================================================
# Test 6: Send Test Message (Optional)
#==============================================================================

if [ "$SEND_TEST" = true ]; then
    echo ""
    echo -e "${BLUE}Test 6: Send Test Message${NC}"
    
    # Get registered phone number
    PHONE=$(curl -s "${SIGNAL_CLI_URL}/v1/about" 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); accounts=data.get('accounts', []); print(accounts[0] if accounts else '')" 2>/dev/null || echo "")
    
    if [ -n "$PHONE" ]; then
        echo -e "  Registered phone: ${PHONE}"
        echo -e "  ${YELLOW}To send a test message, run:${NC}"
        echo -e "  curl -X POST '${SIGNAL_CLI_URL}/v2/send' \\"
        echo -e "    -H 'Content-Type: application/json' \\"
        echo -e "    -d '{\"number\": \"${PHONE}\", \"recipients\": [\"YOUR_PHONE\"], \"message\": \"Test from Signal bot!\"}'"
        test_warn "Manual test message step (not automated)"
    else
        test_fail "No phone number registered for sending test"
    fi
fi

#==============================================================================
# Summary
#==============================================================================

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}Passed: ${TESTS_PASSED}${NC}"
echo -e "${RED}Failed: ${TESTS_FAILED}${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed. Check the output above.${NC}"
    exit 1
fi
