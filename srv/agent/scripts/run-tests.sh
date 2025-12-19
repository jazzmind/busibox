#!/usr/bin/env bash
# Run agent server tests (works both locally and on deployed host)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Set PYTHONPATH to include project root
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

# Set LiteLLM API key for tests (dispatcher agent needs it)
export LITELLM_API_KEY="${LITELLM_API_KEY:-sk-test-key}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Agent Server Test Runner${NC}"
echo "=========================="
echo ""

# Check if we're in a venv or have pytest available
if command -v pytest &> /dev/null; then
    PYTEST_CMD="pytest"
elif [ -f ".venv/bin/pytest" ]; then
    echo "Activating virtual environment (.venv)..."
    source .venv/bin/activate
    PYTEST_CMD="pytest"
elif [ -f "venv/bin/pytest" ]; then
    echo "Activating virtual environment (venv)..."
    source venv/bin/activate
    PYTEST_CMD="pytest"
else
    echo -e "${RED}Error: pytest not found${NC}"
    echo ""
    echo "To set up locally:"
    echo "  bash scripts/setup-venv.sh"
    echo "  source venv/bin/activate"
    echo ""
    echo "Or install dependencies:"
    echo "  pip install -r requirements.test.txt"
    exit 1
fi

# Parse arguments
TEST_TYPE="${1:-all}"
COVERAGE="${2:-}"

case "$TEST_TYPE" in
    unit)
        echo "Running unit tests..."
        $PYTEST_CMD tests/unit/ -v
        ;;
    integration)
        echo "Running integration tests..."
        $PYTEST_CMD tests/integration/ -v
        ;;
    coverage|cov)
        echo "Running tests with coverage..."
        $PYTEST_CMD tests/ -v --cov=app --cov-report=html --cov-report=term
        echo ""
        echo "Coverage report: htmlcov/index.html"
        ;;
    all|*)
        echo "Running all tests..."
        if [ "$COVERAGE" = "cov" ] || [ "$COVERAGE" = "coverage" ]; then
            $PYTEST_CMD tests/ -v --cov=app --cov-report=html --cov-report=term
        else
            $PYTEST_CMD tests/ -v
        fi
        ;;
esac

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✓ Tests passed${NC}"
else
    echo ""
    echo -e "${RED}✗ Tests failed${NC}"
fi

exit $EXIT_CODE








