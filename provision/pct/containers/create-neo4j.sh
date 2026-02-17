#!/usr/bin/env bash
#
# Create Neo4j LXC Container
#
# Description:
#   Creates a dedicated Neo4j graph database container.
#   Neo4j runs natively in this LXC (no Docker-in-LXC).
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, provision/pct/lib/functions.sh
#
# Usage:
#   bash provision/pct/containers/create-neo4j.sh [staging|production]
#
# Container Created:
#   - neo4j-lxc - Neo4j graph database
#
# Notes:
#   - Requires persistent storage mount for graph data
#   - Host paths must exist before running (host/setup-proxmox-host.sh)
#

set -euo pipefail

# Determine mode from argument
MODE="${1:-production}"

# Get script directory and source dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source configuration
if [[ "$MODE" == "staging" ]]; then
  echo "==> Creating Neo4j service in STAGING mode"
  source "${PCT_DIR}/stage-vars.env"
  PREFIX="${STAGE_PREFIX}"

  CT_NEO4J="${CT_NEO4J_STAGING}"
  IP_NEO4J="${IP_NEO4J_STAGING}"
else
  echo "==> Creating Neo4j service in PRODUCTION mode"
  source "${PCT_DIR}/vars.env"
  PREFIX=""
fi

# Source common functions
source "${PCT_DIR}/lib/functions.sh"

# Validate environment
validate_env || exit 1

# Track created containers for cleanup on error (same pattern as other create-* scripts)
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

# Environment-specific data directory base
if [[ "$MODE" == "staging" ]]; then
  DATA_BASE="/var/lib/data-staging"
else
  DATA_BASE="/var/lib/data"
fi

# Ensure host data directory exists for idempotent reruns.
# This avoids failing when setup-proxmox-host.sh was run before neo4j support existed.
mkdir -p "${DATA_BASE}/neo4j"

# Create Neo4j container
CT_EXISTED_BEFORE=false
if pct status "${CT_NEO4J}" &>/dev/null; then
  CT_EXISTED_BEFORE=true
fi

create_ct "${CT_NEO4J}" "${IP_NEO4J}" "${PREFIX}neo4j-lxc" unpriv || cleanup_on_error
if [[ "${CT_EXISTED_BEFORE}" == "false" ]]; then
  CREATED_CONTAINERS+=("${CT_NEO4J}")
fi
add_data_mount "${CT_NEO4J}" "${DATA_BASE}/neo4j" "/srv/neo4j/data" "0" || cleanup_on_error

echo ""
echo "=========================================="
echo "Neo4j service created successfully!"
echo "Mode: ${MODE}"
echo "Container:"
echo "  - ${PREFIX}neo4j-lxc: ${CT_NEO4J} @ ${IP_NEO4J}"
echo "=========================================="
echo ""
