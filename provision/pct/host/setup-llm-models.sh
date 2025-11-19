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
set -eo pipefail

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

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Host directory for shared model cache
# IMPORTANT: Must match the path created by setup-proxmox-host.sh
HUGGINGFACE_CACHE="/var/lib/llm-models/huggingface"
# HuggingFace stores models in hub/ subdirectory
MODELS_DIR="${HUGGINGFACE_CACHE}/hub"
VENV_DIR="/opt/model-downloader"

# HuggingFace token (required for gated models like PaliGemma)
# Set HF_TOKEN environment variable or create /root/.huggingface/token file
# Get token from: https://huggingface.co/settings/tokens
HF_TOKEN="${HF_TOKEN:-}"
if [[ -z "$HF_TOKEN" ]] && [[ -f "$HOME/.huggingface/token" ]]; then
    HF_TOKEN=$(cat "$HOME/.huggingface/token")
fi

# Models to pre-download
# These should match the model_registry.yml in provision/ansible/group_vars/all/
# Update model_registry.yml to change which models are used for which purposes
MODELS=(
    "microsoft/Phi-4-multimodal-instruct"  # fast.model_name - Fast chat model (6B parameters, GPU 0)
    "Qwen/Qwen3-Embedding-8B"              # embedding.model_name - Text embeddings (8B parameters, 4096 dims, GPU 1)
    "google/paligemma-3b-pt-448"           # Required by ColPali - PaliGemma-3B base model
    "vidore/colpali-v1.3"                  # visual-embedding.model_name - ColPali LoRA adapters for PDF embeddings (GPU 2)
    "Qwen/Qwen3-VL-8B-Instruct"            # vision.model_name - Vision-language model (8B parameters, GPU 1)
    "Qwen/Qwen3-30B-A3B-Instruct-2507"     # default.model_name - General purpose chat (30B parameters, GPU 1)
    # Marker/Surya models for PDF extraction
    "vikp/surya_det2"                     # Marker layout detection model (~1.4GB)
    "vikp/surya_ocr"                       # Marker OCR model (~500MB)
)

echo "=========================================="
echo "LLM and Marker Model Pre-Download"
echo "=========================================="
log_info "This will download models to: ${HUGGINGFACE_CACHE}"
log_info "Includes: LLM models (vLLM) and Marker/Surya models (PDF extraction)"
echo ""

# Check for HuggingFace authentication
if [[ -z "$HF_TOKEN" ]]; then
    log_warning "No HuggingFace token found!"
    log_warning "Some models (like PaliGemma) are gated and require authentication."
    echo ""
    log_info "To fix this:"
    log_info "1. Get a token from: https://huggingface.co/settings/tokens"
    log_info "2. Accept the license at: https://huggingface.co/google/paligemma-3b-pt-448"
    log_info "3. Run: huggingface-cli login"
    log_info "   OR set HF_TOKEN environment variable"
    log_info "   OR create file: $HOME/.huggingface/token"
    echo ""
    log_warning "Continuing anyway - gated models will fail..."
    echo ""
else
    log_success "HuggingFace token found (${#HF_TOKEN} characters)"
    echo ""
fi

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
        
        # Update model configuration database for cached models too (non-interactive)
        if [ -f "${SCRIPT_DIR}/update-model-config.sh" ]; then
            "${SCRIPT_DIR}/update-model-config.sh" --non-interactive "${MODEL}" 2>/dev/null || true
        fi
    else
        log_info "↓ Downloading ${MODEL}..."
        
        # Show estimated size and time
        case "${MODEL}" in
            *"30B"*) log_info "  Estimated: ~57GB | ETA: 30-60 min" ;;
            *"paligemma"*) log_info "  Estimated: ~11GB | ETA: 10-20 min" ;;
            *"Embedding"*|*"Phi-4"*|*"Qwen3-VL"*) log_info "  Estimated: ~12-17GB | ETA: 10-30 min" ;;
            *"colpali"*) log_info "  Estimated: ~20MB | ETA: 1-2 min" ;;
            *"surya_det2"*) log_info "  Estimated: ~1.4GB | ETA: 2-5 min" ;;
            *"surya_ocr"*) log_info "  Estimated: ~500MB | ETA: 1-3 min" ;;
        esac
        
        # Download with HF token - progress bars will show automatically
        HF_HOME="${HUGGINGFACE_CACHE}" HF_TOKEN="${HF_TOKEN}" "${VENV_DIR}/bin/python3" << EOF
from huggingface_hub import snapshot_download
import os
import sys

model_name = '${MODEL}'
hf_token = os.environ.get('HF_TOKEN', None)

try:
    cache_dir = snapshot_download(
        model_name, 
        resume_download=True,
        token=hf_token if hf_token else None
    )
    parent_dir = os.path.dirname(cache_dir)
    models_dir = os.path.dirname(parent_dir)
    model_name_final = os.path.basename(models_dir)
    print(f"CACHE_DIR:{cache_dir}")
    print(f"MODEL_DIR:{model_name_final}")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
EOF
        
        if [ $? -eq 0 ]; then
            log_success "✓ ${MODEL} downloaded"
            
            # Update model configuration database (non-interactive mode)
            if [ -f "${SCRIPT_DIR}/update-model-config.sh" ]; then
                log_info "  Analyzing model configuration..."
                "${SCRIPT_DIR}/update-model-config.sh" --non-interactive "${MODEL}" 2>/dev/null || log_warning "  Failed to analyze model (non-fatal)"
            fi
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
log_info "4. Update model configuration database (optional but recommended):"
log_info "   bash ${SCRIPT_DIR}/update-model-config.sh"
log_info "   This analyzes downloaded models and updates memory estimation config"
echo ""
log_info "5. vLLM will use these pre-downloaded models (no re-download needed)"
echo ""

