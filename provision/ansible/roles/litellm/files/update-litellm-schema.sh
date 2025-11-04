#!/usr/bin/env bash
#
# Update LiteLLM Database Schema (Non-Destructive)
#
# Simply updates the database schema to match the current LiteLLM version
# Does NOT drop any tables or delete any data
#
set -euo pipefail

echo "=========================================="
echo "LiteLLM Database Schema Update"
echo "=========================================="
echo ""

# Load environment
source /etc/default/litellm
export DATABASE_URL

# Activate venv
source /opt/litellm/venv/bin/activate

# Get Prisma directory
PRISMA_DIR=$(python -c "import os, litellm; print(os.path.dirname(litellm.__file__))")/proxy
cd "$PRISMA_DIR"

echo "[1/2] Regenerating Prisma client..."
prisma generate

echo ""
echo "[2/2] Updating database schema (non-destructive)..."
prisma db push --skip-generate

echo ""
echo "=========================================="
echo "Schema updated successfully!"
echo "=========================================="
echo ""
echo "Restart LiteLLM: systemctl restart litellm"

