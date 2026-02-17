#!/usr/bin/env bash
#
# Create Worker Services LXC Containers
#
# Description:
#   Creates worker service containers: data worker and liteLLM.
#   These handle background processing and LLM API gateway functionality.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, provision/pct/lib/functions.sh
#
# Usage:
#   bash provision/pct/containers/create-worker-services.sh [staging|production]
#
# Containers Created:
#   - data-lxc  - Document ingestion worker with Redis
#   - litellm-lxc - LiteLLM API gateway

set -euo pipefail

# Determine mode from argument
MODE="${1:-production}"

# Get script directory and source dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source configuration
if [[ "$MODE" == "staging" ]]; then
  echo "==> Creating worker services in STAGING mode"
  source "${PCT_DIR}/stage-vars.env"
  PREFIX="${STAGE_PREFIX}"
  
  CT_DATA="$CT_DATA_STAGING"
  CT_LITELLM="$CT_LITELLM_STAGING"
  
  IP_DATA="$IP_DATA_STAGING"
  IP_LITELLM="$IP_LITELLM_STAGING"
else
  echo "==> Creating worker services in PRODUCTION mode"
  source "${PCT_DIR}/vars.env"
  PREFIX=""
fi

# Source common functions
source "${PCT_DIR}/lib/functions.sh"

# Validate environment
validate_env || exit 1

# Use environment-specific data directories to isolate staging from production.
if [[ "$MODE" == "staging" ]]; then
  DATA_BASE="/var/lib/data-staging"
else
  DATA_BASE="/var/lib/data"
fi

# Track created containers for cleanup on error
CREATED_CONTAINERS=()

cleanup_on_error() {
  echo ""
  echo "=========================================="
  echo "Error occurred - cleaning up created containers"
  echo "=========================================="
  for ctid in "${CREATED_CONTAINERS[@]}"; do
    if pct status "$ctid" &>/dev/null; then
      echo "Removing container $ctid..."
      pct stop "$ctid" 2>/dev/null || true
      sleep 2
      pct destroy "$ctid" --purge 2>/dev/null || true
    fi
  done
  echo "Cleanup complete"
  exit 1
}

# Create data worker container
# Data needs more memory for Marker models (OCR, layout detection, etc.)
# With 255GB system RAM available, give it 32GB for comfortable headroom
MEM_MB_DATA=32768
create_ct "$CT_DATA" "$IP_DATA" "${PREFIX}data-lxc" unpriv || cleanup_on_error
CREATED_CONTAINERS+=("$CT_DATA")

# Increase memory allocation for data container (needs 32GB for Marker models)
pct set "$CT_DATA" -memory "$MEM_MB_DATA"
echo "  Increased ${PREFIX}data-lxc memory to ${MEM_MB_DATA}MB (32GB) for ML models"

# Stop container to configure GPU passthrough
echo "==> Stopping container to configure GPU passthrough"
pct stop "$CT_DATA" || true
sleep 2

# Add ALL GPUs passthrough for data container
# All GPUs are passed through, but services default to GPU 0 via CUDA_VISIBLE_DEVICES
# This allows flexibility to use other GPUs if needed
add_all_gpus "$CT_DATA" || {
  echo "WARNING: Failed to configure GPU passthrough for data container"
  echo "  GPU passthrough can be configured manually later:"
  echo "  bash provision/pct/host/configure-gpu-passthrough.sh $CT_DATA"
}

# Restart container
echo "==> Starting container with GPU access"
pct start "$CT_DATA" || {
  echo "ERROR: Failed to start container"
  exit 1
}

# Mount embedding model cache from host
echo "==> Mounting embedding model cache"
add_data_mount "$CT_DATA" "/var/lib/embedding-models/fastembed" "/var/lib/embedding-models/fastembed" "0" || {
  echo "WARNING: Failed to mount embedding model cache"
  echo "  Mount can be configured manually later"
}

# Mount Redis data directory from host for persistence across LXC rebuilds
echo "==> Mounting Redis data directory"
add_data_mount "$CT_DATA" "${DATA_BASE}/redis" "/var/lib/redis" "1" || {
  echo "WARNING: Failed to mount Redis data directory"
  echo "  Mount can be configured manually later"
}

# Create liteLLM container
create_ct "$CT_LITELLM" "$IP_LITELLM" "${PREFIX}litellm-lxc" unpriv || cleanup_on_error
CREATED_CONTAINERS+=("$CT_LITELLM")

echo ""
echo "=========================================="
echo "Worker services created successfully!"
echo "Mode: ${MODE}"
echo "Containers:"
echo "  - ${PREFIX}data-lxc:  $CT_DATA @ $IP_DATA"
echo "  - ${PREFIX}litellm-lxc: $CT_LITELLM @ $IP_LITELLM"
echo "=========================================="

