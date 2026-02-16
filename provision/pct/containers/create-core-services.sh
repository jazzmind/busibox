#!/usr/bin/env bash
#
# Create Core Services LXC Containers
#
# Description:
#   Creates core infrastructure containers: proxy, core-apps, user-apps, agent, and authz.
#   These are the main application containers for the Busibox platform.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, provision/pct/lib/functions.sh
#
# Usage:
#   bash provision/pct/containers/create-core-services.sh [staging|production]
#
# Containers Created:
#   - proxy-lxc      - nginx reverse proxy (main entry point)
#   - core-apps-lxc  - Core Next.js applications (busibox-portal, busibox-agents)
#   - user-apps-lxc  - External/user-deployed applications
#   - agent-lxc      - Agent API server
#   - authz-lxc      - Authorization service (RLS token issuer)

set -euo pipefail

# Determine mode from argument
MODE="${1:-production}"

# Get script directory and source dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source configuration
if [[ "$MODE" == "staging" ]]; then
  echo "==> Creating core services in STAGING mode"
  source "${PCT_DIR}/stage-vars.env"
  PREFIX="${STAGE_PREFIX}"
  
  CT_PROXY="$CT_PROXY_STAGING"
  CT_CORE_APPS="$CT_CORE_APPS_STAGING"
  CT_USER_APPS="$CT_USER_APPS_STAGING"
  CT_AGENT="$CT_AGENT_STAGING"
  CT_AUTHZ="$CT_AUTHZ_STAGING"
  
  IP_PROXY="$IP_PROXY_STAGING"
  IP_CORE_APPS="$IP_CORE_APPS_STAGING"
  IP_USER_APPS="$IP_USER_APPS_STAGING"
  IP_AGENT="$IP_AGENT_STAGING"
  IP_AUTHZ="$IP_AUTHZ_STAGING"
else
  echo "==> Creating core services in PRODUCTION mode"
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

# Create proxy container
create_ct "$CT_PROXY" "$IP_PROXY" "${PREFIX}proxy-lxc" unpriv || cleanup_on_error
CREATED_CONTAINERS+=("$CT_PROXY")

# Create core-apps container (busibox-portal, busibox-agents)
create_ct "$CT_CORE_APPS" "$IP_CORE_APPS" "${PREFIX}core-apps-lxc" unpriv || cleanup_on_error
CREATED_CONTAINERS+=("$CT_CORE_APPS")

# Create user-apps container (external/user-deployed apps)
create_ct "$CT_USER_APPS" "$IP_USER_APPS" "${PREFIX}user-apps-lxc" unpriv || cleanup_on_error
CREATED_CONTAINERS+=("$CT_USER_APPS")

# Create agent container
create_ct "$CT_AGENT" "$IP_AGENT" "${PREFIX}agent-lxc" unpriv || cleanup_on_error
CREATED_CONTAINERS+=("$CT_AGENT")

# Create authz container
create_ct "$CT_AUTHZ" "$IP_AUTHZ" "${PREFIX}authz-lxc" unpriv || cleanup_on_error
CREATED_CONTAINERS+=("$CT_AUTHZ")

echo ""
echo "=========================================="
echo "Core services created successfully!"
echo "Mode: ${MODE}"
echo "Containers:"
echo "  - ${PREFIX}proxy-lxc:      $CT_PROXY @ $IP_PROXY"
echo "  - ${PREFIX}core-apps-lxc:  $CT_CORE_APPS @ $IP_CORE_APPS"
echo "  - ${PREFIX}user-apps-lxc:  $CT_USER_APPS @ $IP_USER_APPS"
echo "  - ${PREFIX}agent-lxc:      $CT_AGENT @ $IP_AGENT"
echo "  - ${PREFIX}authz-lxc:      $CT_AUTHZ @ $IP_AUTHZ"
echo "=========================================="

