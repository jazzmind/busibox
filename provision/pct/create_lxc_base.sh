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
  local CTID=$1 IP=$2 NAME=$3 PRIV=$4 DISK_SIZE="${5:-$DISK_GB}"
  
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
  
  echo "==> Creating $NAME ($CTID) at $IP (disk: ${DISK_SIZE})"
  
  # Build create command with proper privilege settings
  if [[ "$PRIV" == "priv" ]]; then
    # Create privileged container (unprivileged=0)
    pct create "$CTID" "$TEMPLATE" \
      -hostname "$NAME" \
      -net0 name=eth0,bridge=$BRIDGE,ip=${IP}${CIDR},gw=$GW \
      -storage "$STORAGE" \
      -memory "$MEM_MB" -cores "$CPUS" \
      -rootfs "$STORAGE:$DISK_SIZE" \
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
      -rootfs "$STORAGE:$DISK_SIZE" \
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

# Add data mount to container (automatically adds persistent storage bind mounts)
add_data_mount() {
  local CTID=$1
  local HOST_PATH=$2
  local CONTAINER_PATH=$3
  local MP_NUM="${4:-0}"
  
  local CONFIG_FILE="/etc/pve/lxc/${CTID}.conf"
  
  # Check if mount already exists
  if grep -q "mp${MP_NUM}:" "$CONFIG_FILE"; then
    echo "    Data mount already configured (mp${MP_NUM})"
    return 0
  fi
  
  # Check if host path exists
  if [[ ! -d "$HOST_PATH" ]]; then
    echo "    WARNING: Host path $HOST_PATH does not exist"
    echo "    Run: bash provision/pct/setup-proxmox-host.sh first"
    return 1
  fi
  
  # Add mount point with proper options
  echo "mp${MP_NUM}: ${HOST_PATH},mp=${CONTAINER_PATH},backup=0,replicate=0" >> "$CONFIG_FILE"
  echo "    Added data mount: ${HOST_PATH} -> ${CONTAINER_PATH}"
  
  return 0
}

