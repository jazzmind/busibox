#!/usr/bin/env bash
#
# Inspect LiteLLM Prisma Schema
#
set -e

source /opt/litellm/venv/bin/activate

SCHEMA_DIR=$(python -c "import os, litellm; print(os.path.dirname(litellm.__file__))")/proxy
SCHEMA_FILE="$SCHEMA_DIR/schema.prisma"

echo "=========================================="
echo "LiteLLM Prisma Schema Inspection"
echo "=========================================="
echo ""

echo "Schema file: $SCHEMA_FILE"
echo ""

echo "Looking for LiteLLM_VerificationToken model..."
echo "------------------------------------------------------------"
sed -n '/^model LiteLLM_VerificationToken/,/^}/p' "$SCHEMA_FILE"
echo ""

echo "Looking for LiteLLM_BudgetTable model..."
echo "------------------------------------------------------------"
sed -n '/^model LiteLLM_BudgetTable/,/^}/p' "$SCHEMA_FILE"
echo ""

echo "Searching for 'litellm_budget_table' references..."
echo "------------------------------------------------------------"
grep -n "litellm_budget_table" "$SCHEMA_FILE" || echo "No references found"
echo ""

echo "=========================================="
echo "Full schema models list:"
echo "=========================================="
grep "^model " "$SCHEMA_FILE"

