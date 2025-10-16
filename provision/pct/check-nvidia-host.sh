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
  log_info "Installing NVIDIA drivers on host..."
  
  # Add NVIDIA repository
  wget https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/3bf863cc.pub -O /tmp/nvidia-cuda-keyring.asc
  gpg --dearmor < /tmp/nvidia-cuda-keyring.asc > /usr/share/keyrings/nvidia-cuda-keyring.gpg
  chmod 644 /usr/share/keyrings/nvidia-cuda-keyring.gpg
  
  echo "deb [signed-by=/usr/share/keyrings/nvidia-cuda-keyring.gpg] https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/ /" > /etc/apt/sources.list.d/nvidia-cuda.list
  
  apt-get update
  apt-get install -y cuda-drivers
  
  log_success "NVIDIA drivers installed. You may need to reboot the host."
}

install_cuda_drivers_metapackage() {
  log_info "Installing cuda-drivers meta-package..."
  
  # Ensure NVIDIA repo is configured
  if [[ ! -f /etc/apt/sources.list.d/nvidia-cuda.list ]]; then
    install_nvidia_drivers
    return
  fi
  
  apt-get update
  apt-get install -y cuda-drivers
  
  log_success "cuda-drivers meta-package installed"
}

purge_and_reinstall() {
  log_warning "Purging and reinstalling NVIDIA drivers..."
  
  # Stop any NVIDIA services
  systemctl stop nvidia-persistenced 2>/dev/null || true
  
  # Purge old packages
  apt-get purge -y 'nvidia-*' 'cuda-*' 'libnvidia-*' 'libcuda*' || true
  apt-get autoremove -y
  apt-get autoclean
  
  # Reinstall
  install_nvidia_drivers
  
  log_warning "Host reboot required for changes to take effect"
}

# Run main function
main

