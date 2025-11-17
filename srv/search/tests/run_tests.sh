#!/usr/bin/env bash
#
# Run Search API Tests
#
# Description:
#   Comprehensive test runner for the Search API.
#   Runs unit tests, integration tests, and generates coverage report.
#
# Execution Context: Development/CI environment
# Dependencies: pytest, pytest-asyncio, pytest-cov
#
# Usage:
#   bash tests/run_tests.sh [options]
#
# Options:
#   --unit           Run only unit tests
#   --integration    Run only integration tests
#   --coverage       Generate coverage report
#   --verbose        Verbose output
#   --fast           Skip slow tests

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=========================================="
echo "Search API Test Runner"
echo "=========================================="
echo ""

# Change to project directory
cd "$PROJECT_DIR"

# Check if venv exists
if [ ! -d "venv" ]; then
    echo -e "${RED}Error: Virtual environment not found${NC}"
    echo "Please create a venv first: python3 -m venv venv"
    exit 1
fi

# Activate venv
echo "Activating virtual environment..."
source venv/bin/activate

# Install test dependencies if needed
echo "Checking test dependencies..."
pip install -q pytest pytest-asyncio pytest-cov pytest-mock 2>/dev/null || true

# Parse arguments
RUN_UNIT=false
RUN_INTEGRATION=false
RUN_COVERAGE=false
VERBOSE=false
SKIP_SLOW=false

if [ $# -eq 0 ]; then
    # No arguments, run all tests
    RUN_UNIT=true
    RUN_INTEGRATION=true
else
    while [[ $# -gt 0 ]]; do
        case $1 in
            --unit)
                RUN_UNIT=true
                shift
                ;;
            --integration)
                RUN_INTEGRATION=true
                shift
                ;;
            --coverage)
                RUN_COVERAGE=true
                shift
                ;;
            --verbose)
                VERBOSE=true
                shift
                ;;
            --fast)
                SKIP_SLOW=true
                shift
                ;;
            *)
                echo "Unknown option: $1"
                echo "Usage: $0 [--unit] [--integration] [--coverage] [--verbose] [--fast]"
                exit 1
                ;;
        esac
    done
fi

# Build pytest command
PYTEST_ARGS="-v"

if [ "$VERBOSE" = true ]; then
    PYTEST_ARGS="$PYTEST_ARGS -vv"
fi

if [ "$SKIP_SLOW" = true ]; then
    PYTEST_ARGS="$PYTEST_ARGS -m 'not slow'"
fi

if [ "$RUN_COVERAGE" = true ]; then
    PYTEST_ARGS="$PYTEST_ARGS --cov=src --cov-report=html --cov-report=term-missing"
fi

# Track test results
UNIT_PASSED=false
INTEGRATION_PASSED=false

# Run unit tests
if [ "$RUN_UNIT" = true ]; then
    echo ""
    echo "=========================================="
    echo "Running Unit Tests"
    echo "=========================================="
    echo ""
    
    if pytest $PYTEST_ARGS -m unit tests/unit/; then
        UNIT_PASSED=true
        echo -e "${GREEN}✓ Unit tests passed${NC}"
    else
        echo -e "${RED}✗ Unit tests failed${NC}"
    fi
fi

# Run integration tests
if [ "$RUN_INTEGRATION" = true ]; then
    echo ""
    echo "=========================================="
    echo "Running Integration Tests"
    echo "=========================================="
    echo ""
    
    if pytest $PYTEST_ARGS -m integration tests/integration/; then
        INTEGRATION_PASSED=true
        echo -e "${GREEN}✓ Integration tests passed${NC}"
    else
        echo -e "${RED}✗ Integration tests failed${NC}"
    fi
fi

# Summary
echo ""
echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo ""

if [ "$RUN_UNIT" = true ]; then
    if [ "$UNIT_PASSED" = true ]; then
        echo -e "Unit Tests:        ${GREEN}PASSED${NC}"
    else
        echo -e "Unit Tests:        ${RED}FAILED${NC}"
    fi
fi

if [ "$RUN_INTEGRATION" = true ]; then
    if [ "$INTEGRATION_PASSED" = true ]; then
        echo -e "Integration Tests: ${GREEN}PASSED${NC}"
    else
        echo -e "Integration Tests: ${RED}FAILED${NC}"
    fi
fi

if [ "$RUN_COVERAGE" = true ]; then
    echo ""
    echo "Coverage report generated: htmlcov/index.html"
fi

echo ""

# Exit with appropriate code
if [ "$RUN_UNIT" = true ] && [ "$UNIT_PASSED" = false ]; then
    exit 1
fi

if [ "$RUN_INTEGRATION" = true ] && [ "$INTEGRATION_PASSED" = false ]; then
    exit 1
fi

exit 0

