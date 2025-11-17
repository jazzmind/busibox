#!/usr/bin/env bash
#
# Configure GPU Passthrough for LXC Containers
#
# NOTE: As of 2025-11-04, GPU passthrough is automatically configured during
# container creation by create_lxc_base.sh for vLLM and Ollama containers.
# This script is kept for:
# - Manual GPU configuration
# - Reconfiguring GPUs for existing containers
# - Adding GPUs to non-LLM containers
# - Advanced multi-GPU setups
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Add NVIDIA GPU passthrough configuration to LXC containers
#
# USAGE:
#   bash configure-gpu-passthrough.sh <container-id> <gpu-numbers> [--force]
#
# EXAMPLES:
#   # Add GPU 0 to container 208 (ollama)
#   bash configure-gpu-passthrough.sh 208 0
#
#   # Add multiple GPUs to container 209 (vLLM)
#   bash configure-gpu-passthrough.sh 209 0,1,2
#
#   # Add GPU range to container
#   bash configure-gpu-passthrough.sh 100 0-2  # GPUs 0, 1, and 2
#
#   # Force reconfiguration (removes old config first)
#   bash configure-gpu-passthrough.sh 100 0,1 --force
#
#   # Single GPU to multiple containers (sharing)
#   bash configure-gpu-passthrough.sh 208 0
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
Usage: $0 <container-id> <gpu-numbers> [--force]

Configure NVIDIA GPU passthrough for an LXC container.

Arguments:
  container-id   LXC container ID (e.g., 208, 209, 100)
  gpu-numbers    GPU device number(s):
                 - Single GPU: 0
                 - Multiple GPUs: 0,1,2
                 - GPU range: 0-2 (expands to 0,1,2)
                 Check available GPUs: nvidia-smi -L
  --force        Force reconfiguration (removes old GPU config first)

Examples:
  # Configure single GPU for container
  $0 208 0

  # Configure multiple GPUs for container
  $0 209 0,1,2

  # Configure GPU range
  $0 100 0-3    # GPUs 0, 1, 2, and 3

  # Share GPU with multiple containers
  $0 208 0
  $0 210 0

  # Force reconfiguration
  $0 208 0,1 --force

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
GPU_SPEC="$2"
FORCE_MODE=false

if [ "${3:-}" = "--force" ]; then
    FORCE_MODE=true
fi

# Validate container ID is numeric
if ! [[ "$CONTAINER_ID" =~ ^[0-9]+$ ]]; then
    error "Container ID must be numeric: $CONTAINER_ID"
    exit 1
fi

# Parse GPU specification (supports: 0, 0,1,2, or 0-2)
GPU_NUMBERS=()

if [[ "$GPU_SPEC" =~ ^[0-9]+-[0-9]+$ ]]; then
    # Range format: 0-2
    IFS='-' read -r START END <<< "$GPU_SPEC"
    for ((i=START; i<=END; i++)); do
        GPU_NUMBERS+=("$i")
    done
    info "Parsed GPU range: ${GPU_NUMBERS[*]}"
elif [[ "$GPU_SPEC" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    # Comma-separated format: 0,1,2
    IFS=',' read -ra GPU_NUMBERS <<< "$GPU_SPEC"
else
    error "Invalid GPU specification: $GPU_SPEC"
    echo "Valid formats: 0 (single), 0,1,2 (multiple), 0-2 (range)"
    exit 1
fi

# Validate at least one GPU specified
if [ ${#GPU_NUMBERS[@]} -eq 0 ]; then
    error "No GPUs specified"
    exit 1
fi

echo "=========================================="
echo "GPU Passthrough Configuration"
echo "=========================================="
info "Container: $CONTAINER_ID"
info "GPUs: ${GPU_NUMBERS[*]}"
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

# Verify all GPUs exist on host
info "Checking GPU availability on host..."
for gpu_num in "${GPU_NUMBERS[@]}"; do
    if ! nvidia-smi -L | grep -q "GPU $gpu_num:"; then
        error "GPU $gpu_num not found on host"
        echo ""
        echo "Available GPUs:"
        nvidia-smi -L
        exit 1
    fi
done

info "Found GPUs on host:"
for gpu_num in "${GPU_NUMBERS[@]}"; do
    nvidia-smi -L | grep "GPU $gpu_num:"
done
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
info "Configuring GPU passthrough for container $CONTAINER_ID..."

# Add header comment
cat >> "$CONF_FILE" << EOF
# GPU Passthrough: NVIDIA GPUs ${GPU_NUMBERS[*]}
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
EOF

# Add device mounts for each GPU
for gpu_num in "${GPU_NUMBERS[@]}"; do
    cat >> "$CONF_FILE" << EOF
lxc.mount.entry: /dev/nvidia${gpu_num} dev/nvidia${gpu_num} none bind,optional,create=file
EOF
done

# Add common NVIDIA device mounts
cat >> "$CONF_FILE" << EOF
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps dev/nvidia-caps none bind,optional,create=dir
EOF

success "GPU passthrough configuration added for GPUs: ${GPU_NUMBERS[*]}"

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
echo "2. Install NVIDIA drivers in the container (MUST match host version):"
echo "   bash provision/pct/install-nvidia-drivers.sh $CONTAINER_ID"
echo ""
echo "   Or manually inside container:"
echo "   pct enter $CONTAINER_ID"
echo ""
echo "   # Check host driver version first (on Proxmox host):"
echo "   nvidia-smi | grep 'Driver Version'"
echo ""
echo "   # Install matching driver version in container:"
echo "   wget https://us.download.nvidia.com/XFree86/Linux-x86_64/\$DRIVER_VERSION/NVIDIA-Linux-x86_64-\$DRIVER_VERSION.run"
echo "   bash NVIDIA-Linux-x86_64-\$DRIVER_VERSION.run --no-kernel-module --silent"
echo ""
echo "3. Verify GPU is accessible:"
echo "   pct exec $CONTAINER_ID -- nvidia-smi"
echo ""
echo "4. Test with PyTorch (if using ML workloads):"
echo "   pct exec $CONTAINER_ID -- python3 -c 'import torch; print(f\"CUDA: {torch.cuda.is_available()}, GPUs: {torch.cuda.device_count()}\")"
echo ""
echo "5. If GPUs not visible, check host GPU devices:"
echo "   ls -la /dev/nvidia*"
echo ""
echo "6. View container config:"
echo "   cat /etc/pve/lxc/${CONTAINER_ID}.conf"
echo ""

