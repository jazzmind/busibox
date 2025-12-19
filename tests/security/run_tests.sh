#!/usr/bin/env bash
#
# Security Test Runner
#
# EXECUTION CONTEXT: Admin workstation or local development
# PURPOSE: Run security tests against Busibox API endpoints
#
# USAGE:
#   ./run_tests.sh [options]
#   ./run_tests.sh --env=test
#   ./run_tests.sh --env=local --marker=auth
#   ./run_tests.sh --help
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Default values
ENV="test"
MARKER=""
VERBOSE=""
COVERAGE=""
PARALLEL=""
EXTRA_ARGS=""

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_usage() {
    echo ""
    echo "Busibox Security Test Runner"
    echo ""
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --env=ENV        Target environment (local|test|production) [default: test]"
    echo "  --marker=MARKER  Run only tests with specific marker (auth|injection|fuzz|rate_limit|idor)"
    echo "  --slow           Include slow tests"
    echo "  --coverage       Generate coverage report"
    echo "  --parallel       Run tests in parallel"
    echo "  -v, --verbose    Verbose output"
    echo "  -h, --help       Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                           # Run all security tests against test env"
    echo "  $0 --env=local               # Run against local development"
    echo "  $0 --marker=auth             # Run only authentication tests"
    echo "  $0 --marker=injection        # Run only injection tests"
    echo "  $0 --marker=fuzz --slow      # Run fuzzing tests including slow ones"
    echo ""
    echo "Environment Variables:"
    echo "  SECURITY_TEST_ENV            Override target environment"
    echo "  TEST_JWT_TOKEN               Valid JWT token for authenticated tests"
    echo "  AUTHZ_ADMIN_TOKEN            Admin token for admin endpoint tests"
    echo "  TEST_CLIENT_ID               OAuth client ID"
    echo "  TEST_CLIENT_SECRET           OAuth client secret"
    echo ""
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --env=*)
            ENV="${1#*=}"
            shift
            ;;
        --marker=*)
            MARKER="${1#*=}"
            shift
            ;;
        --slow)
            EXTRA_ARGS="$EXTRA_ARGS --runslow"
            shift
            ;;
        --coverage)
            COVERAGE="true"
            shift
            ;;
        --parallel)
            PARALLEL="true"
            shift
            ;;
        -v|--verbose)
            VERBOSE="-vv"
            shift
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            print_usage
            exit 1
            ;;
    esac
done

# Validate environment
if [[ ! "$ENV" =~ ^(local|test|production)$ ]]; then
    echo -e "${RED}Invalid environment: $ENV${NC}"
    echo "Valid options: local, test, production"
    exit 1
fi

echo -e "${BLUE}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                    Busibox Security Tests                            ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Change to security tests directory
cd "$SCRIPT_DIR"

# Check if venv exists, create if not
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install dependencies
echo -e "${BLUE}Installing dependencies...${NC}"
pip install -q -r requirements.txt

# Set environment
export SECURITY_TEST_ENV="$ENV"

echo ""
echo -e "${GREEN}Target Environment: $ENV${NC}"

# Show endpoint info based on environment
case $ENV in
    local)
        echo "  Agent API:  http://localhost:8000"
        echo "  Ingest API: http://localhost:8002"
        echo "  Search API: http://localhost:8003"
        echo "  Authz API:  http://localhost:8010"
        ;;
    test)
        echo "  Agent API:  http://10.96.201.202:8000"
        echo "  Ingest API: http://10.96.201.206:8002"
        echo "  Search API: http://10.96.201.204:8003"
        echo "  Authz API:  http://10.96.201.210:8010"
        ;;
    production)
        echo "  Agent API:  http://10.96.200.202:8000"
        echo "  Ingest API: http://10.96.200.206:8002"
        echo "  Search API: http://10.96.200.204:8003"
        echo "  Authz API:  http://10.96.200.210:8010"
        ;;
esac

echo ""

# Build pytest command
PYTEST_CMD="python -m pytest"

# Add verbose flag
if [ -n "$VERBOSE" ]; then
    PYTEST_CMD="$PYTEST_CMD $VERBOSE"
else
    PYTEST_CMD="$PYTEST_CMD -v"
fi

# Add marker filter
if [ -n "$MARKER" ]; then
    PYTEST_CMD="$PYTEST_CMD -m $MARKER"
    echo -e "${YELLOW}Running tests with marker: $MARKER${NC}"
fi

# Add coverage
if [ -n "$COVERAGE" ]; then
    PYTEST_CMD="$PYTEST_CMD --cov=. --cov-report=html --cov-report=term"
fi

# Add parallel execution
if [ -n "$PARALLEL" ]; then
    PYTEST_CMD="$PYTEST_CMD -n auto"
fi

# Add extra args
if [ -n "$EXTRA_ARGS" ]; then
    PYTEST_CMD="$PYTEST_CMD $EXTRA_ARGS"
fi

echo ""
echo -e "${BLUE}Running: $PYTEST_CMD${NC}"
echo ""

# Run tests
$PYTEST_CMD

# Capture exit code
EXIT_CODE=$?

# Deactivate venv
deactivate

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                    All Security Tests Passed!                        ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════╝${NC}"
else
    echo -e "${RED}╔══════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║                    Some Security Tests Failed                        ║${NC}"
    echo -e "${RED}╚══════════════════════════════════════════════════════════════════════╝${NC}"
fi

exit $EXIT_CODE




