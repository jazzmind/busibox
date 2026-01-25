#!/usr/bin/env bash
#
# Download models for the current tier
#
# Usage:
#   download-models.sh              # Download all models for detected tier
#   download-models.sh fast         # Download only fast model
#   download-models.sh --check      # Check if models are cached
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source UI library for progress display
source "${SCRIPT_DIR}/../lib/ui.sh"

# Get backend and tier
BACKEND="${LLM_BACKEND:-$(bash "${SCRIPT_DIR}/detect-backend.sh")}"
TIER="${LLM_TIER:-$(bash "${SCRIPT_DIR}/get-memory-tier.sh" "$BACKEND")}"

# Get models
eval "$(bash "${SCRIPT_DIR}/get-models.sh" all)"

download_mlx_model() {
    local model="$1"
    
    info "Downloading MLX model: ${model}"
    
    # Check if already cached
    local cache_dir="${HOME}/.cache/huggingface/hub"
    local model_dir="${cache_dir}/models--${model//\//-}"
    
    if [[ -d "$model_dir" ]]; then
        success "Model already cached: ${model}"
        return 0
    fi
    
    # Install huggingface_hub if needed
    if ! python3 -c "import huggingface_hub" 2>/dev/null; then
        info "Installing huggingface_hub..."
        pip3 install -q huggingface_hub
    fi
    
    # Download model
    python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('${model}', local_dir_use_symlinks=True)
"
    
    success "Downloaded: ${model}"
}

download_vllm_model() {
    local model="$1"
    
    info "Downloading vLLM model: ${model}"
    
    # Check if running in container or on host
    if [[ -f /.dockerenv ]]; then
        # Inside container - use vLLM directly
        python3 -c "
from vllm import LLM
LLM('${model}', download_dir='/root/.cache/huggingface')
"
    else
        # On host - use huggingface_hub
        if ! python3 -c "import huggingface_hub" 2>/dev/null; then
            info "Installing huggingface_hub..."
            pip3 install -q huggingface_hub
        fi
        
        python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('${model}')
"
    fi
    
    success "Downloaded: ${model}"
}

check_model_cached() {
    local model="$1"
    local cache_dir="${HOME}/.cache/huggingface/hub"
    local model_dir="${cache_dir}/models--${model//\//-}"
    
    if [[ -d "$model_dir" ]]; then
        return 0
    fi
    return 1
}

download_model() {
    local model="$1"
    
    if [[ "$BACKEND" == "mlx" ]]; then
        download_mlx_model "$model"
    elif [[ "$BACKEND" == "vllm" ]]; then
        download_vllm_model "$model"
    else
        error "Cannot download models for cloud backend"
        return 1
    fi
}

check_all_models() {
    local all_cached=true
    
    echo "Checking model cache..."
    echo ""
    
    for role in fast agent frontier; do
        local model
        model=$(bash "${SCRIPT_DIR}/get-models.sh" "$role")
        
        if check_model_cached "$model"; then
            echo -e "  ${GREEN}✓${NC} ${role}: ${model}"
        else
            echo -e "  ${YELLOW}○${NC} ${role}: ${model} (not cached)"
            all_cached=false
        fi
    done
    
    echo ""
    
    if [[ "$all_cached" == true ]]; then
        success "All models cached - ready for offline use"
        return 0
    else
        warn "Some models need to be downloaded"
        return 1
    fi
}

# Main
main() {
    local target="${1:-all}"
    
    echo ""
    echo "LLM Backend: ${BACKEND}"
    echo "Tier: ${TIER}"
    echo ""
    
    if [[ "$BACKEND" == "cloud" ]]; then
        info "Cloud backend selected - no local models to download"
        exit 0
    fi
    
    case "$target" in
        --check)
            check_all_models
            ;;
        fast)
            download_model "$LLM_MODEL_FAST"
            ;;
        agent)
            download_model "$LLM_MODEL_AGENT"
            ;;
        frontier)
            download_model "$LLM_MODEL_FRONTIER"
            ;;
        all)
            info "Downloading all models for ${TIER} tier..."
            echo ""
            download_model "$LLM_MODEL_FAST"
            download_model "$LLM_MODEL_AGENT"
            download_model "$LLM_MODEL_FRONTIER"
            echo ""
            success "All models downloaded"
            ;;
        *)
            echo "Usage: $0 [fast|agent|frontier|all|--check]" >&2
            exit 1
            ;;
    esac
}

main "$@"
