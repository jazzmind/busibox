#!/usr/bin/env bash
#
# Create Ollama LXC Container (Optional)
#
# Description:
#   Creates Ollama container with single GPU passthrough and model storage mount.
#   Ollama provides a simple interface for running LLMs locally.
#   This container is OPTIONAL and not created by default.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, nvidia-smi, provision/pct/lib/functions.sh
#
# Usage:
#   bash provision/pct/containers/create-ollama.sh [test|production] [GPU_NUM]
#
# Arguments:
#   MODE     - test or production (default: production)
#   GPU_NUM  - GPU number to use (default: 0)
#
# Notes:
#   - Requires NVIDIA drivers installed on host
#   - Uses single GPU (specify with GPU_NUM argument)
#   - Uses /var/lib/llm-models/ollama for model storage

set -euo pipefail

# Determine mode from argument
MODE="${1:-production}"
GPU_NUM="${2:-0}"

# Get script directory and source dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source configuration
if [[ "$MODE" == "test" ]]; then
  echo "==> Creating Ollama container in TEST mode"
  source "${PCT_DIR}/test-vars.env"
  CTID="$CT_OLLAMA_TEST"
  IP="$IP_OLLAMA_TEST"
  NAME="${TEST_PREFIX}ollama-lxc"
else
  echo "==> Creating Ollama container in PRODUCTION mode"
  source "${PCT_DIR}/vars.env"
  CTID="$CT_OLLAMA"
  IP="$IP_OLLAMA"
  NAME="ollama-lxc"
fi

# Source common functions
source "${PCT_DIR}/lib/functions.sh"

# Validate environment
validate_env || exit 1

# Create container (privileged for GPU access)
create_ct "$CTID" "$IP" "$NAME" priv || exit 1

# Add model storage mount
add_data_mount "$CTID" "/var/lib/llm-models/ollama" "/var/lib/llm-models/ollama" "0" || {
  echo "ERROR: Failed to add model storage mount"
  exit 1
}

# Stop container to configure GPU
echo "==> Stopping container to configure GPU passthrough"
pct stop "$CTID" || true
sleep 2

# Add single GPU passthrough
add_gpu_passthrough "$CTID" "$GPU_NUM" || {
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
echo "Ollama container created successfully!"
echo "Container ID: $CTID"
echo "IP Address: $IP"
echo "Name: $NAME"
echo "GPU Access: GPU ${GPU_NUM}"
echo "=========================================="

