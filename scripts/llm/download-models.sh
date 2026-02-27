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

# MLX virtual environment (PEP 668 compliance for modern macOS)
MLX_VENV_DIR="${HOME}/.busibox/mlx-venv"

# Setup or use the MLX virtual environment
setup_mlx_venv() {
    mkdir -p "${HOME}/.busibox"
    if [[ ! -d "$MLX_VENV_DIR" ]]; then
        info "Creating MLX virtual environment..."
        python3 -m venv "$MLX_VENV_DIR"
    fi
}

# Get venv python path
get_mlx_python() {
    echo "${MLX_VENV_DIR}/bin/python3"
}

# Get venv pip path
get_mlx_pip() {
    echo "${MLX_VENV_DIR}/bin/pip3"
}

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
    
    # Setup venv and get paths
    setup_mlx_venv
    local mlx_python
    local mlx_pip
    mlx_python=$(get_mlx_python)
    mlx_pip=$(get_mlx_pip)
    
    # Install huggingface_hub if needed
    if ! "$mlx_python" -c "import huggingface_hub" 2>/dev/null; then
        info "Installing huggingface_hub into virtual environment..."
        "$mlx_pip" install -q huggingface_hub
    fi
    
    # Download model
    "$mlx_python" -c "
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
        # On host - use huggingface_hub via venv (PEP 668 compliance)
        setup_mlx_venv
        local mlx_python
        local mlx_pip
        mlx_python=$(get_mlx_python)
        mlx_pip=$(get_mlx_pip)
        
        if ! "$mlx_python" -c "import huggingface_hub" 2>/dev/null; then
            info "Installing huggingface_hub into virtual environment..."
            "$mlx_pip" install -q huggingface_hub
        fi
        
        "$mlx_python" -c "
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

# ── Marker / Surya model pre-download ──────────────────────────────────────────
# Downloads models into the Docker model_cache volume (mounted at /root/.cache)
# so the data-worker doesn't need to fetch them on first document processing.
# Uses the surya S3 download API via a one-shot Python script inside the
# data-worker container.

MARKER_MODELS=(
    "text_detection/2025_05_07"
    "text_recognition/2025_09_23"
    "layout/2025_09_23"
    "table_recognition/2025_02_18"
    "ocr_error_detection/2025_02_18"
)

check_marker_models_cached() {
    local cache_dir="${HOME}/.cache/datalab/models"
    local all_cached=true

    for model in "${MARKER_MODELS[@]}"; do
        local model_dir="${cache_dir}/${model}"
        if [[ -d "$model_dir" && -f "${model_dir}/manifest.json" ]]; then
            echo -e "  ${GREEN}✓${NC} marker: ${model}"
        else
            echo -e "  ${YELLOW}○${NC} marker: ${model} (not cached)"
            all_cached=false
        fi
    done

    $all_cached
}

download_marker_models() {
    info "Pre-downloading Marker/Surya models into model_cache volume..."

    # Run inside the data-worker container so we have surya installed
    local container="dev-data-worker"
    if ! docker ps --format '{{.Names}}' | grep -q "^${container}$"; then
        warn "data-worker container not running -- Marker models will download on first use"
        return 0
    fi

    docker exec "$container" python3 -c "
import os, sys
from surya.common.s3 import download_directory
from surya.settings import settings

models = [
    ('text_detection/2025_05_07',       settings.DETECTOR_MODEL_CHECKPOINT),
    ('text_recognition/2025_09_23',     settings.RECOGNITION_MODEL_CHECKPOINT),
    ('layout/2025_09_23',               settings.LAYOUT_MODEL_CHECKPOINT),
    ('table_recognition/2025_02_18',    settings.TABLE_REC_MODEL_CHECKPOINT),
    ('ocr_error_detection/2025_02_18',  settings.OCR_ERROR_MODEL_CHECKPOINT),
]

cache_dir = settings.MODEL_CACHE_DIR
for model_path, checkpoint in models:
    local_path = os.path.join(cache_dir, model_path)
    manifest = os.path.join(local_path, 'manifest.json')
    if os.path.exists(manifest):
        print(f'  Already cached: {model_path}')
        continue
    os.makedirs(local_path, exist_ok=True)
    print(f'  Downloading: {model_path}')
    download_directory(model_path, local_path)
    print(f'  Done: {model_path}')

print('All Marker/Surya models cached.')
" 2>&1

    if [[ $? -eq 0 ]]; then
        success "Marker/Surya models cached"
    else
        warn "Marker model download had errors -- models will download on first use"
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
            echo ""
            echo "Checking Marker/Surya model cache..."
            check_marker_models_cached
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
        marker)
            download_marker_models
            ;;
        all)
            info "Downloading all models for ${TIER} tier..."
            echo ""
            download_model "$LLM_MODEL_FAST"
            download_model "$LLM_MODEL_AGENT"
            download_model "$LLM_MODEL_FRONTIER"
            echo ""
            download_marker_models
            echo ""
            success "All models downloaded"
            ;;
        *)
            echo "Usage: $0 [fast|agent|frontier|all|marker|--check]" >&2
            exit 1
            ;;
    esac
}

main "$@"
