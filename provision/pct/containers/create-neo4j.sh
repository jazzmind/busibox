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

# Environment-specific data directory base
if [[ "$MODE" == "staging" ]]; then
  DATA_BASE="/var/lib/data-staging"
else
  DATA_BASE="/var/lib/data"
fi

# Create Neo4j container
create_ct "${CT_NEO4J}" "${IP_NEO4J}" "${PREFIX}neo4j-lxc" unpriv
add_data_mount "${CT_NEO4J}" "${DATA_BASE}/neo4j" "/srv/neo4j/data" "0"

echo ""
echo "=========================================="
echo "Neo4j service created successfully!"
echo "Mode: ${MODE}"
echo "Container:"
echo "  - ${PREFIX}neo4j-lxc: ${CT_NEO4J} @ ${IP_NEO4J}"
echo "=========================================="
echo ""
