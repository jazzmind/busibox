#!/usr/bin/env bash
#
# Check Prisma Installation in LiteLLM venv
#
set -e

echo "=========================================="
echo "LiteLLM Prisma Diagnostic"
echo "=========================================="
echo ""

# Activate venv
echo "[1/6] Activating virtual environment..."
source /opt/litellm/venv/bin/activate
echo "  ✓ Venv activated: $VIRTUAL_ENV"
echo ""

# Check if prisma is installed
echo "[2/6] Checking if prisma package is installed..."
if python -c "import prisma" 2>/dev/null; then
    echo "  ✓ Prisma package is importable"
    PRISMA_VERSION=$(python -c "import prisma; print(prisma.__version__)" 2>/dev/null || echo "unknown")
    echo "  Version: $PRISMA_VERSION"
else
    echo "  ✗ Prisma package NOT importable"
    echo "  This is the problem!"
fi
echo ""

# List prisma in pip
echo "[3/6] Checking pip list for prisma..."
pip list | grep -i prisma || echo "  ✗ Prisma not found in pip list"
echo ""

# Check prisma CLI
echo "[4/6] Checking for prisma CLI..."
if command -v prisma &>/dev/null; then
    echo "  ✓ Prisma CLI found at: $(which prisma)"
    prisma --version || echo "  (version check failed)"
else
    echo "  ✗ Prisma CLI not found in PATH"
fi
echo ""

# Check LiteLLM's prisma directory
echo "[5/6] Checking LiteLLM's Prisma schema..."
PRISMA_DIR=$(python -c "import os, litellm; print(os.path.dirname(litellm.__file__))")/proxy
if [ -f "$PRISMA_DIR/schema.prisma" ]; then
    echo "  ✓ Prisma schema found at: $PRISMA_DIR/schema.prisma"
    echo "  Models: $(grep -c '^model ' "$PRISMA_DIR/schema.prisma")"
else
    echo "  ✗ Prisma schema not found at: $PRISMA_DIR/schema.prisma"
fi
echo ""

# Check generated Prisma client
echo "[6/6] Checking generated Prisma client..."
if [ -d "$PRISMA_DIR/.prisma" ]; then
    echo "  ✓ Generated Prisma client exists"
    ls -la "$PRISMA_DIR/.prisma/" 2>/dev/null || echo "  (cannot list directory)"
else
    echo "  ✗ Generated Prisma client NOT found at: $PRISMA_DIR/.prisma"
    echo "  Run: prisma generate (in $PRISMA_DIR)"
fi
echo ""

echo "=========================================="
echo "Diagnostic complete!"
echo "=========================================="
echo ""
echo "Summary:"
if python -c "import prisma" 2>/dev/null; then
    echo "  ✓ Prisma is installed and working"
else
    echo "  ✗ Prisma is NOT installed or not working"
    echo ""
    echo "To fix, run:"
    echo "  sudo -u litellm bash -c 'source /opt/litellm/venv/bin/activate && pip install --force-reinstall prisma'"
fi

