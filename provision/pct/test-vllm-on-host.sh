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
apt-get update

# Step 3: Install open NVIDIA kernel drivers
log_info "Step 3: Installing Debian's open NVIDIA kernel drivers..."
apt-get install -y \
  nvidia-kernel-open-dkms \
  nvidia-smi \
  libnvidia-ml1

log_info "Installed packages:"
dpkg -l | grep nvidia | awk '{print $2, $3}'

# Step 4: Install CUDA toolkit from Debian
log_info "Step 4: Installing CUDA toolkit from Debian repositories..."
apt-get install -y \
  nvidia-cuda-toolkit \
  nvidia-cuda-dev

log_info "CUDA packages installed:"
dpkg -l | grep cuda | awk '{print $2, $3}'

# Step 5: Check if reboot is needed
if [[ ! -f /dev/nvidia0 ]] || ! nvidia-smi &>/dev/null; then
  log_warning "=========================================="
  log_warning "Kernel driver installed - REBOOT REQUIRED"
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

