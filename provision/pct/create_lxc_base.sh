#!/usr/bin/env bash
set -euo pipefail

# Determine which configuration to load based on argument
MODE="${1:-production}"

SCRIPT_DIR="$(dirname "$0")"

if [[ "$MODE" == "test" ]]; then
  echo "==> Running in TEST mode"
  source "${SCRIPT_DIR}/test-vars.env"
  print_test_config
else
  echo "==> Running in PRODUCTION mode"
  source "${SCRIPT_DIR}/vars.env"
fi

create_ct () {
  local CTID=$1 IP=$2 NAME=$3 PRIV=$4
  
  # Check if container already exists
  if pct status "$CTID" &>/dev/null; then
    echo "==> Container $NAME ($CTID) already exists"
    
    # Check if it's running
    if pct status "$CTID" | grep -q "running"; then
      echo "    Status: Running"
    else
      echo "    Status: Stopped - starting container"
      pct start "$CTID"
      sleep 3
    fi
    
    # Verify network configuration matches
    if ! pct config "$CTID" | grep -q "ip=${IP}"; then
      echo "    WARNING: Container exists but IP ($IP) may not match configuration"
      echo "    Current config:"
      pct config "$CTID" | grep "net0"
    fi
    
    return 0
  fi
  
  echo "==> Creating $NAME ($CTID) at $IP"
  
  # Build create command with proper privilege settings
  if [[ "$PRIV" == "priv" ]]; then
    # Create privileged container (unprivileged=0)
    pct create "$CTID" "$TEMPLATE" \
      -hostname "$NAME" \
      -net0 name=eth0,bridge=$BRIDGE,ip=${IP}${CIDR},gw=$GW \
      -storage "$STORAGE" \
      -memory "$MEM_MB" -cores "$CPUS" \
      -rootfs "$STORAGE:$DISK_GB" \
      -features nesting=1,keyctl=1 \
      -unprivileged 0 \
      -onboot 1 -start 1 \
      -ssh-public-keys "$SSH_PUBKEY_PATH" || return 1
  else
    # Create unprivileged container (default)
    pct create "$CTID" "$TEMPLATE" \
      -hostname "$NAME" \
      -net0 name=eth0,bridge=$BRIDGE,ip=${IP}${CIDR},gw=$GW \
      -storage "$STORAGE" \
      -memory "$MEM_MB" -cores "$CPUS" \
      -rootfs "$STORAGE:$DISK_GB" \
      -features nesting=1,keyctl=1 \
      -onboot 1 -start 1 \
      -ssh-public-keys "$SSH_PUBKEY_PATH" || return 1
  fi
  
  # /dev/net/tun
  echo "lxc.cgroup2.devices.allow: c 10:200 rwm" >> "/etc/pve/lxc/${CTID}.conf"
  echo "lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file" >> "/etc/pve/lxc/${CTID}.conf"
  
  pct start "$CTID"
  sleep 3
  pct exec "$CTID" -- bash -lc "apt update && apt install -y curl ca-certificates sudo gnupg ufw"
  
  echo "    Successfully created and started $NAME"
}

# Apply name prefix for test mode
PREFIX=""
if [[ "$MODE" == "test" ]]; then
  PREFIX="${TEST_PREFIX}"
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

# Create containers based on mode with error handling
if [[ "$MODE" == "test" ]]; then
  create_ct "$CT_PROXY_TEST" "$IP_PROXY_TEST" "${PREFIX}proxy-lxc" unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_PROXY_TEST")

  create_ct "$CT_APPS_TEST"   "$IP_APPS_TEST"   "${PREFIX}apps-lxc"   unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_APPS_TEST")

  create_ct "$CT_AGENT_TEST"  "$IP_AGENT_TEST"  "${PREFIX}agent-lxc"  unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_AGENT_TEST")
  
  create_ct "$CT_PG_TEST"     "$IP_PG_TEST"     "${PREFIX}pg-lxc"     unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_PG_TEST")
  
  create_ct "$CT_MILVUS_TEST" "$IP_MILVUS_TEST" "${PREFIX}milvus-lxc" priv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_MILVUS_TEST")
    
  create_ct "$CT_FILES_TEST"  "$IP_FILES_TEST"  "${PREFIX}files-lxc"  priv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_FILES_TEST")
  
  create_ct "$CT_INGEST_TEST" "$IP_INGEST_TEST" "${PREFIX}ingest-lxc" unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_INGEST_TEST")

else
  create_ct "$CT_PROXY" "$IP_PROXY" proxy-lxc unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_PROXY")

  create_ct "$CT_APPS"   "$IP_APPS"   apps-lxc   unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_APPS")

  create_ct "$CT_AGENT"  "$IP_AGENT"  agent-lxc  unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_AGENT")
  
  create_ct "$CT_PG"     "$IP_PG"     pg-lxc     unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_PG")
  
  create_ct "$CT_MILVUS" "$IP_MILVUS" milvus-lxc priv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_MILVUS")
  
  create_ct "$CT_FILES"  "$IP_FILES"  files-lxc  priv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_FILES")
  
  create_ct "$CT_INGEST" "$IP_INGEST" ingest-lxc unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_INGEST")

fi

echo ""
echo "=========================================="
echo "All containers created successfully!"
echo "Mode: ${MODE}"
echo "=========================================="
