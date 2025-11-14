#!/bin/bash
# Fix ColPali Model Cache Permissions
# 
# Execution context: Run from admin workstation
# Purpose: Fix permissions on model cache for ColPali service
# 
# Usage: bash scripts/fix-colpali-permissions.sh

set -euo pipefail

VLLM_LXC_IP="10.96.200.208"

echo "=== Fixing ColPali Model Cache Permissions ==="
echo ""

echo "Connecting to vllm-lxc (${VLLM_LXC_IP})..."
echo ""

ssh root@${VLLM_LXC_IP} << 'ENDSSH'
set -e

echo "1. Checking vllm group..."
if ! getent group vllm > /dev/null 2>&1; then
    echo "   Creating vllm group..."
    groupadd vllm
else
    echo "   ✓ vllm group exists"
fi

echo ""
echo "2. Adding colpali user to vllm group..."
if ! groups colpali 2>/dev/null | grep -q vllm; then
    usermod -a -G vllm colpali
    echo "   ✓ Added colpali to vllm group"
else
    echo "   ✓ colpali already in vllm group"
fi

echo ""
echo "3. Fixing permissions on model cache..."
chgrp -R vllm /var/lib/llm-models/huggingface 2>/dev/null || true
chmod -R g+rwX /var/lib/llm-models/huggingface 2>/dev/null || true

echo ""
echo "4. Fixing specific model directories..."
for model_dir in \
    "/var/lib/llm-models/huggingface/models--vidore--colpali-v1.3" \
    "/var/lib/llm-models/huggingface/models--google--paligemma-3b-pt-448" \
    "/var/lib/llm-models/huggingface/hub"
do
    if [ -d "$model_dir" ]; then
        echo "   Fixing: $model_dir"
        chgrp -R vllm "$model_dir"
        chmod -R g+rwX "$model_dir"
        # Remove any lock files from failed downloads
        find "$model_dir" -name "*.lock" -exec rm -f {} \; 2>/dev/null || true
    fi
done

echo ""
echo "5. Verifying permissions..."
ls -ld /var/lib/llm-models/huggingface/models--vidore--colpali-v1.3 || echo "   (ColPali model not cached)"
ls -ld /var/lib/llm-models/huggingface/models--google--paligemma-3b-pt-448 || echo "   (PaliGemma model not cached)"

echo ""
echo "6. Restarting colpali service..."
systemctl restart colpali

echo ""
echo "7. Checking service status..."
sleep 2
systemctl status colpali --no-pager || true

ENDSSH

echo ""
echo "=== Done ==="
echo ""
echo "Check logs with:"
echo "  ssh root@${VLLM_LXC_IP} journalctl -u colpali -f"

