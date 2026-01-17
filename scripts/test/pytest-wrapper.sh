#!/usr/bin/env bash
#
# Pytest Wrapper with Failure Tracking
#
# EXECUTION CONTEXT: Inside service containers (via SSH)
# PURPOSE: Run pytest and capture failed tests for easy rerun
#
# USAGE:
#   bash pytest-wrapper.sh [pytest-args...]
#
# FEATURES:
# - Captures failed test names
# - Generates pytest filter for rerunning only failed tests
# - Continues to show summary even on failure (doesn't exit immediately)
# - Saves failed tests to /tmp/pytest-failed-tests.txt
#
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Temp file for pytest output
PYTEST_OUTPUT=$(mktemp)
FAILED_TESTS_FILE="/tmp/pytest-failed-tests.txt"

# Cleanup on exit
trap "rm -f $PYTEST_OUTPUT" EXIT

# Run pytest and capture output
echo -e "${BLUE}Running pytest...${NC}"
echo ""

# Run pytest with all arguments, capture exit code
set +e
python -m pytest "$@" 2>&1 | tee "$PYTEST_OUTPUT"
PYTEST_EXIT_CODE=$?
set -e

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "Test Results Summary"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

# Extract test summary
if grep -q "short test summary info" "$PYTEST_OUTPUT"; then
    # Parse failed/error tests from pytest output
    grep -E "^(FAILED|ERROR)" "$PYTEST_OUTPUT" | awk '{print $2}' > "$FAILED_TESTS_FILE" || true
    
    FAILED_COUNT=$(wc -l < "$FAILED_TESTS_FILE" | tr -d ' ')
    
    if [[ "$FAILED_COUNT" -gt 0 ]]; then
        echo -e "${RED}✗ $FAILED_COUNT test(s) failed${NC}"
        echo ""
        echo "Failed tests:"
        cat "$FAILED_TESTS_FILE" | sed 's/^/  - /'
        echo ""
        echo -e "${YELLOW}To rerun only failed tests:${NC}"
        echo ""
        
        # Generate pytest filter
        FAILED_TESTS=$(cat "$FAILED_TESTS_FILE" | tr '\n' ' ')
        echo -e "${BLUE}  pytest $FAILED_TESTS${NC}"
        echo ""
        
        # Also generate -k filter for pattern matching
        if [[ "$FAILED_COUNT" -le 10 ]]; then
            # For small number of failures, show individual test names
            echo "Or using -k filter:"
            FAILED_NAMES=$(cat "$FAILED_TESTS_FILE" | sed 's/.*:://' | tr '\n' ' or ' | sed 's/ or $//')
            echo -e "${BLUE}  pytest -k \"$FAILED_NAMES\"${NC}"
            echo ""
        fi
        
        echo "Failed tests saved to: $FAILED_TESTS_FILE"
    else
        echo -e "${GREEN}✓ All tests passed!${NC}"
        rm -f "$FAILED_TESTS_FILE"
    fi
else
    echo -e "${GREEN}✓ All tests passed!${NC}"
    rm -f "$FAILED_TESTS_FILE"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

# Exit with pytest's exit code
exit $PYTEST_EXIT_CODE
