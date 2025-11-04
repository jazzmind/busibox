#!/usr/bin/env bash
#
# Test if Prisma can be imported in the LiteLLM environment
#
set -e

echo "=========================================="
echo "Testing Prisma Import"
echo "=========================================="
echo ""

echo "[1/5] Testing as current user..."
source /opt/litellm/venv/bin/activate
python3 -c "import prisma; print('✓ Prisma imports successfully'); print(f'  Version: {prisma.__version__}')" || echo "✗ Cannot import prisma"
echo ""

echo "[2/5] Testing as litellm user..."
sudo -u litellm bash -c "source /opt/litellm/venv/bin/activate && python3 -c 'import prisma; print(\"✓ Prisma imports successfully\")'" || echo "✗ Cannot import prisma as litellm user"
echo ""

echo "[3/5] Checking if prisma is in pip list..."
source /opt/litellm/venv/bin/activate
pip list | grep prisma || echo "✗ Prisma not in pip list"
echo ""

echo "[4/5] Checking site-packages..."
SITE_PACKAGES=$(python3 -c "import site; print(site.getsitepackages()[0])")
echo "Site packages: $SITE_PACKAGES"
if [ -d "$SITE_PACKAGES/prisma" ]; then
    echo "✓ prisma directory exists"
    ls -la "$SITE_PACKAGES/prisma/" | head -10
else
    echo "✗ prisma directory NOT found"
fi
echo ""

echo "[5/5] Testing import with full path..."
PYTHONPATH="$SITE_PACKAGES" python3 -c "import prisma; print('✓ Prisma imports with PYTHONPATH')" || echo "✗ Cannot import even with PYTHONPATH"
echo ""

echo "=========================================="
echo "Checking systemd service environment..."
echo "=========================================="
echo ""
echo "Service file environment:"
systemctl cat litellm | grep -A5 "\[Service\]"
echo ""

echo "Checking if venv python is being used:"
sudo -u litellm bash -c "source /opt/litellm/venv/bin/activate && which python3 && python3 --version"

