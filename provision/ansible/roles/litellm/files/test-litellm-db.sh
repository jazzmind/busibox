#!/usr/bin/env bash
#
# Test LiteLLM Database Connection
#
# Quick script to verify DATABASE_URL is accessible and database is reachable
#
set -e

echo "=========================================="
echo "LiteLLM Database Connection Test"
echo "=========================================="
echo ""

# Test 1: Can we read the environment file?
echo "[1/4] Testing environment file access..."
if [ -r /etc/default/litellm ]; then
    echo "  ✓ Can read /etc/default/litellm"
    ls -la /etc/default/litellm
else
    echo "  ✗ Cannot read /etc/default/litellm"
    ls -la /etc/default/litellm
    exit 1
fi
echo ""

# Test 2: Load environment
echo "[2/4] Loading environment variables..."
source /etc/default/litellm

if [ -n "${DATABASE_URL}" ]; then
    echo "  ✓ DATABASE_URL is set"
    echo "  URL: ${DATABASE_URL%%@*}@***"
else
    echo "  ✗ DATABASE_URL is not set"
    exit 1
fi
echo ""

# Test 3: Test database connection
echo "[3/4] Testing database connection..."
if command -v psql &>/dev/null; then
    if psql "${DATABASE_URL}" -c "SELECT 1;" >/dev/null 2>&1; then
        echo "  ✓ Database connection successful"
    else
        echo "  ✗ Database connection failed"
        exit 1
    fi
else
    echo "  ⚠ psql not installed, skipping connection test"
fi
echo ""

# Test 4: Check LiteLLM tables
echo "[4/4] Checking LiteLLM tables..."
TABLE_COUNT=$(psql "${DATABASE_URL}" -t -c "SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'LiteLLM_%';" 2>/dev/null || echo "0")

if [ "$TABLE_COUNT" -gt 20 ]; then
    echo "  ✓ Found ${TABLE_COUNT} LiteLLM tables"
else
    echo "  ⚠ Only found ${TABLE_COUNT} LiteLLM tables (expected 25+)"
    echo "  Run: sudo -u litellm bash /usr/local/bin/fix-litellm-database"
fi
echo ""

echo "=========================================="
echo "Database test complete!"
echo "=========================================="

