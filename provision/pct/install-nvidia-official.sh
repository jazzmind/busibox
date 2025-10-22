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

log_info "Installing NVIDIA driver 550 using official NVIDIA CUDA repository..."
log_info "This works on Debian 13 (Trixie) and Debian 12 (Bookworm)"

# Clean up any existing installations
log_info "Step 1: Removing existing NVIDIA packages..."

# First, remove repository configs to avoid conflicts
rm -rf /etc/apt/sources.list.d/cuda* /etc/apt/sources.list.d/nvidia*
rm -rf /usr/share/keyrings/cuda* /usr/share/keyrings/nvidia*

# Purge all existing NVIDIA packages
log_info "Purging all existing NVIDIA/CUDA packages..."
apt-get purge -y 'nvidia-*' 'cuda-*' 'libnvidia-*' 'libcuda*' 2>/dev/null || true
apt-get autoremove -y
apt-get clean

# Install the CUDA keyring package directly
log_info "Step 2: Installing NVIDIA CUDA repository keyring..."
cd /tmp
wget https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb
rm cuda-keyring_1.1-1_all.deb

# Update package lists
log_info "Step 3: Updating package lists from NVIDIA repository..."
apt-get update

# Install CUDA drivers (which includes the kernel driver and nvidia-smi)
log_info "Step 4: Installing CUDA drivers 12.6..."
apt-get install -y cuda-drivers-550

# Verify installation
log_info "Step 5: Verifying installation..."
if dpkg -l | grep -q nvidia-driver; then
  log_success "NVIDIA driver packages installed!"
  dpkg -l | grep nvidia-driver
else
  log_error "Driver installation may have failed"
  exit 1
fi

log_warning "=========================================="
log_warning "NVIDIA driver 550 installed from NVIDIA repository"
log_warning "REBOOT REQUIRED to load kernel modules"
log_warning "=========================================="
log_info ""
log_info "After reboot:"
log_info "1. Run: nvidia-smi"
log_info "2. Should show driver 550.x and CUDA 12.4"
log_info "3. Then run: bash provision/pct/test-vllm-on-host.sh"
log_info "   to continue with Python/PyTorch/vLLM setup"

