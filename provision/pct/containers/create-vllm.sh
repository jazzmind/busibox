#!/usr/bin/env bash
#
# Create vLLM LXC Container
#
# Description:
#   Creates vLLM container with GPU passthrough (ALL GPUs) and model storage mount.
#   vLLM is used for high-performance LLM inference with GPU acceleration.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, nvidia-smi, provision/pct/lib/functions.sh
#
# Usage:
#   bash provision/pct/containers/create-vllm.sh [test|production]
#
# Notes:
#   - Requires NVIDIA drivers installed on host
#   - Automatically passes through ALL available GPUs
#   - Uses /var/lib/llm-models/huggingface for model storage
#   - Requires 40GB disk space for container

set -euo pipefail

# Determine mode from argument
MODE="${1:-production}"

# Get script directory and source dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source configuration
if [[ "$MODE" == "test" ]]; then
  echo "==> Creating vLLM container in TEST mode"
  source "${PCT_DIR}/test-vars.env"
  CTID="$CT_VLLM_TEST"
  IP="$IP_VLLM_TEST"
  NAME="${TEST_PREFIX}vllm-lxc"
else
  echo "==> Creating vLLM container in PRODUCTION mode"
  source "${PCT_DIR}/vars.env"
  CTID="$CT_VLLM"
  IP="$IP_VLLM"
  NAME="vllm-lxc"
fi

# Source common functions
source "${PCT_DIR}/lib/functions.sh"

# Validate environment
validate_env || exit 1

# Create container (privileged for GPU access, 40GB disk)
create_ct "$CTID" "$IP" "$NAME" priv 40 || exit 1

# Add model storage mount
add_data_mount "$CTID" "/var/lib/llm-models/huggingface" "/var/lib/llm-models/huggingface" "0" || {
  echo "ERROR: Failed to add model storage mount"
  exit 1
}

# Stop container to configure GPU
echo "==> Stopping container to configure GPU passthrough"
pct stop "$CTID" || true
sleep 2

# Add ALL GPUs passthrough
add_all_gpus "$CTID" || {
  echo "ERROR: Failed to configure GPU passthrough"
  exit 1
}

# Restart container
echo "==> Starting container with GPU access"
pct start "$CTID" || {
  echo "ERROR: Failed to start container"
  exit 1
}

echo ""
echo "=========================================="
echo "vLLM container created successfully!"
echo "Container ID: $CTID"
echo "IP Address: $IP"
echo "Name: $NAME"
echo "GPU Access: ALL available GPUs"
echo "=========================================="

