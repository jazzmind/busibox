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
HUGGINGFACE_CACHE="/var/lib/huggingface-cache"

# Models to pre-download
MODELS=(
    "Qwen/Qwen2.5-0.5B-Instruct"      # Small test model (0.5B parameters)
    "Qwen/Qwen2.5-3B-Instruct"        # Production model (3B parameters)
    "THUDM/glm-4-9b-chat"             # GLM-4 model (9B parameters)
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

# Step 2: Install HuggingFace CLI if needed
log_info "Step 2: Installing HuggingFace CLI..."
if ! python3 -c "import huggingface_hub" 2>/dev/null; then
    log_info "Installing Python dependencies..."
    apt-get update -qq
    apt-get install -y python3-pip &>/dev/null
    pip3 install -q huggingface-hub
    log_success "HuggingFace CLI installed"
else
    log_success "HuggingFace CLI already installed"
fi
echo ""

# Step 3: Download models
log_info "Step 3: Downloading models..."
echo ""

for MODEL in "${MODELS[@]}"; do
    MODEL_DIR=$(echo "$MODEL" | sed 's/\//-/'g)
    MODEL_PATH="${HUGGINGFACE_CACHE}/models--${MODEL_DIR}"
    
    if [[ -d "${MODEL_PATH}" ]]; then
        log_success "✓ ${MODEL} (already cached)"
    else
        log_info "↓ Downloading ${MODEL}..."
        log_info "  This may take 10-30 minutes depending on model size..."
        
        HF_HOME="${HUGGINGFACE_CACHE}" python3 << EOF
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

# Step 4: Show model sizes
log_info "Step 4: Model storage summary..."
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
log_info "3. Ansible will automatically mount this cache into containers:"
log_info "   Host: ${HUGGINGFACE_CACHE}"
log_info "   Container: /root/.cache/huggingface"
echo ""
log_info "4. vLLM will use these pre-downloaded models (no re-download needed)"
echo ""

