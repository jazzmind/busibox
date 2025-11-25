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

# Increase memory allocation for vLLM container
# vLLM needs significant RAM for CPU offloading of KV cache:
# - Main vLLM service: 180GB (vllm_memory_limit)
# - vLLM Embedding service: 70GB (vllm_embedding_memory_limit)
# Allocate 200GB to support both services + overhead
MEM_MB_VLLM=204800  # 200GB in MB
pct set "$CTID" -memory "$MEM_MB_VLLM"
echo "  Increased ${NAME} memory to ${MEM_MB_VLLM}MB (200GB) for CPU offloading"

# Add model storage mount
add_data_mount "$CTID" "/var/lib/llm-models/huggingface" "/var/lib/llm-models/huggingface" "0" || {
  echo "ERROR: Failed to add model storage mount"
  exit 1
}

# Stop container to configure GPU
echo "==> Stopping container to configure GPU passthrough"
pct stop "$CTID" || true
sleep 2

# Add GPUs 1+ passthrough (GPU 0 is reserved for ingest container)
# vLLM needs 2+ GPUs for tensor parallelism and model sharding
# Get total GPU count and use GPUs 1 onwards
if command -v nvidia-smi &>/dev/null; then
  GPU_COUNT=$(nvidia-smi -L | wc -l)
  if [[ "$GPU_COUNT" -gt 1 ]]; then
    # Build GPU list starting from GPU 1 (e.g., "1,2,3" or "1-3")
    if [[ "$GPU_COUNT" -eq 2 ]]; then
      GPU_LIST="1"
    elif [[ "$GPU_COUNT" -eq 3 ]]; then
      GPU_LIST="1,2"
    else
      # For 4+ GPUs, use range format
      END_GPU=$((GPU_COUNT - 1))
      GPU_LIST="1-${END_GPU}"
    fi
    
    echo "==> Configuring GPUs ${GPU_LIST} for vLLM (GPU 0 reserved for ingest)"
    add_gpus "$CTID" "$GPU_LIST" || {
      echo "ERROR: Failed to configure GPU passthrough"
      exit 1
    }
  else
    echo "WARNING: Only 1 GPU detected. vLLM needs 2+ GPUs for optimal performance."
    echo "  Consider using GPU 0 for vLLM and disabling GPU for ingest if needed."
    echo "  Configuring GPU 0 for vLLM (not recommended for production)"
    add_gpu_passthrough "$CTID" 0 || {
      echo "ERROR: Failed to configure GPU passthrough"
      exit 1
    }
  fi
else
  echo "WARNING: nvidia-smi not found. Skipping GPU passthrough."
  echo "  Configure GPU passthrough manually after NVIDIA drivers are installed."
fi

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
echo "Memory: ${MEM_MB_VLLM}MB (200GB) - supports CPU offloading"
if command -v nvidia-smi &>/dev/null; then
  GPU_COUNT=$(nvidia-smi -L | wc -l)
  if [[ "$GPU_COUNT" -gt 1 ]]; then
    END_GPU=$((GPU_COUNT - 1))
    echo "GPU Access: GPUs 1-${END_GPU} (GPU 0 reserved for ingest)"
  else
    echo "GPU Access: GPU 0 (WARNING: Only 1 GPU available)"
  fi
else
  echo "GPU Access: Not configured (nvidia-smi not found)"
fi
echo "=========================================="

