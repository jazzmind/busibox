#!/usr/bin/env bash
#
# Restart LiteLLM Service (with daemon reload)
#
set -e

echo "=========================================="
echo "Restarting LiteLLM Service"
echo "=========================================="
echo ""

echo "[1/3] Reloading systemd daemon..."
systemctl daemon-reload
echo "  ✓ Daemon reloaded"
echo ""

echo "[2/3] Restarting LiteLLM service..."
systemctl restart litellm
echo "  ✓ Service restarted"
echo ""

echo "[3/3] Checking service status..."
sleep 2
systemctl status litellm --no-pager -l
echo ""

echo "=========================================="
echo "Service Status"
echo "=========================================="
echo ""

if systemctl is-active --quiet litellm; then
    echo "✓ LiteLLM is running"
    echo ""
    echo "Recent logs:"
    journalctl -u litellm -n 20 --no-pager
else
    echo "✗ LiteLLM failed to start"
    echo ""
    echo "Error logs:"
    journalctl -u litellm -n 50 --no-pager
    exit 1
fi

