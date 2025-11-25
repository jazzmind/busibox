#!/usr/bin/env bash
#
# Configure GPU Passthrough for LXC Containers
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Pass ALL GPUs to container and ensure drivers are installed
#
# This script:
# 1. Passes ALL available GPUs to the specified container
# 2. Checks if NVIDIA drivers are installed in the container
# 3. Installs drivers if missing (CUDA toolkit)
#
# USAGE:
#   bash configure-container-gpus.sh <container-id> [--skip-driver-install]
#
# EXAMPLES:
#   # Configure GPUs for ingest container (206)
#   bash configure-container-gpus.sh 206
#
#   # Configure GPUs but skip driver installation
#   bash configure-container-gpus.sh 207 --skip-driver-install
#
# REQUIREMENTS:
#   - NVIDIA drivers installed on Proxmox host
#   - Container must exist
#   - Container will be stopped/started during configuration
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
Usage: $0 <container-id> [--skip-driver-install]

Configure GPU passthrough for an LXC container.

Arguments:
  container-id          LXC container ID (e.g., 206, 207, 208)
  --skip-driver-install Skip driver installation in container

This script will:
  1. Pass ALL available GPUs to the container
  2. Check if NVIDIA drivers are installed in container
  3. Install CUDA toolkit if missing (unless --skip-driver-install)

Examples:
  # Configure GPUs for ingest container
  $0 206

  # Configure GPUs but skip driver installation
  $0 207 --skip-driver-install

EOF
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root"
    exit 1
fi

# Parse arguments
CONTAINER_ID=""
SKIP_DRIVER_INSTALL=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-driver-install)
            SKIP_DRIVER_INSTALL=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        [0-9]*)
            CONTAINER_ID="$1"
            shift
            ;;
        *)
            error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

if [[ -z "$CONTAINER_ID" ]]; then
    error "Container ID is required"
    usage
    exit 1
fi

# Check if container exists
if ! pct status "$CONTAINER_ID" &>/dev/null; then
    error "Container $CONTAINER_ID does not exist"
    exit 1
fi

# Check if nvidia-smi is available on host
if ! command -v nvidia-smi &>/dev/null; then
    error "nvidia-smi not found on host"
    echo "Run: bash provision/pct/host/setup-proxmox-host.sh to install NVIDIA drivers"
    exit 1
fi

# Get GPU count
GPU_COUNT=$(nvidia-smi -L | wc -l)
if [[ "$GPU_COUNT" -eq 0 ]]; then
    error "No NVIDIA GPUs detected on host"
    exit 1
fi

info "Detected $GPU_COUNT GPU(s) on host"

# Get container config file
CONFIG_FILE="/etc/pve/lxc/${CONTAINER_ID}.conf"

# Stop container if running
if pct status "$CONTAINER_ID" | grep -q "running"; then
    info "Stopping container $CONTAINER_ID..."
    pct stop "$CONTAINER_ID"
    sleep 2
fi

# Check if GPU passthrough already configured
if grep -q "# GPU Passthrough" "$CONFIG_FILE"; then
    # Count how many GPUs are currently configured
    configured_gpus=$(grep -c "^lxc.mount.entry: /dev/nvidia[0-9]" "$CONFIG_FILE" || echo "0")
    
    # Check if all GPUs are already configured
    if [[ "$configured_gpus" -eq "$GPU_COUNT" ]]; then
        success "All $GPU_COUNT GPU(s) already configured for container $CONTAINER_ID"
        info "Skipping GPU passthrough configuration (already complete)"
    else
        warn "GPU passthrough partially configured ($configured_gpus/$GPU_COUNT GPUs)"
        warn "Container currently has access to $configured_gpus GPU(s), but $GPU_COUNT are available"
        echo ""
        read -p "Reconfigure to add all GPUs? (Y/n): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Nn]$ ]]; then
            info "Skipping GPU passthrough reconfiguration"
        else
            # Remove old GPU configuration
            info "Removing old GPU configuration..."
            backup_file="${CONFIG_FILE}.backup-$(date +%Y%m%d-%H%M%S)"
            cp "$CONFIG_FILE" "$backup_file"
            info "Backup saved: $backup_file"
            
            sed -i '/^# GPU Passthrough/d' "$CONFIG_FILE"
            sed -i '/^lxc.cgroup2.devices.allow: c 195/d' "$CONFIG_FILE"
            sed -i '/^lxc.cgroup2.devices.allow: c 234/d' "$CONFIG_FILE"
            sed -i '/^lxc.cgroup2.devices.allow: c 508/d' "$CONFIG_FILE"
            sed -i '/^lxc.mount.entry:.*nvidia/d' "$CONFIG_FILE"
        fi
    fi
