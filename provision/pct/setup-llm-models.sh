#!/bin/bash
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

# Host directories for shared models
OLLAMA_MODELS_DIR="/var/lib/ollama-models"
VLLM_MODELS_DIR="/var/lib/vllm-models"
HUGGINGFACE_CACHE="/var/lib/huggingface-cache"

log_info "Setting up shared LLM model storage on Proxmox host..."
echo ""

# Step 1: Create host directories
log_info "Step 1: Creating model directories on host..."
mkdir -p "${OLLAMA_MODELS_DIR}"
mkdir -p "${VLLM_MODELS_DIR}"
mkdir -p "${HUGGINGFACE_CACHE}"

log_success "Directories created:"
log_info "  Ollama models: ${OLLAMA_MODELS_DIR}"
log_info "  vLLM models:   ${VLLM_MODELS_DIR}"
log_info "  HuggingFace:   ${HUGGINGFACE_CACHE}"
echo ""

# Step 2: Pre-download Ollama model
log_info "Step 2: Pre-downloading Ollama test model..."

if [[ ! -f "${OLLAMA_MODELS_DIR}/manifests/registry.ollama.ai/library/qwen2.5/0.5b" ]]; then
    log_info "Installing Ollama on host (if not already installed)..."
    if ! command -v ollama &>/dev/null; then
        curl -fsSL https://ollama.com/install.sh | sh
    fi
    
    log_info "Downloading qwen2.5:0.5b model..."
    OLLAMA_MODELS="${OLLAMA_MODELS_DIR}" ollama pull qwen2.5:0.5b
    
    log_success "Ollama model downloaded"
else
    log_success "Ollama model already downloaded"
fi
echo ""

# Step 3: Pre-download vLLM/HuggingFace model
log_info "Step 3: Pre-downloading vLLM test model..."

if [[ ! -d "${HUGGINGFACE_CACHE}/models--Qwen--Qwen2.5-0.5B-Instruct" ]]; then
    log_info "Installing Python and huggingface-cli..."
    apt-get update -qq
    apt-get install -y python3-pip &>/dev/null || true
    pip3 install -q huggingface-hub || true
    
    log_info "Downloading Qwen/Qwen2.5-0.5B-Instruct model..."
    HF_HOME="${HUGGINGFACE_CACHE}" python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen2.5-0.5B-Instruct')
"
    log_success "vLLM model downloaded"
else
    log_success "vLLM model already downloaded"
fi
echo ""

# Step 4: Show model sizes
log_info "Step 4: Model storage summary..."
echo ""
log_info "Disk usage:"
du -sh "${OLLAMA_MODELS_DIR}" 2>/dev/null || echo "  Ollama: 0B"
du -sh "${HUGGINGFACE_CACHE}" 2>/dev/null || echo "  HuggingFace: 0B"
echo ""

log_success "=========================================="
log_success "Model pre-download complete!"
log_success "=========================================="
echo ""
log_info "Next steps:"
log_info "1. Update Ansible inventory to mount these directories"
log_info "2. Deploy containers with:"
log_info "   bash deploy-llm-stack.sh test"
echo ""
log_info "Model directories to mount in containers:"
log_info "  Ollama:  ${OLLAMA_MODELS_DIR} -> /var/lib/ollama/models"
log_info "  vLLM:    ${HUGGINGFACE_CACHE} -> /root/.cache/huggingface"
echo ""

