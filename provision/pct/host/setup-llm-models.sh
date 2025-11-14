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
# HuggingFace stores models in hub/ subdirectory
MODELS_DIR="${HUGGINGFACE_CACHE}/hub"
VENV_DIR="/opt/model-downloader"

# Models to pre-download
MODELS=(
    "microsoft/Phi-4-multimodal-instruct"  # Phi-4 chat model (6B parameters, GPU 0)
    "Qwen/Qwen3-Embedding-8B"              # Qwen3 embedding model (8B parameters, 4096 dims, GPU 1)
    "google/paligemma-3b-pt-448"           # PaliGemma-3B base model (required by ColPali)
    "vidore/colpali-v1.3"                  # ColPali v1.3 LoRA adapters for PDF embeddings (GPU 2)
    "Qwen/Qwen3-VL-8B-Instruct"            # Qwen3 VL model (8B parameters, 4096 dims, GPU 1)
    "Qwen/Qwen3-30B-A3B-Instruct-2507"    # Qwen3 30B model (30B parameters, 4096 dims, GPU 1)
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
    MODEL_DIR=$(echo "$MODEL" | sed 's/\//-/g')
    MODEL_PATH="${MODELS_DIR}/models--${MODEL_DIR}"
    
    # Check if model exists by looking for any snapshots directory
    if [[ -d "${MODEL_PATH}/snapshots" ]] && [[ -n "$(ls -A ${MODEL_PATH}/snapshots 2>/dev/null)" ]]; then
        log_success "✓ ${MODEL} (already cached)"
    else
        log_info "↓ Downloading ${MODEL}..."
        log_info "  This may take 10-30 minutes depending on model size..."
        
        # Download and capture the actual cache directory
        DOWNLOAD_OUTPUT=$(HF_HOME="${HUGGINGFACE_CACHE}" "${VENV_DIR}/bin/python3" << EOF
from huggingface_hub import snapshot_download
import os
try:
    cache_dir = snapshot_download('${MODEL}', resume_download=True)
    # Find the models-- directory (go up two levels from snapshot)
    parent_dir = os.path.dirname(cache_dir)  # Remove snapshot hash
    models_dir = os.path.dirname(parent_dir)  # Remove /snapshots
    model_name = os.path.basename(models_dir)  # Get models--Org--ModelName
    print(f"CACHE_DIR:{cache_dir}")
    print(f"MODEL_DIR:{model_name}")
except Exception as e:
    print(f"ERROR:{e}")
    exit(1)
EOF
)
        
        if [ $? -eq 0 ]; then
            log_success "✓ ${MODEL} downloaded"
            # Extract and display the actual cache location
            ACTUAL_CACHE=$(echo "$DOWNLOAD_OUTPUT" | grep "CACHE_DIR:" | cut -d: -f2-)
            ACTUAL_MODEL=$(echo "$DOWNLOAD_OUTPUT" | grep "MODEL_DIR:" | cut -d: -f2-)
            if [[ -n "$ACTUAL_MODEL" ]]; then
                log_info "  Cached as: ${ACTUAL_MODEL}"
            fi
        else
            log_error "✗ Failed to download ${MODEL}"
            log_error "$DOWNLOAD_OUTPUT"
            exit 1
        fi
    fi
done
echo ""

# Step 5: Show model sizes
log_info "Step 5: Model storage summary..."
echo ""
TOTAL_SIZE=$(du -sh "${HUGGINGFACE_CACHE}" 2>/dev/null | awk '{print $1}')
echo "  Total cache size: ${TOTAL_SIZE}"
echo ""
log_info "Downloaded models (requested):"
for MODEL in "${MODELS[@]}"; do
    # Convert model name to directory format (org/model -> models--org--model)
    MODEL_DIR=$(echo "$MODEL" | sed 's/\/--/--/g' | sed 's/\//--/g')
    MODEL_PATH="${MODELS_DIR}/models--${MODEL_DIR}"
    
    if [[ -d "${MODEL_PATH}" ]]; then
        # Count snapshots
        SNAPSHOTS=$(find "${MODEL_PATH}/snapshots" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
        # Get size of entire model directory
        SIZE=$(du -sh "${MODEL_PATH}" 2>/dev/null | awk '{print $1}')
        
        # Check if this is a LoRA adapter model (small size indicates adapters only)
        SIZE_BYTES=$(du -sb "${MODEL_PATH}" 2>/dev/null | awk '{print $1}')
        if [[ $SIZE_BYTES -lt 100000000 ]]; then  # Less than 100MB
            echo "  ✓ ${MODEL}: ${SIZE} (${SNAPSHOTS} snapshot(s)) [LoRA adapters only]"
        else
            echo "  ✓ ${MODEL}: ${SIZE} (${SNAPSHOTS} snapshot(s))"
        fi
    else
        echo "  ✗ ${MODEL}: NOT FOUND"
    fi
done
echo ""

# Step 6: List ALL cached models for verification
log_info "Step 6: All cached model directories:"
echo ""
if ls -1d "${MODELS_DIR}"/models--* 2>/dev/null | grep -q .; then
    ls -1d "${MODELS_DIR}"/models--* 2>/dev/null | while read -r dir; do
        MODEL_NAME=$(basename "$dir" | sed 's/models--//g' | sed 's/--/\//g')
        # Get size of blobs directory if it exists, otherwise whole directory
        if [[ -d "${dir}/blobs" ]]; then
            SIZE=$(du -sh "${dir}/blobs" 2>/dev/null | awk '{print $1}')
        else
            SIZE=$(du -sh "$dir" 2>/dev/null | awk '{print $1}')
        fi
        SNAPSHOTS=$(find "${dir}/snapshots" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
        echo "  ✓ ${MODEL_NAME}: ${SIZE} (${SNAPSHOTS} snapshot(s))"
    done
else
    log_warning "No models found in ${MODELS_DIR}"
fi
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

