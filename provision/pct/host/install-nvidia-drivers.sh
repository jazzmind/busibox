#!/usr/bin/env bash
#
# Install NVIDIA Drivers in LXC Container
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Install NVIDIA proprietary drivers in an LXC container that match the host version
#
# USAGE:
#   bash install-nvidia-drivers.sh <container-id> [driver-version]
#
# EXAMPLES:
#   # Auto-detect host driver version and install in container
#   bash install-nvidia-drivers.sh 208
#
#   # Install specific driver version
#   bash install-nvidia-drivers.sh 208 580.82
#
# REQUIREMENTS:
#   - Container must exist and have GPU passthrough configured
#   - NVIDIA drivers must be installed on Proxmox host
#   - Container must have internet access for downloading drivers
#
# WHY THIS IS NEEDED:
#   LXC containers share the host kernel, so the NVIDIA driver version in the
#   container MUST exactly match the host driver version. Using Debian packages
#   (nvidia-driver-535) will cause "Driver/library version mismatch" errors.
#
# WHAT IT DOES:
#   1. Detects NVIDIA driver version on Proxmox host
#   2. Downloads matching proprietary NVIDIA driver installer
#   3. Installs driver in container with --no-kernel-module flag
#   4. Verifies nvidia-smi works in container
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
Usage: $0 <container-id> [driver-version]

Install NVIDIA proprietary drivers in an LXC container, matching the host version.

Arguments:
  container-id     LXC container ID (e.g., 208, 209)
  driver-version   Optional: specific driver version (e.g., 580.82)
                   If omitted, auto-detects from host

Examples:
  # Auto-detect and install (recommended)
  $0 208

  # Install specific version
  $0 208 580.82

Important:
  - Driver version MUST match host to avoid "Driver/library version mismatch"
  - Container must have GPU passthrough configured first
  - Uses proprietary NVIDIA installer, NOT Debian packages
  - Installation takes 2-5 minutes

After installation:
  pct exec <container-id> -- nvidia-smi
EOF
}

