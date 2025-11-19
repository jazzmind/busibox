#!/usr/bin/env bash
#
# Create Busibox LXC Infrastructure
#
# Description:
#   Main orchestrator script for creating all Busibox LXC containers.
#   This script calls individual container creation scripts in the proper order.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, provision/pct/lib/functions.sh, provision/pct/containers/*.sh
#
# Usage:
#   bash provision/pct/create_lxc_base.sh [test|production] [--with-ollama]
#
# Arguments:
#   MODE          - test or production (default: production)
#   --with-ollama - Include optional Ollama container (default: not created)
#
# Examples:
#   bash provision/pct/create_lxc_base.sh production           # Production without Ollama
#   bash provision/pct/create_lxc_base.sh test --with-ollama   # Test with Ollama
#
# Containers Created (in order):
#   1. Core Services:    proxy, apps, agent
#   2. Data Services:    postgres, milvus, minio
#   3. Worker Services:  ingest, litellm
#   4. LLM Services:     vllm (ollama optional with --with-ollama)
#
# Notes:
#   - Requires Proxmox host setup completed (setup-proxmox-host.sh)
#   - Creates containers in dependency order
#   - All support scripts can be run independently for debugging

set -euo pipefail

# Get script directory (containers subdirectory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Parse arguments
MODE="${1:-production}"
CREATE_OLLAMA=false

shift || true  # Remove first argument if it exists
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-ollama)
      CREATE_OLLAMA=true
      shift
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [test|production] [--with-ollama]"
      exit 1
      ;;
  esac
done

# Source configuration to display settings
if [[ "$MODE" == "test" ]]; then
  echo "=========================================="
  echo "Busibox Infrastructure - TEST Mode"
  echo "=========================================="
  source "${PCT_DIR}/test-vars.env"
  print_test_config
else
  echo "=========================================="
  echo "Busibox Infrastructure - PRODUCTION Mode"
  echo "=========================================="
  source "${PCT_DIR}/vars.env"
  echo ""
  echo "Creating containers in PRODUCTION environment"
fi

echo ""
echo "Ollama Container: $(if $CREATE_OLLAMA; then echo "ENABLED"; else echo "DISABLED (use --with-ollama to enable)"; fi)"
echo "=========================================="
echo ""

# Track overall progress
TOTAL_STEPS=4
if $CREATE_OLLAMA; then
  TOTAL_STEPS=5
fi
CURRENT_STEP=0

print_step() {
  CURRENT_STEP=$((CURRENT_STEP + 1))
  echo ""
  echo "=========================================="
  echo "Step $CURRENT_STEP/$TOTAL_STEPS: $1"
  echo "=========================================="
}

# Step 1: Create core services
print_step "Creating Core Services (proxy, apps, agent)"
bash "${SCRIPT_DIR}/create-core-services.sh" "$MODE" || {
  echo "ERROR: Failed to create core services"
  exit 1
}

# Step 2: Create data services
print_step "Creating Data Services (postgres, milvus, minio)"
bash "${SCRIPT_DIR}/create-data-services.sh" "$MODE" || {
  echo "ERROR: Failed to create data services"
  exit 1
}

# Step 3: Create worker services
print_step "Creating Worker Services (ingest, litellm)"
bash "${SCRIPT_DIR}/create-worker-services.sh" "$MODE" || {
  echo "ERROR: Failed to create worker services"
  exit 1
}

# Step 4: Create vLLM (with all GPUs)
print_step "Creating vLLM Service (all GPUs)"
bash "${SCRIPT_DIR}/create-vllm.sh" "$MODE" || {
  echo "ERROR: Failed to create vLLM container"
  exit 1
}

# Step 5 (optional): Create Ollama
if $CREATE_OLLAMA; then
  print_step "Creating Ollama Service (optional, single GPU)"
  bash "${SCRIPT_DIR}/create-ollama.sh" "$MODE" 0 || {
    echo "WARNING: Failed to create Ollama container"
    echo "This is optional - continuing anyway"
  }
fi

# Final summary
echo ""
echo "=========================================="
echo "✓ All containers created successfully!"
echo "=========================================="
echo ""
echo "Mode: ${MODE^^}"
echo ""

if [[ "$MODE" == "test" ]]; then
  echo "Test Containers Created:"
  echo "  Core Services:"
  echo "    - TEST-proxy-lxc  ($CT_PROXY_TEST @ $IP_PROXY_TEST)"
  echo "    - TEST-apps-lxc   ($CT_APPS_TEST @ $IP_APPS_TEST)"
  echo "    - TEST-agent-lxc  ($CT_AGENT_TEST @ $IP_AGENT_TEST)"
  echo ""
  echo "  Data Services:"
  echo "    - TEST-pg-lxc     ($CT_PG_TEST @ $IP_PG_TEST)"
  echo "    - TEST-milvus-lxc ($CT_MILVUS_TEST @ $IP_MILVUS_TEST)"
  echo "    - TEST-files-lxc  ($CT_FILES_TEST @ $IP_FILES_TEST)"
  echo ""
  echo "  Worker Services:"
  echo "    - TEST-ingest-lxc ($CT_INGEST_TEST @ $IP_INGEST_TEST) [All GPUs, defaults to GPU 0]"
  echo "    - TEST-litellm-lxc ($CT_LITELLM_TEST @ $IP_LITELLM_TEST)"
  echo ""
  echo "  LLM Services:"
  echo "    - TEST-vllm-lxc   ($CT_VLLM_TEST @ $IP_VLLM_TEST) [GPUs 1+]"
  if $CREATE_OLLAMA; then
    echo "    - TEST-ollama-lxc ($CT_OLLAMA_TEST @ $IP_OLLAMA_TEST) [GPU 0]"
  fi
else
  echo "Production Containers Created:"
  echo "  Core Services:"
  echo "    - proxy-lxc  ($CT_PROXY @ $IP_PROXY)"
  echo "    - apps-lxc   ($CT_APPS @ $IP_APPS)"
  echo "    - agent-lxc  ($CT_AGENT @ $IP_AGENT)"
  echo ""
  echo "  Data Services:"
  echo "    - pg-lxc     ($CT_PG @ $IP_PG)"
  echo "    - milvus-lxc ($CT_MILVUS @ $IP_MILVUS)"
  echo "    - files-lxc  ($CT_FILES @ $IP_FILES)"
  echo ""
  echo "  Worker Services:"
  echo "    - ingest-lxc ($CT_INGEST @ $IP_INGEST) [All GPUs, defaults to GPU 0]"
  echo "    - litellm-lxc ($CT_LITELLM @ $IP_LITELLM)"
  echo ""
  echo "  LLM Services:"
  echo "    - vllm-lxc   ($CT_VLLM @ $IP_VLLM) [GPUs 1+]"
  if $CREATE_OLLAMA; then
    echo "    - ollama-lxc ($CT_OLLAMA @ $IP_OLLAMA) [GPU 0]"
  fi
fi

echo ""
echo "Next Steps:"
echo "  1. Configure containers: cd provision/ansible && make ${MODE}"
echo "  2. Test infrastructure: bash scripts/test-infrastructure.sh"
if $CREATE_OLLAMA; then
  echo "  3. Setup LLM models: bash provision/pct/setup-llm-models.sh"
fi
echo ""
echo "Individual Container Management:"
echo "  - Recreate single service: bash provision/pct/containers/create-{service}.sh ${MODE}"
echo "  - Check GPU usage: bash provision/pct/diagnostic/check-gpu-usage.sh"
echo "  - Check storage: bash provision/pct/diagnostic/check-storage.sh"
echo "=========================================="
