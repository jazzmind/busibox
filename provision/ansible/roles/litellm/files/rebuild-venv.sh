#!/usr/bin/env bash
#
# Rebuild LiteLLM Virtual Environment
#
# The venv got corrupted by force reinstalling packages
#
set -e

echo "=========================================="
echo "Rebuilding LiteLLM Virtual Environment"
echo "=========================================="
echo ""

# Stop service
echo "[1/5] Stopping LiteLLM service..."
systemctl stop litellm || true
echo "  ✓ Service stopped"
echo ""

# Remove corrupted venv
echo "[2/5] Removing corrupted venv..."
rm -rf /opt/litellm/venv
echo "  ✓ Old venv removed"
echo ""

# Create fresh venv
echo "[3/5] Creating fresh venv..."
python3 -m venv /opt/litellm/venv
chown -R litellm:litellm /opt/litellm/venv
echo "  ✓ Fresh venv created"
echo ""

# Reinstall everything
echo "[4/5] Installing packages..."
sudo -u litellm bash << 'EOF'
source /opt/litellm/venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install 'litellm[proxy]'
pip install fastapi uvicorn pyyaml python-dotenv psycopg2-binary prisma
EOF
echo "  ✓ Packages installed"
echo ""

# Generate Prisma client
echo "[5/5] Generating Prisma client..."
sudo -u litellm bash << 'EOF'
set -e
source /opt/litellm/venv/bin/activate
export DATABASE_URL=$(grep '^DATABASE_URL=' /etc/default/litellm | cut -d'=' -f2-)

PRISMA_DIR=$(/opt/litellm/venv/bin/python3 -c "import os, litellm; print(os.path.dirname(litellm.__file__))")/proxy
cd "$PRISMA_DIR"
prisma generate
EOF
echo "  ✓ Prisma client generated"
echo ""

echo "=========================================="
echo "Venv rebuilt successfully!"
echo "=========================================="
echo ""
echo "Starting LiteLLM service..."
systemctl start litellm
sleep 3
systemctl status litellm --no-pager