# Parse arguments
if [ $# -lt 1 ]; then
    error "Missing container ID"
    echo
    usage
    exit 1
fi

CONTAINER_ID="$1"
DRIVER_VERSION="${2:-}"

# Validate container ID is numeric
if ! [[ "$CONTAINER_ID" =~ ^[0-9]+$ ]]; then
    error "Container ID must be numeric: $CONTAINER_ID"
    exit 1
fi

echo "=========================================="
echo "NVIDIA Driver Installation for Container"
echo "=========================================="
info "Container: $CONTAINER_ID"
echo ""

# Verify container exists
if ! pct status "$CONTAINER_ID" &>/dev/null; then
    error "Container $CONTAINER_ID not found"
    echo "List available containers: pct list"
    exit 1
fi

# Verify container is running
if ! pct status "$CONTAINER_ID" | grep -q "running"; then
    error "Container $CONTAINER_ID is not running"
    echo "Start it first: pct start $CONTAINER_ID"
    exit 1
fi

# Verify nvidia-smi is available on host
if ! command -v nvidia-smi &>/dev/null; then
    error "nvidia-smi not found on host. Install NVIDIA drivers on Proxmox first."
    exit 1
fi

# Auto-detect driver version if not specified
if [ -z "$DRIVER_VERSION" ]; then
    info "Detecting NVIDIA driver version on host..."
    DRIVER_VERSION=$(nvidia-smi | grep "Driver Version" | awk '{print $3}' | head -1)
    
    if [ -z "$DRIVER_VERSION" ]; then
        error "Could not detect NVIDIA driver version on host"
        echo "Specify manually: $0 $CONTAINER_ID <driver-version>"
        exit 1
    fi
    
    info "Detected host driver version: $DRIVER_VERSION"
else
    info "Using specified driver version: $DRIVER_VERSION"
fi

# Validate driver version format (e.g., 580.82, 535.183.01)
if ! [[ "$DRIVER_VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]; then
    error "Invalid driver version format: $DRIVER_VERSION"
    echo "Expected format: 580.82 or 535.183.01"
    exit 1
fi

# NVIDIA driver download URL
NVIDIA_BASE_URL="https://us.download.nvidia.com/XFree86/Linux-x86_64"
DRIVER_FILENAME="NVIDIA-Linux-x86_64-${DRIVER_VERSION}.run"
DRIVER_URL="${NVIDIA_BASE_URL}/${DRIVER_VERSION}/${DRIVER_FILENAME}"

info "Driver download URL: $DRIVER_URL"
echo ""

# Host cache directory for NVIDIA drivers (shared across containers)
HOST_CACHE_DIR="/var/lib/llm-models/nvidia-drivers"
mkdir -p "$HOST_CACHE_DIR"

# Check if driver is already installed
info "Checking if driver is already installed in container..."
INSTALLED_VERSION=""
if pct exec "$CONTAINER_ID" -- bash -c "command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null" 2>/dev/null; then
    INSTALLED_VERSION=$(pct exec "$CONTAINER_ID" -- nvidia-smi | grep "Driver Version" | awk '{print $3}' | head -1 || echo "unknown")
    
    if [ "$INSTALLED_VERSION" = "$DRIVER_VERSION" ]; then
        success "Driver version $DRIVER_VERSION is already installed in container"
        echo ""
        pct exec "$CONTAINER_ID" -- nvidia-smi
        exit 0
    else
        warn "Different driver version installed: $INSTALLED_VERSION (need $DRIVER_VERSION)"
        warn "Will uninstall existing drivers and install correct version..."
    fi
else
    info "No NVIDIA driver installed in container"
fi

# Uninstall existing drivers if version mismatch or if installation exists
if [ -n "$INSTALLED_VERSION" ] || pct exec "$CONTAINER_ID" -- bash -c "ls /usr/lib/x86_64-linux-gnu/libnvidia* 2>/dev/null || ls /usr/local/nvidia* 2>/dev/null" 2>/dev/null | grep -q .; then
    warn "Uninstalling existing NVIDIA drivers..."
    
    pct exec "$CONTAINER_ID" -- bash -c "
        # Try to uninstall using NVIDIA installer if it exists
        if [ -f /usr/bin/nvidia-uninstall ]; then
            /usr/bin/nvidia-uninstall --silent || true
        fi
        
        # Remove NVIDIA libraries and binaries
        rm -rf /usr/lib/x86_64-linux-gnu/libnvidia* 2>/dev/null || true
        rm -rf /usr/local/nvidia* 2>/dev/null || true
        rm -f /usr/bin/nvidia-smi /usr/bin/nvidia-debugdump /usr/bin/nvidia-ml-py* 2>/dev/null || true
        rm -rf /usr/lib/x86_64-linux-gnu/libGL* 2>/dev/null || true
        
        # Remove CUDA toolkit if installed via apt (but keep if installed via NVIDIA installer)
        apt-get purge -y 'cuda-drivers' 'cuda-toolkit' 'nvidia-driver*' 2>/dev/null || true
        apt-get autoremove -y 2>/dev/null || true
        
        # Clean up any remaining NVIDIA files
        find /usr -name '*nvidia*' -type f -delete 2>/dev/null || true
        find /usr -name '*cuda*' -type f -delete 2>/dev/null || true
    " || warn "Some cleanup steps failed (may be harmless)"
    
    success "Existing drivers uninstalled"
    echo ""
fi

echo ""
info "Installing prerequisites in container..."

# Install build dependencies and monitoring tools
pct exec "$CONTAINER_ID" -- bash -c "
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq wget kmod libglvnd0 pkg-config libglvnd-dev > /dev/null 2>&1
    
    # Install nvtop for GPU monitoring (available in Debian 12+)
    apt-get install -y -qq nvtop > /dev/null 2>&1 || echo 'nvtop not available in repos, will install from source later'
" || {
    error "Failed to install prerequisites"
    exit 1
}

success "Prerequisites installed"

# Download driver installer to host cache (if not already cached)
HOST_DRIVER_PATH="${HOST_CACHE_DIR}/${DRIVER_FILENAME}"

if [ -f "$HOST_DRIVER_PATH" ]; then
    info "Driver installer found in host cache: $HOST_DRIVER_PATH"
    success "Using cached driver installer"
else
    info "Downloading NVIDIA driver $DRIVER_VERSION to host cache..."
    info "This may take a few minutes..."
    info "Cache location: $HOST_CACHE_DIR"
    
    if wget -q --show-progress "$DRIVER_URL" -O "$HOST_DRIVER_PATH"; then
        chmod +x "$HOST_DRIVER_PATH"
        success "Driver downloaded and cached on host"
    else
        error "Failed to download driver installer"
        echo ""
        echo "Check if URL is valid:"
        echo "  $DRIVER_URL"
        echo ""
        echo "You can find available driver versions at:"
        echo "  https://www.nvidia.com/Download/index.aspx"
        exit 1
    fi
fi

# Copy driver installer from host cache to container
info "Copying driver installer to container..."
pct push "$CONTAINER_ID" "$HOST_DRIVER_PATH" "/tmp/$DRIVER_FILENAME" || {
    error "Failed to copy driver installer to container"
    exit 1
}

pct exec "$CONTAINER_ID" -- chmod +x "/tmp/$DRIVER_FILENAME" || {
    error "Failed to make driver installer executable"
    exit 1
}

success "Driver installer ready in container"
echo ""

# Install driver (this takes a few minutes)
info "Installing NVIDIA driver in container..."
info "This will take 2-5 minutes, please wait..."
echo ""

# Install driver (this takes a few minutes)
# Use --uninstall-first to remove any conflicting installations
pct exec "$CONTAINER_ID" -- bash -c "
    cd /tmp
    
    # Run installer with options for container environment
    # --no-kernel-module: Don't install kernel module (use host's)
    # --silent: Non-interactive installation
    # --no-drm: Skip DRM setup
    # --install-libglvnd: Install GL vendor neutral dispatch library
    # --uninstall-first: Remove any existing conflicting installations
    
    ./'$DRIVER_FILENAME' \
        --no-kernel-module \
        --silent \
        --no-drm \
        --install-libglvnd \
        --uninstall-first \
        2>&1
    
    # Check exit code
    INSTALL_EXIT=\$?
    if [ \$INSTALL_EXIT -ne 0 ]; then
        echo 'Installation failed with exit code: '\$INSTALL_EXIT
        echo ''
        echo 'Checking installer log for details...'
        if [ -f /var/log/nvidia-installer.log ]; then
            tail -50 /var/log/nvidia-installer.log
        fi
        exit \$INSTALL_EXIT
    fi
" || {
    error "Driver installation failed"
    echo ""
    echo "Check logs in container:"
    echo "  pct exec $CONTAINER_ID -- cat /var/log/nvidia-installer.log"
    echo ""
    echo "Common issues:"
    echo "  - Conflicting driver installations: Try manual cleanup first"
    echo "  - Container needs reboot: pct reboot $CONTAINER_ID"
    exit 1
}

echo ""
success "Driver installation completed"

# Verify installation
info "Verifying NVIDIA driver installation..."
echo ""

if pct exec "$CONTAINER_ID" -- nvidia-smi 2>/dev/null; then
    echo ""
    success "NVIDIA driver is working correctly in container!"
    
    # Display driver and GPU info
    INSTALLED_VERSION=$(pct exec "$CONTAINER_ID" -- nvidia-smi | grep "Driver Version" | awk '{print $3}' | head -1)
    GPU_COUNT=$(pct exec "$CONTAINER_ID" -- nvidia-smi --list-gpus | wc -l)
    
    echo ""
    info "Summary:"
    echo "  Container ID: $CONTAINER_ID"
    echo "  Driver Version: $INSTALLED_VERSION"
    echo "  GPUs Available: $GPU_COUNT"
else
    error "Driver installed but nvidia-smi failed"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check GPU passthrough is configured:"
    echo "     cat /etc/pve/lxc/${CONTAINER_ID}.conf | grep nvidia"
    echo ""
    echo "  2. Check GPU devices exist in container:"
    echo "     pct exec $CONTAINER_ID -- ls -la /dev/nvidia*"
    echo ""
    echo "  3. Check driver installation log:"
    echo "     pct exec $CONTAINER_ID -- cat /var/log/nvidia-installer.log"
    exit 1
fi

# Install nvtop for GPU monitoring
info "Installing nvtop for GPU monitoring..."

if ! pct exec "$CONTAINER_ID" -- bash -c "command -v nvtop &>/dev/null" 2>/dev/null; then
    # nvtop not installed, try to install from repos or build from source
    pct exec "$CONTAINER_ID" -- bash -c "
        export DEBIAN_FRONTEND=noninteractive
        
        # Try apt first (available in Debian 12+)
        if apt-get install -y nvtop > /dev/null 2>&1; then
            echo 'nvtop installed from repository'
            exit 0
        fi
        
        # If apt fails, build from source
        echo 'Building nvtop from source...'
        apt-get install -y -qq git cmake libncurses5-dev libncursesw5-dev libudev-dev > /dev/null 2>&1
        
        cd /tmp
        git clone --quiet https://github.com/Syllo/nvtop.git 2>/dev/null
        cd nvtop
        mkdir -p build
        cd build
        cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1
        make -j\$(nproc) > /dev/null 2>&1
        make install > /dev/null 2>&1
        
        # Cleanup
        cd /tmp
        rm -rf nvtop
    " 2>/dev/null && success "nvtop installed" || warn "nvtop installation failed (optional)"
else
    success "nvtop already installed"
fi

# Cleanup container temp file (keep host cache)
info "Cleaning up container temp files..."
pct exec "$CONTAINER_ID" -- bash -c "rm -f /tmp/$DRIVER_FILENAME" 2>/dev/null || true

# Note: Host cache is kept for reuse across containers
info "Driver installer cached on host at: $HOST_DRIVER_PATH"

echo ""
echo "=========================================="
success "Installation Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Test GPU access:"
echo "   pct exec $CONTAINER_ID -- nvidia-smi"
echo ""
echo "2. Monitor GPU usage with nvtop (interactive):"
echo "   pct enter $CONTAINER_ID"
echo "   nvtop"
echo ""
echo "3. Install CUDA toolkit (if needed for development):"
echo "   pct exec $CONTAINER_ID -- apt-get install -y nvidia-cuda-toolkit"
echo ""
echo "4. Test with PyTorch (if using ML workloads):"
echo "   pct exec $CONTAINER_ID -- python3 -c 'import torch; print(f\"CUDA: {torch.cuda.is_available()}, GPUs: {torch.cuda.device_count()}\")"
echo ""
echo "5. If version mismatch errors occur, verify host/container versions match:"
echo "   Host: nvidia-smi | grep 'Driver Version'"
echo "   Container: pct exec $CONTAINER_ID -- nvidia-smi | grep 'Driver Version'"
echo ""


