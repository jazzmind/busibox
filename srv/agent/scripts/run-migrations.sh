#!/bin/bash
# Run database migrations for agent-server
# This script should be run before starting the service

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Load environment variables if .env exists
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Check if DATABASE_URL is set
if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL environment variable is not set"
    exit 1
fi

echo "Running database migrations..."
echo "Database: ${DATABASE_URL%%\?*}"  # Print URL without query params

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Check if alembic_version table exists
if ! alembic current &> /dev/null; then
    echo "No migration history found. Checking if tables already exist..."
    
    # Try to stamp the database with the current version if tables exist
    if alembic stamp head 2>&1 | grep -q "Can't locate revision identified by"; then
        echo "Stamping database at initial revision..."
        alembic stamp 001
    fi
fi

# Run Alembic migrations
alembic upgrade head

echo "✓ Migrations completed successfully"

