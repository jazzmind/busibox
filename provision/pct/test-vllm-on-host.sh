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

log_info "Setting up vLLM test environment on Proxmox host..."
log_info "Using Debian's open NVIDIA drivers and CUDA toolkit"

# Step 1: Clean up any existing NVIDIA installations
log_info "Step 1: Cleaning up existing NVIDIA packages and repositories..."

# Remove ALL NVIDIA repository configurations
log_info "Removing all NVIDIA repository files..."
rm -rf /etc/apt/sources.list.d/cuda*
rm -rf /usr/share/keyrings/cuda*
rm -rf /usr/share/keyrings/nvidia*
rm -rf /tmp/cuda-keyring*

# Also check for any .sources files (new apt format)
rm -rf /etc/apt/sources.list.d/nvidia*
find /etc/apt -name "*nvidia*" -delete 2>/dev/null || true
find /etc/apt -name "*cuda*" -delete 2>/dev/null || true

# Purge the cuda-keyring package if installed
dpkg -P cuda-keyring 2>/dev/null || true

log_info "Purging existing NVIDIA packages..."
# Now we can safely use apt
apt-get purge -y 'nvidia-*' 'cuda-*' 'libnvidia-*' 2>/dev/null || true
apt-get autoremove -y
apt-get clean
apt-get update

# Step 2: Enable Debian non-free and contrib repos
log_info "Step 2: Enabling Debian non-free and contrib repositories..."
if ! grep -q "non-free-firmware non-free contrib" /etc/apt/sources.list; then
  sed -i 's/main$/main non-free-firmware non-free contrib/' /etc/apt/sources.list
fi

# Check what Debian version we're on
DEBIAN_VERSION=$(cat /etc/debian_version | cut -d. -f1)
log_info "Debian version: ${DEBIAN_VERSION}"

# For Debian 12 (bookworm), we might need backports for newer NVIDIA packages
if [[ "$DEBIAN_VERSION" == "12" ]]; then
  log_info "Adding bookworm-backports repository for newer NVIDIA packages..."
  if ! grep -q "bookworm-backports" /etc/apt/sources.list; then
    echo "deb http://deb.debian.org/debian bookworm-backports main non-free-firmware non-free contrib" >> /etc/apt/sources.list
  fi
fi

apt-get update

# Step 3: Install NVIDIA drivers
log_info "Step 3: Searching for available NVIDIA packages in Debian ${DEBIAN_VERSION} (Trixie)..."

# Search for all available NVIDIA packages
log_info "All available NVIDIA packages:"
apt-cache search nvidia | grep "^nvidia-" | sort | head -40

log_info ""
log_info "Searching for CUDA packages:"
apt-cache search cuda | grep -E "^(nvidia-cuda|cuda-)" | head -20

log_info ""
log_info "Searching for driver packages specifically:"
apt-cache search --names-only "^nvidia-driver" | head -10

log_info ""
log_info "Based on what's available, attempting installation..."

# Try different package combinations based on what exists in Trixie
PACKAGES_TO_TRY=(
  "nvidia-driver"
  "nvidia-kernel-dkms"
  "nvidia-smi"
  "nvidia-utils"
)

INSTALLED_PACKAGES=()

for pkg in "${PACKAGES_TO_TRY[@]}"; do
  if apt-cache show "$pkg" &>/dev/null; then
    log_info "Package $pkg is available, installing..."
    if apt-get install -y "$pkg"; then
      INSTALLED_PACKAGES+=("$pkg")
      log_success "Installed $pkg"
    else
      log_warning "Failed to install $pkg"
    fi
  else
    log_warning "Package $pkg not available in repositories"
  fi
done

log_info ""
log_info "Installed NVIDIA packages:"
dpkg -l | grep nvidia | awk '{print $2, $3}'

# Check if we have nvidia-smi now
if command -v nvidia-smi &>/dev/null; then
  log_success "nvidia-smi is available!"
else
  log_warning "nvidia-smi not found yet, may need additional packages or reboot"
fi

# Step 4: Already installed CUDA toolkit above, check version
log_info "Step 4: Checking CUDA installation..."

log_info "CUDA packages installed:"
dpkg -l | grep cuda | awk '{print $2, $3}'

# Check for nvcc
if command -v nvcc &>/dev/null; then
  log_info "CUDA compiler version:"
  nvcc --version | grep "release" || true
fi

# Step 5: Check if NVIDIA driver is already working
log_info "Step 5: Checking for existing NVIDIA driver..."

if nvidia-smi &>/dev/null; then
  log_success "NVIDIA driver is already working!"
  nvidia-smi
