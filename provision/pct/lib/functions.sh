#!/usr/bin/env bash
#
# Shared Functions for LXC Container Management
#
# Description:
#   Common functions used across container creation scripts for Busibox infrastructure.
#   These functions provide reusable logic for container creation, data mounts, and GPU passthrough.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, nvidia-smi (for GPU passthrough)
#
# Functions:
#   create_ct()          - Create and start an LXC container
#   add_data_mount()     - Add persistent storage bind mount to container
#   add_gpu_passthrough() - Configure single NVIDIA GPU passthrough for container
#   add_all_gpus()       - Pass through all available NVIDIA GPUs to container
#   add_gpus()           - Pass through multiple specific NVIDIA GPUs to container
#
# Usage:
#   source "$(dirname "$0")/lib/functions.sh"

# Create an LXC container with standard configuration
# Args: CTID IP NAME PRIVILEGE [DISK_SIZE]
create_ct() {
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
  
  # Add /dev/net/tun support for VPN/tunneling
  echo "lxc.cgroup2.devices.allow: c 10:200 rwm" >> "/etc/pve/lxc/${CTID}.conf"
  echo "lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file" >> "/etc/pve/lxc/${CTID}.conf"
  
  pct start "$CTID"
  sleep 3
  pct exec "$CTID" -- bash -lc "apt update && apt install -y curl ca-certificates sudo gnupg ufw"
  
  echo "    Successfully created and started $NAME"
}

# Add persistent storage bind mount to container
# Args: CTID HOST_PATH CONTAINER_PATH [MP_NUM]
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

# Configure single NVIDIA GPU passthrough for container
# Args: CTID GPU_NUM
add_gpu_passthrough() {
  local CTID=$1
  local GPU_NUM="${2:-0}"
  
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

# Pass through ALL available NVIDIA GPUs to container
# Args: CTID
add_all_gpus() {
  local CTID=$1
  
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
  
  # Get list of available GPUs
  local GPU_COUNT=$(nvidia-smi -L | wc -l)
  
  if [[ "$GPU_COUNT" -eq 0 ]]; then
    echo "    WARNING: No NVIDIA GPUs detected on host"
    return 1
  fi
  
  echo "    Configuring ALL GPUs (${GPU_COUNT} detected) for passthrough..."
  
  # Add common GPU device permissions
  cat >> "$CONFIG_FILE" << EOF
# GPU Passthrough: ALL NVIDIA GPUs (${GPU_COUNT} total)
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps dev/nvidia-caps none bind,optional,create=dir
EOF
  
  # Add each GPU device
  for ((i=0; i<GPU_COUNT; i++)); do
    echo "lxc.mount.entry: /dev/nvidia${i} dev/nvidia${i} none bind,optional,create=file" >> "$CONFIG_FILE"
    echo "    Added GPU ${i}"
  done
  
  echo "    Successfully configured ${GPU_COUNT} GPUs for passthrough"
  
  return 0
}

# Pass through multiple specific NVIDIA GPUs to container
# Args: CTID GPU_NUMBERS (comma-separated, e.g., "1,2,3" or "1-3")
# Example: add_gpus 208 "1,2,3"  # Add GPUs 1, 2, and 3 to container 208
add_gpus() {
  local CTID=$1
  local GPU_SPEC="$2"
  
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
  
  # Parse GPU specification (supports: 1, 1,2,3, or 1-3)
  local GPU_NUMBERS=()
  
  if [[ "$GPU_SPEC" =~ ^[0-9]+-[0-9]+$ ]]; then
    # Range format: 1-3
    IFS='-' read -r START END <<< "$GPU_SPEC"
    for ((i=START; i<=END; i++)); do
      GPU_NUMBERS+=("$i")
    done
  elif [[ "$GPU_SPEC" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    # Comma-separated format: 1,2,3
    IFS=',' read -ra GPU_NUMBERS <<< "$GPU_SPEC"
  else
    echo "    ERROR: Invalid GPU specification: $GPU_SPEC"
    echo "    Valid formats: 1 (single), 1,2,3 (multiple), 1-3 (range)"
    return 1
  fi
  
  # Validate all GPUs exist
  for gpu_num in "${GPU_NUMBERS[@]}"; do
    if ! nvidia-smi -L | grep -q "GPU ${gpu_num}:"; then
      echo "    WARNING: GPU ${gpu_num} not found on host"
      echo "    Available GPUs:"
      nvidia-smi -L 2>/dev/null || echo "      None detected"
      return 1
    fi
  done
  
  echo "    Configuring GPUs ${GPU_NUMBERS[*]} for passthrough..."
  
  # Add common GPU device permissions
  cat >> "$CONFIG_FILE" << EOF
# GPU Passthrough: NVIDIA GPUs ${GPU_NUMBERS[*]}
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps dev/nvidia-caps none bind,optional,create=dir
EOF
  
  # Add each specified GPU device
  for gpu_num in "${GPU_NUMBERS[@]}"; do
    echo "lxc.mount.entry: /dev/nvidia${gpu_num} dev/nvidia${gpu_num} none bind,optional,create=file" >> "$CONFIG_FILE"
    echo "    Added GPU ${gpu_num}"
  done
  
  echo "    Successfully configured ${#GPU_NUMBERS[@]} GPUs for passthrough"
  
  return 0
}

# Validate required environment variables are set
validate_env() {
  local required_vars=(
    "BRIDGE" "CIDR" "GW" 
    "TEMPLATE" "STORAGE" "SSH_PUBKEY_PATH"
    "MEM_MB" "CPUS" "DISK_GB"
  )
  
  local missing_vars=()
  
  for var in "${required_vars[@]}"; do
    if [[ -z "${!var:-}" ]]; then
      missing_vars+=("$var")
    fi
  done
  
  if [[ ${#missing_vars[@]} -gt 0 ]]; then
    echo "ERROR: Missing required environment variables:"
    printf '  - %s\n' "${missing_vars[@]}"
    echo ""
    echo "Please source vars.env or test-vars.env before running this script"
    return 1
  fi
  
  return 0
}

