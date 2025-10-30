#!/usr/bin/env bash
#
# Configure GPU Passthrough for LXC Containers
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Add NVIDIA GPU passthrough configuration to LXC containers
#
# USAGE:
#   bash configure-gpu-passthrough.sh <container-id> <gpu-number> [--force]
#
# EXAMPLES:
#   # Add GPU 0 to container 208 (ollama)
#   bash configure-gpu-passthrough.sh 208 0
#
#   # Add GPU 1 to container 209 (vLLM)
#   bash configure-gpu-passthrough.sh 209 1
#
#   # Force reconfiguration (removes old config first)
#   bash configure-gpu-passthrough.sh 100 0 --force
#
#   # Configure multiple containers
#   bash configure-gpu-passthrough.sh 208 0
#   bash configure-gpu-passthrough.sh 209 1
#   bash configure-gpu-passthrough.sh 210 0  # Share GPU 0
#
# REQUIREMENTS:
#   - NVIDIA drivers installed on Proxmox host
#   - Container must exist and be stopped (or use --force to auto-stop)
#   - GPU number must exist on host (check with: nvidia-smi -L)
#
# WHAT IT DOES:
#   1. Validates container and GPU exist
#   2. Backs up container configuration
#   3. Adds GPU device passthrough to container config
#   4. Optionally restarts container
#
# NOTES:
#   - Multiple containers can share the same GPU
#   - Container must install NVIDIA drivers after configuration
#   - Use --force to remove old GPU config and reconfigure
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

usage() {
    cat <<EOF
Usage: $0 <container-id> <gpu-number> [--force]

Configure NVIDIA GPU passthrough for an LXC container.

Arguments:
  container-id  LXC container ID (e.g., 208, 209, 100)
  gpu-number    GPU device number (0, 1, 2, etc. - check with: nvidia-smi -L)
  --force       Force reconfiguration (removes old GPU config first)

Examples:
  # Configure GPU 0 for ollama container
  $0 208 0

  # Configure GPU 1 for vLLM container
  $0 209 1

  # Share GPU 0 with multiple containers
  $0 208 0
  $0 210 0

  # Force reconfiguration
  $0 208 0 --force

After configuration:
  1. Start the container: pct start <container-id>
  2. Install NVIDIA drivers in container:
     ssh root@<container-ip>
     apt update && apt install -y nvidia-driver-535 nvidia-cuda-toolkit
  3. Verify: nvidia-smi
EOF
}

