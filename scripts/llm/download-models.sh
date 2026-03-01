#!/usr/bin/env bash
#
# Download all models for the current tier and backend
#
# All HuggingFace models are downloaded to $HOME/.cache/huggingface/hub
# which is then bind-mounted into Docker containers.
#
# Usage:
#   download-models.sh              # Download all models for detected tier
#   download-models.sh --check      # Check if models are cached
#   download-models.sh fast         # Download only fast model
#   download-models.sh marker      # Download Marker/Surya models (in-container)
#
# Environment:
#   LLM_TIER        - Override memory tier (minimal/entry/standard/enhanced)
#   LLM_BACKEND     - Override backend (mlx/vllm/cloud)
#   CONTAINER_PREFIX - Docker container prefix (default: dev)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source UI library for progress display
source "${SCRIPT_DIR}/../lib/ui.sh"

# MLX virtual environment (PEP 668 compliance for modern macOS)
MLX_VENV_DIR="${HOME}/.busibox/mlx-venv"

# Container prefix for marker model downloads
CONTAINER_PREFIX="${CONTAINER_PREFIX:-dev}"

# Get backend and tier
BACKEND="${LLM_BACKEND:-$(bash "${SCRIPT_DIR}/detect-backend.sh")}"
TIER="${LLM_TIER:-$(bash "${SCRIPT_DIR}/get-memory-tier.sh" "$BACKEND")}"

# Download order: most critical first, least needed last
ALL_ROLES=(test fast embed agent voice transcribe image visual-embedding)

# Setup or use the MLX virtual environment
setup_venv() {
    mkdir -p "${HOME}/.busibox"
    if [[ ! -d "$MLX_VENV_DIR" ]]; then
        info "Creating Python virtual environment for model downloads..."
        python3 -m venv "$MLX_VENV_DIR"
    fi
}

get_venv_python() {
    echo "${MLX_VENV_DIR}/bin/python3"
}

get_venv_pip() {
    echo "${MLX_VENV_DIR}/bin/pip3"
}

# Ensure huggingface_hub is installed in venv
ensure_hf_hub() {
    setup_venv
    local py
    py=$(get_venv_python)
    if ! "$py" -c "import huggingface_hub" 2>/dev/null; then
        info "Installing huggingface_hub..."
        "$(get_venv_pip)" install -q huggingface_hub
    fi
}

# Check if a model is cached in HuggingFace cache
check_model_cached() {
    local model="$1"
    local cache_dir="${HOME}/.cache/huggingface/hub"
    # HuggingFace uses double-dash for / separator: org/model -> models--org--model
    local model_dir="${cache_dir}/models--${model//\//--}"
    [[ -d "$model_dir" ]]
}

# Download a single HuggingFace model
download_hf_model() {
    local model="$1"
    local role="$2"

    if [[ -z "$model" ]]; then
        return 0
    fi

    if check_model_cached "$model"; then
        success "${role}: ${model} (cached)"
        return 0
    fi

    info "Downloading ${role}: ${model}..."

    ensure_hf_hub
    local py
    py=$(get_venv_python)

    "$py" -c "
from huggingface_hub import snapshot_download
snapshot_download('${model}', local_dir_use_symlinks=True)
" 2>&1

    if check_model_cached "$model"; then
        success "${role}: ${model}"
    else
        warn "${role}: ${model} — download may have failed"
    fi
}

# Get model name for a role using get-models.sh
# USE_TIER_ONLY=1 forces tier-based resolution for ALL roles (not purpose-based)
# so downloads match the hardware tier, not the dev environment defaults
get_model_for_role() {
    local role="$1"
    local model
    model=$(USE_TIER_ONLY=1 LLM_BACKEND="$BACKEND" LLM_TIER="$TIER" PYTHON_CMD="$(get_venv_python)" bash "${SCRIPT_DIR}/get-models.sh" "$role" 2>/dev/null) || true
    echo "$model"
}

