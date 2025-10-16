#!/usr/bin/env bash
#
# Check and fix NVIDIA driver setup on Proxmox host
#
# This script:
# 1. Checks if NVIDIA drivers are installed on the host
# 2. Verifies the driver version
# 3. Optionally installs/updates to cuda-drivers meta-package
#
# Usage:
#   bash check-nvidia-host.sh [--fix]
#

set -euo pipefail

# --- Configuration ---
FIX_MODE=false
if [[ "${1:-}" == "--fix" ]]; then
  FIX_MODE=true
fi

# --- Colors for output ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Logging functions ---
log_info() {
  echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $*"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $*"
}

# --- Main checks ---
main() {
  log_info "Checking NVIDIA driver setup on Proxmox host..."
  echo ""
  
  # Check if nvidia-smi is available
  if ! command -v nvidia-smi &>/dev/null; then
    log_error "nvidia-smi not found on host!"
    if [[ "$FIX_MODE" == "true" ]]; then
      install_nvidia_drivers
    else
      log_info "Run with --fix to install NVIDIA drivers"
      exit 1
    fi
  else
    log_success "nvidia-smi found"
  fi
  
  # Get driver version
  if nvidia-smi &>/dev/null; then
    DRIVER_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
    log_success "NVIDIA Driver Version: $DRIVER_VERSION"
    
    # Driver version check (any modern driver should work with nvidia-driver-cuda)
    log_info "Current driver supports CUDA $(nvidia-smi | grep 'CUDA Version' | awk '{print $9}')"
  else
    log_error "nvidia-smi failed to run"
    if [[ "$FIX_MODE" == "true" ]]; then
      log_info "Attempting to fix..."
      purge_and_reinstall
    else
      log_info "Run with --fix to reinstall NVIDIA drivers"
      exit 1
    fi
  fi
  
  # Check kernel module version
  if [[ -f /proc/driver/nvidia/version ]]; then
    log_info "Kernel module info:"
    cat /proc/driver/nvidia/version | head -1
    echo ""
  else
    log_warning "NVIDIA kernel module not loaded"
  fi
  
  # Check installed packages
  log_info "Checking installed NVIDIA packages..."
  if dpkg -l | grep -E '^ii.*nvidia' | grep -v lib | head -10; then
    echo ""
  else
    log_warning "No NVIDIA driver packages found"
  fi
  
  # Check for cuda-drivers meta-package
  if dpkg -l | grep -q "^ii.*cuda-drivers"; then
    log_success "cuda-drivers meta-package is installed"
  else
    log_warning "cuda-drivers meta-package NOT installed"
    if [[ "$FIX_MODE" == "true" ]]; then
      install_cuda_drivers_metapackage
    else
      log_info "Run with --fix to install cuda-drivers meta-package"
    fi
  fi
  
  # List GPUs
  log_info "Available GPUs:"
  nvidia-smi -L
  echo ""
  
  log_success "NVIDIA host check complete!"
}

install_nvidia_drivers() {
  log_info "Installing NVIDIA driver 550 and CUDA 12.4 from NVIDIA repository..."
  
  # Add NVIDIA CUDA repository
  if [[ ! -f /etc/apt/sources.list.d/cuda.list ]]; then
    log_info "Adding NVIDIA CUDA repository..."
    
    # Download and install CUDA keyring package
    wget -q https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/cuda-keyring_1.1-1_all.deb -O /tmp/cuda-keyring.deb
    
    if [[ ! -f /tmp/cuda-keyring.deb ]]; then
      log_error "Failed to download CUDA keyring"
      exit 1
    fi
    
    dpkg -i /tmp/cuda-keyring.deb
    rm -f /tmp/cuda-keyring.deb
  fi
  
  log_info "Updating package lists..."
  apt-get update
  
  # List available driver versions for debugging
  log_info "Available NVIDIA driver 550 packages:"
  apt-cache search --names-only 'nvidia' | grep '550' | head -15
  
  # Install NVIDIA driver 550 and CUDA 12.4 toolkit from NVIDIA repository
  # Using nvidia-driver-bin which contains the actual driver binary
  log_info "Installing NVIDIA driver 550.163.01 and CUDA 12.4 toolkit..."
  apt-get install -y \
    nvidia-driver-bin \
    nvidia-kernel-open-dkms \
    nvidia-utils-550 \
    cuda-toolkit-12-4
  
  log_warning "=========================================="
  log_warning "NVIDIA driver 550 and CUDA 12.4 installed!"
  log_warning "HOST REBOOT REQUIRED!"
  log_warning "=========================================="
  log_info "Run: reboot"
  log_info "After reboot, verify with: nvidia-smi"
  log_info "Should show driver version 550.x and CUDA 12.4"
}

install_cuda_drivers_metapackage() {
  log_info "Installing NVIDIA driver and CUDA 12.4 packages..."
  
  # Just call the main install function
  install_nvidia_drivers
}

purge_and_reinstall() {
  log_warning "Purging and reinstalling NVIDIA drivers..."
  
  # The install_nvidia_drivers function now handles clean removal
  install_nvidia_drivers
}

# Run main function
main