elif [[ ! -f /dev/nvidia0 ]]; then
  log_warning "=========================================="
  log_warning "No NVIDIA GPU devices found in /dev/"
  log_warning "=========================================="
  log_warning "Debian 13 (Trixie) NVIDIA packages appear incomplete."
  log_warning "You may need to install drivers manually or use NVIDIA's official repository."
  log_warning ""
  log_info "Checking if nvidia-smi is installed but driver not loaded..."
  if command -v nvidia-smi &>/dev/null; then
    log_info "nvidia-smi found, trying to run it..."
    nvidia-smi || log_warning "nvidia-smi failed - driver may need reboot or is not installed"
  fi
  
  log_warning ""
  log_warning "To proceed, you have two options:"
  log_warning "1. Install NVIDIA drivers from their official repository"
  log_warning "2. If you already have drivers installed, reboot the host"
  exit 1
else
  log_warning "=========================================="
  log_warning "GPU devices exist but nvidia-smi not working"
  log_warning "May need reboot to load new drivers"
  log_warning "=========================================="
  log_info "After reboot, run this script again to continue with Python setup"
  exit 0
fi

# Step 6: Verify GPU access
log_info "Step 6: Verifying GPU access..."
nvidia-smi

if ! nvidia-smi &>/dev/null; then
  log_error "nvidia-smi failed after driver installation"
  log_error "Please reboot and run this script again"
  exit 1
fi

log_success "GPU drivers working!"

# Step 7: Install Python and venv
log_info "Step 7: Installing Python and virtual environment tools..."
apt-get install -y \
  python3 \
  python3-pip \
  python3-venv \
  python3-dev \
  build-essential

# Step 8: Create test venv
TEST_DIR="/opt/vllm-test"
log_info "Step 8: Creating test environment in ${TEST_DIR}..."
mkdir -p ${TEST_DIR}
cd ${TEST_DIR}

if [[ -d venv ]]; then
  log_info "Removing existing venv..."
  rm -rf venv
fi

python3 -m venv venv
source venv/bin/activate

# Step 9: Upgrade pip
log_info "Step 9: Upgrading pip..."
pip install --upgrade pip

# Step 10: Check CUDA paths
log_info "Step 10: Checking CUDA installation..."
log_info "CUDA libraries in ldconfig:"
ldconfig -p | grep cuda | head -10

log_info "Looking for CUDA in standard locations..."
find /usr -name "libcudart.so*" 2>/dev/null || true
find /usr -name "libcublas.so*" 2>/dev/null || true

# Step 11: Install PyTorch with CUDA support
log_info "Step 11: Installing PyTorch with CUDA support..."
log_info "Using PyTorch's recommended CUDA 12.1 build..."

pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# Step 12: Test PyTorch CUDA
log_info "Step 12: Testing PyTorch CUDA detection..."
python -c "
import torch
print('='*60)
print('PyTorch version:', torch.__version__)
print('CUDA available:', torch.cuda.is_available())
print('CUDA version:', torch.version.cuda)
print('cuDNN version:', torch.backends.cudnn.version())
print('Number of GPUs:', torch.cuda.device_count())
if torch.cuda.is_available():
    for i in range(torch.cuda.device_count()):
        print(f'GPU {i}: {torch.cuda.get_device_name(i)}')
        print(f'  Memory: {torch.cuda.get_device_properties(i).total_memory / 1024**3:.1f} GB')
print('='*60)
"

# Step 13: Install vLLM
log_info "Step 13: Installing vLLM..."
pip install vllm

# Step 14: Test vLLM import
log_info "Step 14: Testing vLLM import..."
python -c "
import vllm
print('vLLM version:', vllm.__version__)
from vllm import LLM
print('vLLM imported successfully!')
"

# Step 15: Install LiteLLM
log_info "Step 15: Installing LiteLLM..."
pip install litellm

log_info "Testing LiteLLM import..."
python -c "
import litellm
print('LiteLLM version:', litellm.__version__)
print('LiteLLM imported successfully!')
"

log_success "=========================================="
log_success "All components installed successfully!"
log_success "=========================================="
log_info "Test environment: ${TEST_DIR}/venv"
log_info "To activate: source ${TEST_DIR}/venv/bin/activate"
log_info ""
log_info "Next steps:"
log_info "1. Test with a small model:"
log_info "   source ${TEST_DIR}/venv/bin/activate"
log_info "   python -c 'from vllm import LLM; llm = LLM(\"facebook/opt-125m\"); print(llm.generate(\"Hello\"))'"
log_info ""
log_info "2. Once working, document the exact package versions and steps"
log_info "3. Apply the same steps to LXC containers via Ansible"

