#!/bin/bash
#
# Check Container Memory Allocation
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Verify container memory allocation matches CPU offload requirements
#
# USAGE:
#   bash check-container-memory.sh [test|production]
#
# WHAT IT DOES:
#   1. Checks vLLM container memory allocation
#   2. Compares against CPU offload requirements (200GB)
#   3. Provides update command if needed
#
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Determine mode
MODE="${1:-production}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source configuration
if [[ "$MODE" == "test" ]]; then
  source "${PCT_DIR}/test-vars.env"
  CT_VLLM="$CT_VLLM_TEST"
else
  source "${PCT_DIR}/vars.env"
fi

# Required memory for vLLM (200GB = 204800MB)
REQUIRED_MB=204800
REQUIRED_GB=200

echo "=========================================="
echo "Container Memory Allocation Check"
echo "Mode: ${MODE}"
echo "=========================================="
echo ""

# Check if container exists
if ! pct status "$CT_VLLM" &>/dev/null; then
  error "vLLM container ($CT_VLLM) does not exist"
  exit 1
fi

# Get current memory allocation
CURRENT_MB=$(pct config "$CT_VLLM" | grep -E "^memory:" | awk '{print $2}' || echo "0")
CURRENT_GB=$((CURRENT_MB / 1024))

info "vLLM Container ($CT_VLLM):"
echo "  Current allocation: ${CURRENT_MB}MB (${CURRENT_GB}GB)"
echo "  Required: ${REQUIRED_MB}MB (${REQUIRED_GB}GB) for CPU offloading"
echo ""

# Check if memory is sufficient
if [[ "$CURRENT_MB" -ge "$REQUIRED_MB" ]]; then
  success "✓ Memory allocation is sufficient"
  echo ""
  echo "CPU Offload Requirements:"
  echo "  - Main vLLM service: 180GB (vllm_memory_limit)"
  echo "  - vLLM Embedding service: 70GB (vllm_embedding_memory_limit)"
  echo "  - Container allocation: ${CURRENT_GB}GB (supports both services)"
else
  warn "✗ Memory allocation is insufficient"
  echo ""
  echo "Current: ${CURRENT_GB}GB"
  echo "Required: ${REQUIRED_GB}GB"
  echo ""
  echo "To update memory allocation:"
  echo "  pct set $CT_VLLM -memory $REQUIRED_MB"
  echo "  pct reboot $CT_VLLM"
  echo ""
  echo "Or recreate container:"
  echo "  pct stop $CT_VLLM"
  echo "  pct destroy $CT_VLLM --purge"
  echo "  bash ${PCT_DIR}/containers/create-vllm.sh $MODE"
  echo ""
  exit 1
fi

echo "=========================================="

