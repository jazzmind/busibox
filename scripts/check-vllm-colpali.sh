#!/bin/bash
#
# Check vLLM ColPali Service Status
#
# EXECUTION CONTEXT: Run from admin workstation
# PURPOSE: Diagnose vLLM ColPali service issues
#
set -euo pipefail

VLLM_IP="10.96.200.208"
COLPALI_PORT="8002"

echo "=========================================="
echo "vLLM ColPali Service Diagnostics"
echo "=========================================="
echo ""

echo "1. Service Status:"
ssh root@${VLLM_IP} "systemctl status vllm-colpali --no-pager" || true
echo ""

echo "2. Recent Logs (last 100 lines):"
ssh root@${VLLM_IP} "journalctl -u vllm-colpali -n 100 --no-pager"
echo ""

echo "3. GPU Status:"
ssh root@${VLLM_IP} "nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv"
echo ""

echo "4. Check if port is listening:"
ssh root@${VLLM_IP} "ss -tlnp | grep ${COLPALI_PORT} || echo 'Port ${COLPALI_PORT} not listening'"
echo ""

echo "5. Model cache check:"
ssh root@${VLLM_IP} "ls -lh /var/lib/llm-models/huggingface/hub/ | grep -E 'colpali|paligemma' || echo 'No ColPali/PaliGemma models found'"
echo ""

echo "6. Service environment:"
ssh root@${VLLM_IP} "systemctl show vllm-colpali | grep -E 'Environment|ExecStart'"
echo ""

echo "=========================================="
echo "Diagnostics complete"
echo "=========================================="

