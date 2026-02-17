#!/usr/bin/env bash
#
# Rebuild a single LXC container while preserving mounted data
#
# Description:
#   Safely destroys and recreates one Busibox LXC container, preserving host data
#   bind mounts. Runs in dry-run mode unless --confirm is provided.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, provision/pct/vars.env, provision/pct/stage-vars.env
#
# Usage:
#   bash provision/pct/containers/rebuild-container.sh <container-name> [staging|production] [--confirm]
#
# Examples:
#   bash provision/pct/containers/rebuild-container.sh pg-lxc production
#   bash provision/pct/containers/rebuild-container.sh pg-lxc staging --confirm
#   bash provision/pct/containers/rebuild-container.sh STAGE-pg-lxc --confirm
#
# Notes:
#   - Dry-run by default; no destructive actions without --confirm
#   - Verifies mount source directories exist and are non-empty before destroy
#   - Recreates the container by calling the corresponding create-*.sh script

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

usage() {
  echo "Usage: $0 <container-name> [staging|production] [--confirm]"
  echo ""
  echo "Examples:"
  echo "  bash $0 pg-lxc production"
  echo "  bash $0 pg-lxc staging --confirm"
  echo "  bash $0 STAGE-pg-lxc --confirm"
}

CONTAINER_NAME=""
MODE="production"
CONFIRM=false

for arg in "$@"; do
  case "$arg" in
    staging|production)
      MODE="$arg"
      ;;
    --confirm)
      CONFIRM=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$CONTAINER_NAME" ]]; then
        CONTAINER_NAME="$arg"
      else
        echo "ERROR: Unexpected argument: $arg"
        usage
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$CONTAINER_NAME" ]]; then
  usage
  exit 1
fi

if [[ "$MODE" == "staging" ]]; then
  source "${PCT_DIR}/stage-vars.env"
else
  source "${PCT_DIR}/vars.env"
fi

normalize_name() {
  local name="$1"
  name="${name#STAGE-}"
  name="${name#stage-}"
  echo "$name"
}

TARGET_NAME="$(normalize_name "$CONTAINER_NAME")"

CTID=""
CREATE_SCRIPT=""
SERVICE_HINT=""
STATEFUL=false

if [[ "$MODE" == "staging" ]]; then
  case "$TARGET_NAME" in
    proxy-lxc) CTID="$CT_PROXY_STAGING"; CREATE_SCRIPT="create-core-services.sh"; SERVICE_HINT="proxy" ;;
    core-apps-lxc) CTID="$CT_CORE_APPS_STAGING"; CREATE_SCRIPT="create-core-services.sh"; SERVICE_HINT="core-apps" ;;
    user-apps-lxc) CTID="$CT_USER_APPS_STAGING"; CREATE_SCRIPT="create-core-services.sh"; SERVICE_HINT="user-apps" ;;
    agent-lxc) CTID="$CT_AGENT_STAGING"; CREATE_SCRIPT="create-core-services.sh"; SERVICE_HINT="agent" ;;
    authz-lxc) CTID="$CT_AUTHZ_STAGING"; CREATE_SCRIPT="create-core-services.sh"; SERVICE_HINT="authz" ;;
    pg-lxc) CTID="$CT_PG_STAGING"; CREATE_SCRIPT="create-data-services.sh"; SERVICE_HINT="postgres"; STATEFUL=true ;;
    milvus-lxc) CTID="$CT_MILVUS_STAGING"; CREATE_SCRIPT="create-data-services.sh"; SERVICE_HINT="milvus"; STATEFUL=true ;;
    files-lxc) CTID="$CT_FILES_STAGING"; CREATE_SCRIPT="create-data-services.sh"; SERVICE_HINT="minio"; STATEFUL=true ;;
    neo4j-lxc) CTID="$CT_NEO4J_STAGING"; CREATE_SCRIPT="create-neo4j.sh"; SERVICE_HINT="neo4j"; STATEFUL=true ;;
    data-lxc) CTID="$CT_DATA_STAGING"; CREATE_SCRIPT="create-worker-services.sh"; SERVICE_HINT="data"; STATEFUL=true ;;
    litellm-lxc) CTID="$CT_LITELLM_STAGING"; CREATE_SCRIPT="create-worker-services.sh"; SERVICE_HINT="litellm" ;;
    bridge-lxc) CTID="$CT_BRIDGE_STAGING"; CREATE_SCRIPT="create-bridge.sh"; SERVICE_HINT="bridge" ;;
    vllm-lxc) CTID="$CT_VLLM_STAGING"; CREATE_SCRIPT="create-vllm.sh"; SERVICE_HINT="vllm" ;;
    ollama-lxc) CTID="$CT_OLLAMA_STAGING"; CREATE_SCRIPT="create-ollama.sh"; SERVICE_HINT="ollama" ;;
    *)
      echo "ERROR: Unsupported container name: $CONTAINER_NAME"
      exit 1
      ;;
  esac