# Check cache status for all models
check_all_models() {
    echo "Checking model cache..."
    echo "Backend: ${BACKEND}, Tier: ${TIER}"
    echo ""

    local all_cached=true

    for role in "${ALL_ROLES[@]}"; do
        local model
        model=$(get_model_for_role "$role")
        if [[ -z "$model" ]]; then
            echo -e "  ${DIM}○${NC} ${role}: (not configured for this tier)"
            continue
        fi
        if check_model_cached "$model"; then
            echo -e "  ${GREEN}✓${NC} ${role}: ${model}"
        else
            echo -e "  ${YELLOW}○${NC} ${role}: ${model} (not cached)"
            all_cached=false
        fi
    done

    echo ""

    # Check Marker/Surya models (only on standard+ tiers)
    if [[ "$TIER" == "standard" || "$TIER" == "enhanced" ]]; then
        echo "Checking Marker/Surya model cache..."
        check_marker_models_cached || all_cached=false
    else
        echo -e "  ${DIM}·${NC} marker: skipped (requires standard+ tier)"
    fi

    echo ""

    if [[ "$all_cached" == true ]]; then
        success "All models cached — ready for offline use"
        return 0
    else
        warn "Some models need to be downloaded"
        return 1
    fi
}

# Download all HuggingFace models for the current tier
download_all_hf_models() {
    info "Downloading all models for ${TIER} tier (${BACKEND})..."
    echo ""

    for role in "${ALL_ROLES[@]}"; do
        local model
        model=$(get_model_for_role "$role")
        if [[ -z "$model" ]]; then
            continue
        fi
        download_hf_model "$model" "$role"
    done
}

# ── Marker / Surya model pre-download ──────────────────────────────────────────
MARKER_MODELS=(
    "text_detection/2025_05_07"
    "text_recognition/2025_09_23"
    "layout/2025_09_23"
    "table_recognition/2025_02_18"
    "ocr_error_detection/2025_02_18"
)

check_marker_models_cached() {
    # Marker models live inside the model_cache volume (mounted at /root/.cache in data-worker)
    # We check via docker exec if available, otherwise skip
    local container="${CONTAINER_PREFIX}-data-worker"
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
        echo -e "  ${DIM}○${NC} marker: data-worker not running — cannot check"
        return 1
    fi

    local all_cached=true
    for model in "${MARKER_MODELS[@]}"; do
        local cached
        cached=$(docker exec "$container" python3 -c "
import os
from surya.settings import settings
path = os.path.join(settings.MODEL_CACHE_DIR, '${model}', 'manifest.json')
print('yes' if os.path.exists(path) else 'no')
" 2>/dev/null || echo "no")
        if [[ "$cached" == "yes" ]]; then
            echo -e "  ${GREEN}✓${NC} marker: ${model}"
        else
            echo -e "  ${YELLOW}○${NC} marker: ${model} (not cached)"
            all_cached=false
        fi
    done

    [[ "$all_cached" == true ]]
}

download_marker_models() {
    info "Pre-downloading Marker/Surya models..."

    local container="${CONTAINER_PREFIX}-data-worker"
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
        warn "data-worker container (${container}) not running — Marker models will download on first use"
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
        warn "Marker model download had errors — models will download on first use"
    fi
}

# Main
main() {
    local target="${1:-all}"

    if [[ "$BACKEND" == "cloud" ]]; then
        info "Cloud backend selected — no local models to download"
        exit 0
    fi

    # Ensure venv with huggingface_hub (and PyYAML) is ready for model resolution
    ensure_hf_hub

    case "$target" in
        --check)
            check_all_models
            ;;
        all)
            download_all_hf_models
            echo ""
            # Marker/Surya models only make sense on standard+ tiers (48GB+)
            # They require significant RAM and won't run on minimal/entry systems
            if [[ "$TIER" == "standard" || "$TIER" == "enhanced" ]]; then
                download_marker_models
            else
                info "Skipping Marker/Surya models (${TIER} tier — requires standard or higher)"
            fi
            echo ""
            success "Model download complete"
            ;;
        marker)
            download_marker_models
            ;;
        fast|agent|embed|whisper|kokoro|flux|colpali)
            local model
            model=$(get_model_for_role "$target")
            if [[ -z "$model" ]]; then
                warn "${target}: not configured for ${TIER}/${BACKEND}"
            else
                download_hf_model "$model" "$target"
            fi
            ;;
        *)
            echo "Usage: $0 [all|fast|agent|embed|whisper|kokoro|flux|colpali|marker|--check]" >&2
            exit 1
            ;;
    esac
}

main "$@"
