#!/usr/bin/env bash
# Quick test script to verify LLM container connectivity
set -euo pipefail

echo "=========================================="
echo "Testing LLM Container Connectivity"
echo "=========================================="
echo ""

# Test IPs
LITELLM_IP="10.96.201.207"
OLLAMA_IP="10.96.201.208"
VLLM_IP="10.96.201.209"

test_container() {
  local name=$1
  local ip=$2
  
  echo -n "Testing ${name} (${ip})... "
  if ping -c 1 -W 2 "${ip}" &>/dev/null; then
    echo "✅ REACHABLE"
    
    echo -n "  SSH access... "
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@"${ip}" "hostname" &>/dev/null; then
      echo "✅ OK"
    else
      echo "❌ FAILED"
    fi
  else
    echo "❌ UNREACHABLE"
  fi
  echo ""
}

test_container "LiteLLM" "$LITELLM_IP"
test_container "Ollama" "$OLLAMA_IP"
test_container "vLLM" "$VLLM_IP"

echo "=========================================="
echo "Next: Configure GPU passthrough for Ollama and vLLM"
echo "See: specs/003-llm-infrastructure/DEPLOYMENT_GUIDE.md"
echo "=========================================="

