#!/usr/bin/env bash
# Fix migration state when database was stamped without running migrations
# This script resets alembic version and runs all migrations from scratch
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "=== Fixing Migration State ==="
echo ""

# Activate venv
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Check DATABASE_URL is set
if [ -z "${DATABASE_URL:-}" ]; then
    if [ -f ".env" ]; then
        echo "Loading DATABASE_URL from .env..."
        export $(grep DATABASE_URL .env | xargs)
    else
        echo "ERROR: DATABASE_URL not set and .env not found"
        exit 1
    fi
fi

echo "1. Checking current migration state..."
alembic current || echo "  (No current version or error)"
echo ""

echo "2. Resetting alembic version to base..."
psql "$DATABASE_URL" -c "DELETE FROM alembic_version;" || echo "  (Table might not exist)"
echo ""

echo "3. Running all migrations from scratch..."
alembic upgrade head
echo ""

echo "4. Verifying final state..."
alembic current
echo ""

echo "✓ Migration state fixed!"
echo ""
echo "Next steps:"
echo "  - Run tests: bash scripts/run-tests.sh"
echo "  - Or via make: make test-agent INV=inventory/test"





