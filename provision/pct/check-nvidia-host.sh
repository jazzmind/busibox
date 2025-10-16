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
  log_info "Installing NVIDIA driver 550.163.01 directly from NVIDIA..."
  
  # Base URL for NVIDIA packages
  NVIDIA_REPO="https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64"
  
  # Remove any conflicting repository configurations
  rm -f /etc/apt/sources.list.d/cuda*.list
  rm -f /usr/share/keyrings/cuda-archive-keyring.gpg
  rm -f /usr/share/keyrings/nvidia-cuda-keyring.gpg
  
  log_info "Downloading NVIDIA driver 550.163.01 packages..."
  
  # Download the specific packages
  cd /tmp
  wget -q "${NVIDIA_REPO}/nvidia-driver-bin_550.163.01-1_amd64.deb"
  wget -q "${NVIDIA_REPO}/nvidia-kernel-open-dkms_550.163.01-1_amd64.deb"
  wget -q "${NVIDIA_REPO}/nvidia-kernel-common_550.163.01-1_amd64.deb"
  wget -q "${NVIDIA_REPO}/nvidia-utils-550_550.163.01-1_amd64.deb"
  wget -q "${NVIDIA_REPO}/libnvidia-ml1_550.163.01-1_amd64.deb"
  wget -q "${NVIDIA_REPO}/cuda-keyring_1.1-1_all.deb"
  
  log_info "Installing NVIDIA driver packages..."
  
  # Install in dependency order
  dpkg -i cuda-keyring_1.1-1_all.deb
  dpkg -i nvidia-kernel-common_550.163.01-1_amd64.deb
  dpkg -i nvidia-kernel-open-dkms_550.163.01-1_amd64.deb || apt-get install -f -y
  dpkg -i libnvidia-ml1_550.163.01-1_amd64.deb
  dpkg -i nvidia-utils-550_550.163.01-1_amd64.deb
  dpkg -i nvidia-driver-bin_550.163.01-1_amd64.deb
  
  # Fix any dependency issues
  apt-get install -f -y
  
  log_info "Installing CUDA 12.4 toolkit..."
  apt-get update
  apt-get install -y cuda-toolkit-12-4
  
  # Cleanup
  cd /
  rm -f /tmp/nvidia-*.deb /tmp/libnvidia-*.deb /tmp/cuda-keyring*.deb
  
  log_warning "=========================================="
  log_warning "NVIDIA driver 550.163.01 and CUDA 12.4 installed!"
  log_warning "HOST REBOOT REQUIRED!"
  log_warning "=========================================="
  log_info "Run: reboot"
  log_info "After reboot, verify with: nvidia-smi"
  log_info "Should show driver version 550.163.01 and CUDA 12.4"
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

