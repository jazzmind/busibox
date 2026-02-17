#!/usr/bin/env bash
#
# Rebuild all staging LXC containers while preserving stateful data
#
# Description:
#   Performs a clean staging reinstall by destroying and recreating all staging
#   containers. Stateful service data is preserved on host bind mounts under
#   /var/lib/data-staging.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, provision/pct/stage-vars.env, provision/pct/containers/create_lxc_base.sh
#
# Usage:
#   bash provision/pct/containers/rebuild-staging.sh [--with-ollama] [--confirm]
#
# Notes:
#   - Dry-run by default; no destructive action unless --confirm is provided.
#   - Verifies required persistent data directories exist and are non-empty.
#   - Verifies bind mounts after rebuild for postgres/milvus/minio/neo4j/data.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

source "${PCT_DIR}/stage-vars.env"

WITH_OLLAMA=false
CONFIRM=false

for arg in "$@"; do
  case "$arg" in
    --with-ollama)
      WITH_OLLAMA=true
      ;;
    --confirm)
      CONFIRM=true
      ;;
    -h|--help)
      echo "Usage: $0 [--with-ollama] [--confirm]"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: ${arg}"
      echo "Usage: $0 [--with-ollama] [--confirm]"
      exit 1
      ;;
  esac
done

STATEFUL_DIRS=(
  "/var/lib/data-staging/postgres"
  "/var/lib/data-staging/redis"
  "/var/lib/data-staging/milvus"
  "/var/lib/data-staging/minio"
  "/var/lib/data-staging/neo4j"
)

STAGING_CTIDS=(
  "$CT_PROXY_STAGING"
  "$CT_CORE_APPS_STAGING"
  "$CT_AGENT_STAGING"
  "$CT_PG_STAGING"
  "$CT_MILVUS_STAGING"
  "$CT_FILES_STAGING"
  "$CT_DATA_STAGING"
  "$CT_LITELLM_STAGING"
  "$CT_VLLM_STAGING"
  "$CT_AUTHZ_STAGING"
  "$CT_BRIDGE_STAGING"
  "$CT_USER_APPS_STAGING"
  "$CT_NEO4J_STAGING"
)

if [[ "$WITH_OLLAMA" == true ]]; then
  STAGING_CTIDS+=("$CT_OLLAMA_STAGING")
fi

echo "=========================================="
echo "Staging Clean Reinstall (Preserve Data)"
echo "=========================================="
echo "Mode: staging"
echo "Ollama included: ${WITH_OLLAMA}"
echo ""

echo "Validating persistent staging data directories..."
for dir in "${STATEFUL_DIRS[@]}"; do
  if [[ ! -d "$dir" ]]; then
    echo "ERROR: Missing required directory: ${dir}"
    echo "Run: bash provision/pct/host/setup-proxmox-host.sh"
    exit 1
  fi

  if [[ -z "$(ls -A "$dir")" ]]; then
    echo "ERROR: Directory exists but is empty: ${dir}"
    echo "Refusing clean reinstall to avoid accidental data reset."
    exit 1
  fi

  size="$(du -sh "$dir" 2>/dev/null | awk '{print $1}')"
  echo "  OK: ${dir} (size: ${size})"
done

echo ""
echo "Containers targeted for clean reinstall:"
for ctid in "${STAGING_CTIDS[@]}"; do
  if pct status "$ctid" &>/dev/null; then
    status="$(pct status "$ctid" | awk '{print $2}')"
    echo "  - ${ctid} (${status})"
  else
    echo "  - ${ctid} (not present)"
  fi
done

echo ""
if [[ "$CONFIRM" != true ]]; then
  echo "Dry-run complete. No containers were destroyed."
  echo "Re-run with --confirm to perform clean reinstall."
  exit 0
fi

echo "==> Stopping and destroying staging containers"
for ctid in "${STAGING_CTIDS[@]}"; do
  if pct status "$ctid" &>/dev/null; then
    echo "  Removing container ${ctid}"
    pct stop "$ctid" 2>/dev/null || true
    sleep 1
    pct destroy "$ctid" --purge
  fi
done

echo ""
echo "==> Recreating staging containers"
if [[ "$WITH_OLLAMA" == true ]]; then
  bash "${SCRIPT_DIR}/create_lxc_base.sh" staging --with-ollama
else
  bash "${SCRIPT_DIR}/create_lxc_base.sh" staging
fi

echo ""
echo "==> Verifying stateful bind mounts on rebuilt containers"
verify_mount() {
  local ctid="$1"
  local host_path="$2"
  local container_path="$3"

  local config_file="/etc/pve/lxc/${ctid}.conf"
  if ! grep -q "mp[0-9]: ${host_path},mp=${container_path}" "$config_file"; then
    echo "ERROR: Missing expected mount on CT ${ctid}: ${host_path} -> ${container_path}"
    exit 1
  fi
  echo "  OK: CT ${ctid} mount ${host_path} -> ${container_path}"
}

verify_mount "$CT_PG_STAGING" "/var/lib/data-staging/postgres" "/var/lib/postgresql/data"
verify_mount "$CT_MILVUS_STAGING" "/var/lib/data-staging/milvus" "/srv/milvus/data"
verify_mount "$CT_FILES_STAGING" "/var/lib/data-staging/minio" "/srv/minio/data"
verify_mount "$CT_NEO4J_STAGING" "/var/lib/data-staging/neo4j" "/srv/neo4j/data"
verify_mount "$CT_DATA_STAGING" "/var/lib/data-staging/redis" "/var/lib/redis"

echo ""
echo "=========================================="
echo "Staging clean reinstall completed"
echo "=========================================="
echo "Data for postgres/milvus/minio/neo4j/redis remained on host data-staging mounts."
echo ""
echo "Next step from repo root:"
echo "  make install SERVICE=all INV=inventory/staging"
echo ""