# Add GPU passthrough to container (automatically configures NVIDIA GPU access)
add_gpu_passthrough() {
  local CTID=$1
  local GPU_NUM="${2:-1}"  # Default to GPU 1 for test, GPU 0 for production
  
  local CONFIG_FILE="/etc/pve/lxc/${CTID}.conf"
  
  # Check if GPU passthrough already configured
  if grep -q "# GPU Passthrough" "$CONFIG_FILE"; then
    echo "    GPU passthrough already configured"
    return 0
  fi
  
  # Check if nvidia-smi is available on host
  if ! command -v nvidia-smi &>/dev/null; then
    echo "    WARNING: nvidia-smi not found on host"
    echo "    Run: bash provision/pct/setup-proxmox-host.sh to install NVIDIA drivers"
    return 1
  fi
  
  # Check if GPU exists on host
  if ! nvidia-smi -L | grep -q "GPU ${GPU_NUM}:"; then
    echo "    WARNING: GPU ${GPU_NUM} not found on host"
    echo "    Available GPUs:"
    nvidia-smi -L 2>/dev/null || echo "      None detected"
    return 1
  fi
  
  echo "    Configuring GPU ${GPU_NUM} passthrough..."
  
  # Add GPU passthrough configuration
  cat >> "$CONFIG_FILE" << EOF
# GPU Passthrough: NVIDIA GPU ${GPU_NUM}
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
lxc.mount.entry: /dev/nvidia${GPU_NUM} dev/nvidia${GPU_NUM} none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps dev/nvidia-caps none bind,optional,create=dir
EOF
  
  echo "    Added GPU ${GPU_NUM} passthrough"
  
  return 0
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
  add_data_mount "$CT_PG_TEST" "/var/lib/data/postgres" "/var/lib/postgresql/data" "0"
  
  create_ct "$CT_MILVUS_TEST" "$IP_MILVUS_TEST" "${PREFIX}milvus-lxc" priv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_MILVUS_TEST")
  add_data_mount "$CT_MILVUS_TEST" "/var/lib/data/milvus" "/srv/milvus/data" "0"
    
  create_ct "$CT_FILES_TEST"  "$IP_FILES_TEST"  "${PREFIX}files-lxc"  priv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_FILES_TEST")
  add_data_mount "$CT_FILES_TEST" "/var/lib/data/minio" "/srv/minio/data" "0"
  
  create_ct "$CT_INGEST_TEST" "$IP_INGEST_TEST" "${PREFIX}ingest-lxc" unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_INGEST_TEST")
  
  create_ct "$CT_LITELLM_TEST" "$IP_LITELLM_TEST" "${PREFIX}litellm-lxc" unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_LITELLM_TEST")
  
  create_ct "$CT_OLLAMA_TEST" "$IP_OLLAMA_TEST" "${PREFIX}ollama-lxc" priv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_OLLAMA_TEST")
  add_data_mount "$CT_OLLAMA_TEST" "/var/lib/llm-models/ollama" "/var/lib/llm-models/ollama" "0"
  pct stop "$CT_OLLAMA_TEST" || true
  add_gpu_passthrough "$CT_OLLAMA_TEST" "0"  # GPU 0 for Ollama
  pct start "$CT_OLLAMA_TEST" || true
  
  create_ct "$CT_VLLM_TEST" "$IP_VLLM_TEST" "${PREFIX}vllm-lxc" priv 40 || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_VLLM_TEST")
  add_data_mount "$CT_VLLM_TEST" "/var/lib/llm-models/huggingface" "/var/lib/llm-models/huggingface" "0"
  pct stop "$CT_VLLM_TEST" || true
  add_gpu_passthrough "$CT_VLLM_TEST" "1"  # GPU 1 for vLLM
  pct start "$CT_VLLM_TEST" || true

else
  create_ct "$CT_PROXY" "$IP_PROXY" proxy-lxc unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_PROXY")

  create_ct "$CT_APPS"   "$IP_APPS"   apps-lxc   unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_APPS")

  create_ct "$CT_AGENT"  "$IP_AGENT"  agent-lxc  unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_AGENT")
  
  create_ct "$CT_PG"     "$IP_PG"     pg-lxc     unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_PG")
  add_data_mount "$CT_PG" "/var/lib/data/postgres" "/var/lib/postgresql/data" "0"
  
  create_ct "$CT_MILVUS" "$IP_MILVUS" milvus-lxc priv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_MILVUS")
  add_data_mount "$CT_MILVUS" "/var/lib/data/milvus" "/srv/milvus/data" "0"
  
  create_ct "$CT_FILES"  "$IP_FILES"  files-lxc  priv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_FILES")
  add_data_mount "$CT_FILES" "/var/lib/data/minio" "/srv/minio/data" "0"
  
  create_ct "$CT_INGEST" "$IP_INGEST" ingest-lxc unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_INGEST")
  
  create_ct "$CT_LITELLM" "$IP_LITELLM" litellm-lxc unpriv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_LITELLM")
  
  create_ct "$CT_OLLAMA" "$IP_OLLAMA" ollama-lxc priv || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_OLLAMA")
  add_data_mount "$CT_OLLAMA" "/var/lib/llm-models/ollama" "/var/lib/llm-models/ollama" "0"
  pct stop "$CT_OLLAMA" || true
  add_gpu_passthrough "$CT_OLLAMA" "0"  # GPU 0 for Ollama
  pct start "$CT_OLLAMA" || true
  
  create_ct "$CT_VLLM" "$IP_VLLM" vllm-lxc priv 40 || cleanup_on_error
  CREATED_CONTAINERS+=("$CT_VLLM")
  add_data_mount "$CT_VLLM" "/var/lib/llm-models/huggingface" "/var/lib/llm-models/huggingface" "0"
  pct stop "$CT_VLLM" || true
  add_gpu_passthrough "$CT_VLLM" "0"  # GPU 0 for vLLM (production uses same GPU)
  pct start "$CT_VLLM" || true

fi

echo ""
echo "=========================================="
echo "All containers created successfully!"
echo "Mode: ${MODE}"
echo "=========================================="
