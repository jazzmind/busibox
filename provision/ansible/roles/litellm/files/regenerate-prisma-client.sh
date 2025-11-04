#!/usr/bin/env bash
#
# Regenerate Prisma Client for LiteLLM
#
# This fixes the issue where the generated client is out of sync with the schema
#
set -e

echo "=========================================="
echo "Regenerating Prisma Client for LiteLLM"
echo "=========================================="
echo ""

# Stop LiteLLM service
echo "[1/7] Stopping LiteLLM service..."
systemctl stop litellm
echo "  ✓ Service stopped"
echo ""

# Activate venv
echo "[2/7] Activating virtual environment..."
source /opt/litellm/venv/bin/activate
export DATABASE_URL=$(grep '^DATABASE_URL=' /etc/default/litellm | cut -d'=' -f2-)
echo "  ✓ Venv activated"
echo ""

# Find schema location
SCHEMA_DIR=$(python -c "import os, litellm; print(os.path.dirname(litellm.__file__))")/proxy
SCHEMA_FILE="$SCHEMA_DIR/schema.prisma"
SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])")

echo "[3/7] Schema location: $SCHEMA_DIR"
echo ""

# Remove old generated client
echo "[4/7] Removing old generated client..."
rm -rf "$SITE_PACKAGES/prisma"
rm -rf "$SCHEMA_DIR/.prisma"
echo "  ✓ Old client removed"
echo ""

# Reinstall prisma package
echo "[5/7] Reinstalling Prisma package..."
pip install --force-reinstall --no-cache-dir prisma
echo "  ✓ Prisma reinstalled"
echo ""

# Generate new client
echo "[6/7] Generating new Prisma client..."
cd "$SCHEMA_DIR"
prisma generate --schema="$SCHEMA_FILE"
echo "  ✓ New client generated"
echo ""

# Verify generation
echo "[7/7] Verifying generation..."
if [ -d "$SITE_PACKAGES/prisma" ]; then
    echo "  ✓ Generated client found at: $SITE_PACKAGES/prisma"
    echo ""
    echo "Client contents:"
    ls -la "$SITE_PACKAGES/prisma/" | head -15
else
    echo "  ✗ Generated client NOT found"
    exit 1
fi
echo ""

echo "=========================================="
echo "Prisma client regenerated successfully"
echo "=========================================="
echo ""
echo "Starting LiteLLM service..."
systemctl start litellm
sleep 3
systemctl status litellm --no-pager -l
echo ""
echo "Check logs with:"
echo "  journalctl -u litellm -f"

