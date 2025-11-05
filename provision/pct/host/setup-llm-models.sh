#!/bin/bash
#
# Pre-download LLM Models to Proxmox Host
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Pre-download and cache LLM models to shared storage before container deployment
#
# USAGE:
#   bash setup-llm-models.sh
#
# WHAT IT DOES:
#   1. Creates shared model directories on Proxmox host
#   2. Downloads models from HuggingFace
#   3. Models are mounted into LXC containers via bind mounts
#
# WHY:
#   - Avoids downloading large models during container deployment
#   - Saves bandwidth and time
#   - Models shared across multiple containers
#   - Mirrors pattern used for NVIDIA drivers
#
set -euo pipefail

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

# Host directory for shared model cache
# IMPORTANT: Must match the path created by setup-proxmox-host.sh
HUGGINGFACE_CACHE="/var/lib/llm-models/huggingface"
VENV_DIR="/opt/model-downloader"

# Models to pre-download
MODELS=(
    "Qwen/Qwen3-VL-8B-Instruct"      # Small test model (8B parameters)
    "microsoft/Phi-4-multimodal-instruct"  # Phi-4 model (6B parameters)
)

echo "=========================================="
echo "LLM Model Pre-Download for vLLM"
echo "=========================================="
log_info "This will download models to: ${HUGGINGFACE_CACHE}"
echo ""

# Step 1: Create host directory
log_info "Step 1: Creating model cache directory on host..."
mkdir -p "${HUGGINGFACE_CACHE}"
log_success "Directory created: ${HUGGINGFACE_CACHE}"
echo ""

# Step 2: Set up Python virtual environment
log_info "Step 2: Setting up Python virtual environment..."
if [ ! -d "${VENV_DIR}" ]; then
    log_info "Installing Python venv support..."
    apt-get update -qq
    apt-get install -y python3-venv python3-pip &>/dev/null
    
    log_info "Creating virtual environment at ${VENV_DIR}..."
    python3 -m venv "${VENV_DIR}"
    log_success "Virtual environment created"
else
    log_success "Virtual environment already exists"
fi
echo ""

# Step 3: Install HuggingFace CLI in venv
log_info "Step 3: Installing HuggingFace CLI..."
if ! "${VENV_DIR}/bin/python3" -c "import huggingface_hub" 2>/dev/null; then
    log_info "Installing huggingface-hub in virtual environment..."
    "${VENV_DIR}/bin/pip" install -q huggingface-hub
    log_success "HuggingFace CLI installed"
else
    log_success "HuggingFace CLI already installed"
fi
echo ""

# Step 4: Download models
log_info "Step 4: Downloading models..."
echo ""

for MODEL in "${MODELS[@]}"; do
    MODEL_DIR=$(echo "$MODEL" | sed 's/\//-/'g)
    MODEL_PATH="${HUGGINGFACE_CACHE}/models--${MODEL_DIR}"
    
    if [[ -d "${MODEL_PATH}" ]]; then
        log_success "✓ ${MODEL} (already cached)"
    else
        log_info "↓ Downloading ${MODEL}..."
        log_info "  This may take 10-30 minutes depending on model size..."
        
        HF_HOME="${HUGGINGFACE_CACHE}" "${VENV_DIR}/bin/python3" << EOF
from huggingface_hub import snapshot_download
try:
    snapshot_download('${MODEL}', resume_download=True)
    print("Download completed successfully")
except Exception as e:
    print(f"Download failed: {e}")
    exit(1)
EOF
        if [ $? -eq 0 ]; then
            log_success "✓ ${MODEL} downloaded"
        else
            log_error "✗ Failed to download ${MODEL}"
            exit 1
        fi
    fi
done
echo ""

# Step 5: Show model sizes
log_info "Step 5: Model storage summary..."
echo ""
du -sh "${HUGGINGFACE_CACHE}" 2>/dev/null | awk '{print "  Total cache size: " $1}'
echo ""
log_info "Downloaded models:"
for MODEL in "${MODELS[@]}"; do
    MODEL_DIR=$(echo "$MODEL" | sed 's/\//-/'g)
    SIZE=$(du -sh "${HUGGINGFACE_CACHE}/models--${MODEL_DIR}" 2>/dev/null | awk '{print $1}' || echo "N/A")
    echo "  - ${MODEL}: ${SIZE}"
done
echo ""

echo "=========================================="
log_success "Model pre-download complete!"
echo "=========================================="
echo ""
log_info "Next steps:"
echo ""
log_info "1. Models are cached at: ${HUGGINGFACE_CACHE}"
log_info "2. Deploy vLLM containers with Ansible:"
log_info "   cd provision/ansible"
log_info "   ansible-playbook -i inventory/test/hosts.yml site.yml --tags vllm"
echo ""
log_info "3. Configure bind mount (run on Proxmox host):"
log_info "   bash provision/pct/add-data-mounts.sh [test|production]"
log_info "   This mounts: Host ${HUGGINGFACE_CACHE} -> Container ${HUGGINGFACE_CACHE}"
echo ""
log_info "4. vLLM will use these pre-downloaded models (no re-download needed)"
echo ""