else
  case "$TARGET_NAME" in
    proxy-lxc) CTID="$CT_PROXY"; CREATE_SCRIPT="create-core-services.sh"; SERVICE_HINT="proxy" ;;
    core-apps-lxc) CTID="$CT_CORE_APPS"; CREATE_SCRIPT="create-core-services.sh"; SERVICE_HINT="core-apps" ;;
    user-apps-lxc) CTID="$CT_USER_APPS"; CREATE_SCRIPT="create-core-services.sh"; SERVICE_HINT="user-apps" ;;
    agent-lxc) CTID="$CT_AGENT"; CREATE_SCRIPT="create-core-services.sh"; SERVICE_HINT="agent" ;;
    authz-lxc) CTID="$CT_AUTHZ"; CREATE_SCRIPT="create-core-services.sh"; SERVICE_HINT="authz" ;;
    pg-lxc) CTID="$CT_PG"; CREATE_SCRIPT="create-data-services.sh"; SERVICE_HINT="postgres"; STATEFUL=true ;;
    milvus-lxc) CTID="$CT_MILVUS"; CREATE_SCRIPT="create-data-services.sh"; SERVICE_HINT="milvus"; STATEFUL=true ;;
    files-lxc) CTID="$CT_FILES"; CREATE_SCRIPT="create-data-services.sh"; SERVICE_HINT="minio"; STATEFUL=true ;;
    neo4j-lxc) CTID="$CT_NEO4J"; CREATE_SCRIPT="create-neo4j.sh"; SERVICE_HINT="neo4j"; STATEFUL=true ;;
    data-lxc) CTID="$CT_DATA"; CREATE_SCRIPT="create-worker-services.sh"; SERVICE_HINT="data"; STATEFUL=true ;;
    litellm-lxc) CTID="$CT_LITELLM"; CREATE_SCRIPT="create-worker-services.sh"; SERVICE_HINT="litellm" ;;
    bridge-lxc) CTID="$CT_BRIDGE"; CREATE_SCRIPT="create-bridge.sh"; SERVICE_HINT="bridge" ;;
    vllm-lxc) CTID="$CT_VLLM"; CREATE_SCRIPT="create-vllm.sh"; SERVICE_HINT="vllm" ;;
    ollama-lxc) CTID="$CT_OLLAMA"; CREATE_SCRIPT="create-ollama.sh"; SERVICE_HINT="ollama" ;;
    *)
      echo "ERROR: Unsupported container name: $CONTAINER_NAME"
      exit 1
      ;;
  esac
fi

CONFIG_FILE="/etc/pve/lxc/${CTID}.conf"
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: Container config not found: $CONFIG_FILE"
  exit 1
fi

if ! pct status "$CTID" &>/dev/null; then
  echo "ERROR: Container CTID ${CTID} does not exist on this host"
  exit 1