# Parse arguments
if [ $# -lt 2 ]; then
    error "Missing required arguments"
    echo
    usage
    exit 1
fi

CONTAINER_ID="$1"
GPU_NUMBER="$2"
FORCE_MODE=false

if [ "${3:-}" = "--force" ]; then
    FORCE_MODE=true
fi

# Validate container ID is numeric
if ! [[ "$CONTAINER_ID" =~ ^[0-9]+$ ]]; then
    error "Container ID must be numeric: $CONTAINER_ID"
    exit 1
fi

# Validate GPU number is numeric
if ! [[ "$GPU_NUMBER" =~ ^[0-9]+$ ]]; then
    error "GPU number must be numeric: $GPU_NUMBER"
    exit 1
fi

echo "=========================================="
echo "GPU Passthrough Configuration"
echo "=========================================="
info "Container: $CONTAINER_ID"
info "GPU: $GPU_NUMBER"
info "Force mode: $FORCE_MODE"
echo ""

# Verify container exists
if ! pct status "$CONTAINER_ID" &>/dev/null; then
    error "Container $CONTAINER_ID not found"
    echo "List available containers: pct list"
    exit 1
fi

# Verify nvidia-smi is available on host
if ! command -v nvidia-smi &>/dev/null; then
    error "nvidia-smi not found. Install NVIDIA drivers on the Proxmox host first."
    echo ""
    echo "Install NVIDIA drivers:"
    echo "  apt update"
    echo "  apt install -y nvidia-driver nvidia-smi"
    exit 1
fi

# Verify GPU exists on host
info "Checking GPU availability on host..."
if ! nvidia-smi -L | grep -q "GPU $GPU_NUMBER:"; then
    error "GPU $GPU_NUMBER not found on host"
    echo ""
    echo "Available GPUs:"
    nvidia-smi -L
    exit 1
fi

info "Found GPU $GPU_NUMBER on host:"
nvidia-smi -L | grep "GPU $GPU_NUMBER:"
echo ""

# Container config file
CONF_FILE="/etc/pve/lxc/${CONTAINER_ID}.conf"

# Check if container is running
CONTAINER_RUNNING=false
if pct status "$CONTAINER_ID" | grep -q "running"; then
    CONTAINER_RUNNING=true
    
    if [ "$FORCE_MODE" = true ]; then
        warn "Container is running, stopping for reconfiguration..."
        pct stop "$CONTAINER_ID"
        sleep 3
    else
        warn "Container is running. Stop it first or use --force flag."
        echo "  pct stop $CONTAINER_ID"
        exit 1
    fi
fi

# Check if GPU passthrough already configured
if grep -q "# GPU Passthrough" "$CONF_FILE" 2>/dev/null; then
    if [ "$FORCE_MODE" = true ]; then
        warn "Removing old GPU configuration..."
        
        # Backup original config
        backup_file="${CONF_FILE}.backup-$(date +%Y%m%d-%H%M%S)"
        cp "$CONF_FILE" "$backup_file"
        info "Backup saved: $backup_file"
        
        # Remove GPU-related lines
        sed -i '/^# GPU Passthrough/d' "$CONF_FILE"
        sed -i '/^lxc.cgroup2.devices.allow: c 195/d' "$CONF_FILE"
        sed -i '/^lxc.cgroup2.devices.allow: c 234/d' "$CONF_FILE"
        sed -i '/^lxc.cgroup2.devices.allow: c 508/d' "$CONF_FILE"
        sed -i '/^lxc.mount.entry:.*nvidia/d' "$CONF_FILE"
        
        success "Old GPU configuration removed"
    else
        error "Container already has GPU passthrough configured"
        echo ""
        echo "Current GPU configuration:"
        grep -A 6 "# GPU Passthrough" "$CONF_FILE"
        echo ""
        echo "Use --force to reconfigure:"
        echo "  $0 $CONTAINER_ID $GPU_NUMBER --force"
        exit 1
    fi
else
    # Backup config before first GPU configuration
    backup_file="${CONF_FILE}.backup-$(date +%Y%m%d-%H%M%S)"
    cp "$CONF_FILE" "$backup_file"
    info "Backup saved: $backup_file"
fi

# Add GPU passthrough configuration
info "Configuring GPU $GPU_NUMBER passthrough for container $CONTAINER_ID..."

cat >> "$CONF_FILE" << EOF
# GPU Passthrough: NVIDIA GPU ${GPU_NUMBER}
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
lxc.mount.entry: /dev/nvidia${GPU_NUMBER} dev/nvidia${GPU_NUMBER} none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps dev/nvidia-caps none bind,optional,create=dir
EOF

success "GPU passthrough configuration added"

# Display the configuration
info "Added configuration:"
echo "---"
tail -n 10 "$CONF_FILE"
echo "---"
echo ""

# Start container if it was running or if force mode
if [ "$CONTAINER_RUNNING" = true ] || [ "$FORCE_MODE" = true ]; then
    info "Starting container $CONTAINER_ID..."
    
    # Try systemctl first (avoids arch bug)
    if systemctl start "pve-container@${CONTAINER_ID}" 2>/dev/null; then
        success "Container started via systemctl"
    elif lxc-start -n "$CONTAINER_ID" 2>/dev/null; then
        success "Container started via lxc-start"
    else
        warn "Failed to start container automatically"
        echo "Start manually: pct start $CONTAINER_ID"
    fi
    
    sleep 3
    
    # Verify container is running
    if pct status "$CONTAINER_ID" | grep -q "running"; then
        success "Container $CONTAINER_ID is running"
        
        # Try to verify GPU devices in container
        info "Verifying GPU devices in container..."
        if pct exec "$CONTAINER_ID" -- ls -la /dev/nvidia* 2>/dev/null; then
            success "GPU devices are visible in container"
        else
            warn "Could not verify GPU devices (container may need NVIDIA drivers)"
        fi
    else
        warn "Container may not be running - check status: pct status $CONTAINER_ID"
    fi
else
    info "Container is stopped. Start it when ready:"
    echo "  pct start $CONTAINER_ID"
fi

echo ""
echo "=========================================="
success "GPU Passthrough Configured!"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Verify container is running:"
echo "   pct status $CONTAINER_ID"
echo ""
echo "2. Install NVIDIA drivers in the container:"
echo "   pct enter $CONTAINER_ID"
echo "   apt update && apt install -y nvidia-driver-535 nvidia-cuda-toolkit"
echo ""
echo "3. Verify GPU is accessible:"
echo "   nvidia-smi"
echo ""
echo "4. If GPU is not visible, check host GPU devices:"
echo "   ls -la /dev/nvidia*"
echo ""
echo "5. View container config:"
echo "   cat /etc/pve/lxc/${CONTAINER_ID}.conf"
echo ""

