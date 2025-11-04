#!/usr/bin/env bash
#
# Fix LiteLLM Prisma Schema Relation Issue
#
# Error: Field: "litellm_budget_table" either does not exist or is not 
# a relational field on the LiteLLM_VerificationToken model
#
set -e

echo "=========================================="
echo "Fixing LiteLLM Schema Relation"
echo "=========================================="
echo ""

# Activate venv
source /opt/litellm/venv/bin/activate
export DATABASE_URL=$(grep '^DATABASE_URL=' /etc/default/litellm | cut -d'=' -f2-)

# Find schema location
SCHEMA_DIR=$(python -c "import os, litellm; print(os.path.dirname(litellm.__file__))")/proxy
SCHEMA_FILE="$SCHEMA_DIR/schema.prisma"

echo "[1/4] Checking schema file..."
if [ ! -f "$SCHEMA_FILE" ]; then
    echo "  ✗ Schema file not found: $SCHEMA_FILE"
    exit 1
fi
echo "  ✓ Schema file: $SCHEMA_FILE"
echo ""

echo "[2/4] Checking for problematic relation..."
if grep -q "litellm_budget_table" "$SCHEMA_FILE"; then
    echo "  Found reference to litellm_budget_table"
    grep -n "litellm_budget_table" "$SCHEMA_FILE" | head -5
else
    echo "  No reference to litellm_budget_table found"
fi
echo ""

echo "[3/4] Regenerating Prisma client..."
cd "$SCHEMA_DIR"
prisma generate --schema="$SCHEMA_FILE"
echo "  ✓ Client regenerated"
echo ""

echo "[4/4] Resetting Prisma client cache..."
# Clear any cached client
rm -rf /opt/litellm/venv/lib/python3.11/site-packages/prisma/__pycache__
echo "  ✓ Cache cleared"
echo ""

echo "=========================================="
echo "Schema fix complete"
echo "=========================================="
echo ""
echo "Restart LiteLLM to apply changes:"
echo "  systemctl restart litellm"