fi

# Add GPU passthrough configuration (all GPUs)
if ! grep -q "# GPU Passthrough" "$CONFIG_FILE"; then
    info "Configuring GPU passthrough for ALL GPUs..."
    
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
    done
    
    success "GPU passthrough configured for all $GPU_COUNT GPU(s)"
fi

# Start container
info "Starting container $CONTAINER_ID..."
pct start "$CONTAINER_ID"
sleep 3

# Check if container is running
if ! pct status "$CONTAINER_ID" | grep -q "running"; then
    error "Failed to start container $CONTAINER_ID"
    exit 1
fi

# Get container IP
CONTAINER_IP=$(pct config "$CONTAINER_ID" | grep "ip=" | awk -F'=' '{print $2}' | awk -F'/' '{print $1}')

if [[ -z "$CONTAINER_IP" ]]; then
    warn "Could not determine container IP, skipping driver check"
    exit 0
fi

# Check if drivers are installed in container
if [[ "$SKIP_DRIVER_INSTALL" == "true" ]]; then
    info "Skipping driver installation (--skip-driver-install)"
    success "GPU passthrough configured. Drivers must be installed manually in container."
    exit 0
fi

info "Checking NVIDIA drivers in container..."

# Check if nvidia-smi works in container
if pct exec "$CONTAINER_ID" -- nvidia-smi &>/dev/null; then
    success "NVIDIA drivers already installed in container"
    pct exec "$CONTAINER_ID" -- nvidia-smi -L
    exit 0
fi

# Drivers not installed - install them using install-nvidia-drivers.sh
warn "NVIDIA drivers not found in container"
info "Installing NVIDIA drivers matching host version..."

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_SCRIPT="${SCRIPT_DIR}/install-nvidia-drivers.sh"

# Check if install-nvidia-drivers.sh exists
if [ ! -f "$INSTALL_SCRIPT" ]; then
    error "install-nvidia-drivers.sh not found at $INSTALL_SCRIPT"
    echo ""
    echo "This script is required to install drivers matching the host version."
    echo "It prevents 'Driver/library version mismatch' errors."
    exit 1
fi

# Make sure script is executable
chmod +x "$INSTALL_SCRIPT"

# Run install-nvidia-drivers.sh to install matching driver version
info "Running install-nvidia-drivers.sh to match host driver version..."
if bash "$INSTALL_SCRIPT" "$CONTAINER_ID"; then
    success "NVIDIA drivers installed successfully (matching host version)"
    pct exec "$CONTAINER_ID" -- nvidia-smi -L
else
    error "Failed to install NVIDIA drivers"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check host has NVIDIA drivers: nvidia-smi"
    echo "  2. Check container has GPU passthrough configured"
    echo "  3. Try manual installation: bash $INSTALL_SCRIPT $CONTAINER_ID"
    exit 1
fi

success "GPU configuration complete for container $CONTAINER_ID"
info "All $GPU_COUNT GPU(s) are now available in the container"
info "Container can use CUDA_VISIBLE_DEVICES to select specific GPUs"

