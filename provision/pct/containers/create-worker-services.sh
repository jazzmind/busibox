#!/usr/bin/env bash
#
# Create Worker Services LXC Containers
#
# Description:
#   Creates worker service containers: ingest worker and liteLLM.
#   These handle background processing and LLM API gateway functionality.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, provision/pct/lib/functions.sh
#
# Usage:
#   bash provision/pct/containers/create-worker-services.sh [test|production]
#
# Containers Created:
#   - ingest-lxc  - Document ingestion worker with Redis
#   - litellm-lxc - LiteLLM API gateway

set -euo pipefail

# Determine mode from argument
MODE="${1:-production}"

# Get script directory and source dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source configuration
if [[ "$MODE" == "test" ]]; then
  echo "==> Creating worker services in TEST mode"
  source "${PCT_DIR}/test-vars.env"
  PREFIX="${TEST_PREFIX}"
  
  CT_INGEST="$CT_INGEST_TEST"
  CT_LITELLM="$CT_LITELLM_TEST"
  
  IP_INGEST="$IP_INGEST_TEST"
  IP_LITELLM="$IP_LITELLM_TEST"
else
  echo "==> Creating worker services in PRODUCTION mode"
  source "${PCT_DIR}/vars.env"
  PREFIX=""
fi

# Source common functions
source "${PCT_DIR}/lib/functions.sh"

# Validate environment
validate_env || exit 1

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

# Create ingest worker container
# Ingest needs more memory for Marker models (OCR, layout detection, etc.)
# With 255GB system RAM available, give it 32GB for comfortable headroom
MEM_MB_INGEST=32768
create_ct "$CT_INGEST" "$IP_INGEST" "${PREFIX}ingest-lxc" unpriv || cleanup_on_error
CREATED_CONTAINERS+=("$CT_INGEST")

# Increase memory allocation for ingest container (needs 32GB for Marker models)
pct set "$CT_INGEST" -memory "$MEM_MB_INGEST"
echo "  Increased ${PREFIX}ingest-lxc memory to ${MEM_MB_INGEST}MB (32GB) for ML models"

# Stop container to configure GPU passthrough
echo "==> Stopping container to configure GPU passthrough"
pct stop "$CT_INGEST" || true
sleep 2

# Add ALL GPUs passthrough for ingest container
# All GPUs are passed through, but services default to GPU 0 via CUDA_VISIBLE_DEVICES
# This allows flexibility to use other GPUs if needed
add_all_gpus "$CT_INGEST" || {
  echo "WARNING: Failed to configure GPU passthrough for ingest container"
  echo "  GPU passthrough can be configured manually later:"
  echo "  bash provision/pct/host/configure-gpu-passthrough.sh $CT_INGEST"
}

# Restart container
echo "==> Starting container with GPU access"
pct start "$CT_INGEST" || {
  echo "ERROR: Failed to start container"
  exit 1
}

# Create liteLLM container
create_ct "$CT_LITELLM" "$IP_LITELLM" "${PREFIX}litellm-lxc" unpriv || cleanup_on_error
CREATED_CONTAINERS+=("$CT_LITELLM")

echo ""
echo "=========================================="
echo "Worker services created successfully!"
echo "Mode: ${MODE}"
echo "Containers:"
echo "  - ${PREFIX}ingest-lxc:  $CT_INGEST @ $IP_INGEST"
echo "  - ${PREFIX}litellm-lxc: $CT_LITELLM @ $IP_LITELLM"
echo "=========================================="