fi

MOUNT_LINES="$(awk '/^mp[0-9]+:/{print}' "$CONFIG_FILE" || true)"

echo "=========================================="
echo "Container Rebuild Plan (Dry Run)"
echo "=========================================="
echo "Mode: ${MODE}"
echo "Container: ${TARGET_NAME}"
echo "CTID: ${CTID}"
echo "Create script: ${CREATE_SCRIPT}"
echo "Stateful: ${STATEFUL}"
echo ""

if [[ -n "$MOUNT_LINES" ]]; then
  echo "Current mount points:"
  echo "$MOUNT_LINES"
  echo ""
else
  echo "No mount points found in ${CONFIG_FILE}"
  echo ""
fi

if [[ "$STATEFUL" == true && -z "$MOUNT_LINES" ]]; then
  echo "ERROR: Refusing rebuild of stateful container without mount points."
  echo "Add persistent mounts first, then retry."
  exit 1
fi

while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  host_path="$(echo "$line" | sed -E 's/^mp[0-9]+:\s*([^,]+).*/\1/')"
  container_path="$(echo "$line" | sed -E 's/.*mp=([^,]+).*/\1/')"

  if [[ ! -d "$host_path" ]]; then
    echo "ERROR: Host mount path missing: ${host_path}"
    exit 1
  fi

  if [[ "$STATEFUL" == true && ( "$host_path" == /var/lib/data/* || "$host_path" == /var/lib/data-staging/* ) ]]; then
    if [[ -z "$(ls -A "$host_path")" ]]; then
      echo "ERROR: Stateful mount path is empty: ${host_path}"
      echo "Refusing rebuild to avoid accidental data reset."
      exit 1
    fi
  fi

  size="$(du -sh "$host_path" 2>/dev/null | awk '{print $1}')"
  echo "Verified mount: ${host_path} -> ${container_path} (size: ${size})"
done <<< "$MOUNT_LINES"

echo ""
if [[ "$CONFIRM" != true ]]; then
  echo "Dry-run complete. No changes made."
  echo "Re-run with --confirm to perform the rebuild."
  exit 0
fi

echo "==> Stopping container ${CTID}"
pct stop "$CTID" 2>/dev/null || true
sleep 2

echo "==> Destroying container ${CTID}"
pct destroy "$CTID" --purge

echo "==> Recreating container via ${CREATE_SCRIPT}"
bash "${SCRIPT_DIR}/${CREATE_SCRIPT}" "$MODE"

NEW_CONFIG_FILE="/etc/pve/lxc/${CTID}.conf"
if [[ ! -f "$NEW_CONFIG_FILE" ]]; then
  echo "ERROR: Recreated container config not found: ${NEW_CONFIG_FILE}"
  exit 1
fi

echo "==> Verifying mount points restored"
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  mp_id="$(echo "$line" | sed -E 's/^(mp[0-9]+):.*/\1/')"
  host_path="$(echo "$line" | sed -E 's/^mp[0-9]+:\s*([^,]+).*/\1/')"
  container_path="$(echo "$line" | sed -E 's/.*mp=([^,]+).*/\1/')"

  if ! grep -q "^${mp_id}: ${host_path},mp=${container_path}" "$NEW_CONFIG_FILE"; then
    echo "ERROR: Expected mount not present after rebuild: ${line}"
    exit 1
  fi
done <<< "$MOUNT_LINES"

echo ""
echo "=========================================="
echo "Rebuild completed successfully"
echo "=========================================="
echo "Container ${TARGET_NAME} (${CTID}) was rebuilt with mount verification."
echo ""
echo "Next step:"
echo "  From repo root, redeploy the service configuration:"
if [[ "$MODE" == "staging" ]]; then
  echo "  make install SERVICE=${SERVICE_HINT} INV=inventory/staging"
else
  echo "  make install SERVICE=${SERVICE_HINT}"
fi
echo ""
