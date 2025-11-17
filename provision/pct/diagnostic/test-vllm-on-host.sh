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
log_info "This script will install Python, PyTorch, vLLM, and LiteLLM to test GPU functionality"
log_info ""
log_warning "Prerequisites:"
log_warning "  - NVIDIA drivers must already be installed on the host"
log_warning "  - Run 'bash provision/pct/setup-proxmox-host.sh' first if not done"
log_info ""

# Step 1: Verify NVIDIA driver is working
log_info "Step 1: Verifying NVIDIA driver..."

if ! command -v nvidia-smi &>/dev/null; then
  log_error "nvidia-smi not found!"
  log_error "Please install NVIDIA drivers first:"
  log_error "  bash provision/pct/setup-proxmox-host.sh"
  exit 1
fi

if ! nvidia-smi &>/dev/null; then
  log_error "nvidia-smi failed to run!"
  log_error "Driver may not be properly installed or loaded"
  log_error "Try rebooting or reinstalling drivers with:"
  log_error "  bash provision/pct/setup-proxmox-host.sh"
  exit 1
fi

log_success "NVIDIA driver is working!"
nvidia-smi
echo ""

# Detect CUDA version
CUDA_VERSION=$(nvidia-smi | grep "CUDA Version" | awk '{print $9}' | cut -d. -f1)
log_info "Detected CUDA version: ${CUDA_VERSION}.x"
echo ""

# Step 2: Install Python and venv
log_info "Step 2: Installing Python and virtual environment tools..."
apt-get update
apt-get install -y \
  python3 \
  python3-pip \
  python3-venv \
  python3-dev \
  build-essential

log_success "Python tools installed"
echo ""

# Step 3: Create test venv
TEST_DIR="/opt/vllm-test"
log_info "Step 3: Creating test environment in ${TEST_DIR}..."

if [[ -d ${TEST_DIR} ]]; then
  log_warning "Test directory exists, removing..."
  rm -rf ${TEST_DIR}
fi

mkdir -p ${TEST_DIR}
cd ${TEST_DIR}

python3 -m venv venv
source venv/bin/activate

log_success "Virtual environment created"
echo ""

# Step 4: Upgrade pip
log_info "Step 4: Upgrading pip..."
pip install --upgrade pip
echo ""

# Step 5: Install PyTorch with CUDA support
log_info "Step 5: Installing PyTorch with CUDA support..."

# Choose PyTorch build based on detected CUDA version
if [[ "$CUDA_VERSION" == "13" ]]; then
  log_info "Installing PyTorch with CUDA 12.4 support (compatible with CUDA 13.x driver)..."
  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
elif [[ "$CUDA_VERSION" == "12" ]]; then
  log_info "Installing PyTorch with CUDA 12.1 support..."
  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
elif [[ "$CUDA_VERSION" == "11" ]]; then
  log_info "Installing PyTorch with CUDA 11.8 support..."
  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
else
  log_warning "Unknown CUDA version ${CUDA_VERSION}, trying CUDA 12.4..."
  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
fi

log_success "PyTorch installed"
echo ""

# Step 6: Test PyTorch CUDA
log_info "Step 6: Testing PyTorch CUDA detection..."
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
        props = torch.cuda.get_device_properties(i)
        print(f'  Memory: {props.total_memory / 1024**3:.1f} GB')
print('='*60)
"

# Check if CUDA is available
if ! python -c "import torch; assert torch.cuda.is_available(), 'CUDA not available'; assert torch.cuda.device_count() > 0, 'No GPUs detected'" 2>/dev/null; then
  log_error "PyTorch cannot detect CUDA or GPUs!"
  log_error "This usually means:"
  log_error "  1. Driver/kernel module version mismatch"
  log_error "  2. Missing CUDA libraries"
  log_error "  3. Incorrect PyTorch CUDA version"
  log_error ""
  log_error "Debug info:"
  ldconfig -p | grep cuda | head -10
  exit 1
fi

log_success "PyTorch can access GPUs!"
echo ""

# Step 7: Install vLLM
log_info "Step 7: Installing vLLM..."
pip install vllm

log_success "vLLM installed"
echo ""

# Step 8: Test vLLM import
log_info "Step 8: Testing vLLM import..."
python -c "
import vllm
print('vLLM version:', vllm.__version__)
from vllm import LLM
print('vLLM imported successfully!')
"

log_success "vLLM is working!"
echo ""

# Step 9: Install LiteLLM
log_info "Step 9: Installing LiteLLM..."
pip install litellm

log_info "Testing LiteLLM import..."
python -c "
import litellm
# litellm doesn't always have __version__, try to get it from package metadata
try:
    from importlib.metadata import version
    print('LiteLLM version:', version('litellm'))
except:
    print('LiteLLM version: (unable to detect)')
print('LiteLLM imported successfully!')
"

log_success "LiteLLM is working!"
echo ""

# Step 10: Summary
log_success "=========================================="
log_success "All components installed successfully!"
log_success "=========================================="
log_info ""
log_info "Test environment: ${TEST_DIR}/venv"
log_info "To activate: source ${TEST_DIR}/venv/bin/activate"
log_info ""
log_info "Installed versions:"
source ${TEST_DIR}/venv/bin/activate
pip list | grep -E "torch|vllm|litellm"
echo ""
log_info "Next steps to test with a model:"
log_info "1. Activate the environment:"
log_info "   source ${TEST_DIR}/venv/bin/activate"
log_info ""
log_info "2. Test vLLM with a small model:"
log_info "   python -c 'from vllm import LLM; llm = LLM(\"facebook/opt-125m\"); print(llm.generate(\"Hello, my name is\"))'"
log_info ""
log_info "3. Or test with Qwen 0.5B (recommended for testing):"
log_info "   python -c 'from vllm import LLM; llm = LLM(\"Qwen/Qwen2.5-0.5B-Instruct\"); print(llm.generate(\"What is AI?\"))'"
log_info ""
log_success "Setup complete! GPU-accelerated LLM inference is ready."
