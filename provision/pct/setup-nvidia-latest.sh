#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

log_info "Setting up NVIDIA latest drivers using official NVIDIA repository..."

# Detect Debian version
DEBIAN_VERSION=$(cat /etc/debian_version | cut -d. -f1)
if [[ "$DEBIAN_VERSION" == "13" ]]; then
  DEBIAN_CODENAME="debian12"  # Trixie uses debian12 NVIDIA repo
  log_info "Detected Debian 13 (Trixie) - using debian12 NVIDIA repository"
elif [[ "$DEBIAN_VERSION" == "12" ]]; then
  DEBIAN_CODENAME="debian12"
  log_info "Detected Debian 12 (Bookworm) - using debian12 NVIDIA repository"
else
  log_error "Unsupported Debian version: $DEBIAN_VERSION"
  exit 1
fi

# Clean up any existing installations
log_info "Step 1: Removing any existing NVIDIA installations..."

# Remove repository configs
rm -rf /etc/apt/sources.list.d/cuda* /etc/apt/sources.list.d/nvidia*
rm -rf /usr/share/keyrings/cuda* /usr/share/keyrings/nvidia*

# Purge all existing NVIDIA/CUDA packages to avoid conflicts
log_info "Purging existing NVIDIA/CUDA packages..."
apt-get purge -y 'nvidia-*' 'cuda-*' 'libnvidia-*' 'libcuda*' 2>/dev/null || true
apt-get autoremove -y
apt-get clean
log_success "Cleanup complete"

# Install the CUDA keyring package
log_info "Step 2: Installing NVIDIA CUDA repository keyring..."
cd /tmp
wget -q https://developer.download.nvidia.com/compute/cuda/repos/${DEBIAN_CODENAME}/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb
rm cuda-keyring_1.1-1_all.deb

# Update package lists
log_info "Step 3: Updating package lists from NVIDIA repository..."
apt-get update

# Show available driver versions
log_info "Step 4: Available NVIDIA driver versions:"
apt-cache search --names-only "^nvidia-driver-[0-9]" | sort -V | tail -10

# Install the latest NVIDIA driver and CUDA toolkit
log_info "Step 5: Installing latest NVIDIA driver and CUDA toolkit..."
log_info "This will install the newest available driver from NVIDIA's repository"

# Use cuda-drivers meta-package which pulls the latest
apt-get install -y cuda-drivers cuda-toolkit

# Verify installation
log_info "Step 6: Verifying installation..."
dpkg -l | grep nvidia-driver | head -5

log_success "=========================================="
log_success "NVIDIA driver installed from NVIDIA repository!"
log_success "=========================================="

# Check if nvidia-smi is available
if command -v nvidia-smi &>/dev/null; then
  log_info "nvidia-smi is installed"
  DRIVER_VERSION=$(dpkg -l | grep 'nvidia-driver-[0-9]' | awk '{print $3}' | head -1)
  log_info "Driver version: ${DRIVER_VERSION}"
else
  log_warning "nvidia-smi not found in PATH yet"
fi

log_warning ""
log_warning "REBOOT REQUIRED to load kernel modules"
log_warning ""
log_info "After reboot:"
log_info "1. Run: nvidia-smi"
log_info "2. Should show your GPUs with the latest driver"
log_info "3. Note the CUDA version shown by nvidia-smi"
log_info "4. Then run: bash provision/pct/test-vllm-on-host.sh"
log_info "   to test Python/PyTorch/vLLM setup"

