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

# Get backend and tier
BACKEND="${LLM_BACKEND:-$(bash "${SCRIPT_DIR}/detect-backend.sh")}"
TIER="${LLM_TIER:-$(bash "${SCRIPT_DIR}/get-memory-tier.sh" "$BACKEND")}"

# Download order: most critical first, least needed last
ALL_ROLES=(test fast embed agent voice transcribe image)

# Find Python 3.10+ (required by outlines/mlx-lm)
find_python310() {
    for candidate in python3.13 python3.12 python3.11 python3.10; do
        local p
        p=$(command -v "$candidate" 2>/dev/null) && { echo "$p"; return 0; }
    done
    for prefix in /opt/homebrew/bin /usr/local/bin; do
        for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
            [[ -x "${prefix}/${candidate}" ]] && {
                local ver
                ver=$("${prefix}/${candidate}" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)
                [[ -n "$ver" && "$ver" -ge 10 ]] && { echo "${prefix}/${candidate}"; return 0; }
            }
        done
    done
    if command -v python3 &>/dev/null; then
        local ver
        ver=$(python3 -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)
        [[ -n "$ver" && "$ver" -ge 10 ]] && { echo "python3"; return 0; }
    fi
    return 1
}

# Setup or use the MLX virtual environment
setup_venv() {
    mkdir -p "${HOME}/.busibox"
    if [[ ! -d "$MLX_VENV_DIR" ]]; then
        local py
        py=$(find_python310) || py="python3"
        info "Creating Python virtual environment for model downloads ($(${py} --version 2>&1))..."
        "$py" -m venv "$MLX_VENV_DIR"
    else
        local venv_ver
        venv_ver=$("${MLX_VENV_DIR}/bin/python3" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
        if [[ "$venv_ver" -lt 10 ]]; then
            warn "MLX venv uses Python 3.${venv_ver} (need 3.10+). Recreating..."
            rm -rf "$MLX_VENV_DIR"
            local py
            py=$(find_python310) || py="python3"
            info "Creating Python virtual environment ($(${py} --version 2>&1))..."
            "$py" -m venv "$MLX_VENV_DIR"
        fi
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

# Check if a model is cached in fastembed cache
check_fastembed_cached() {
    local model="$1"
    local fastembed_cache="${HOME}/.cache/fastembed"
    local model_normalized="${model//\//_}"
    model_normalized="${model_normalized//:/_}"
    local cache_path="${fastembed_cache}/${model_normalized}"
    [[ -d "$cache_path" ]] && [[ -f "$cache_path/model_optimized.onnx" || -f "$cache_path/model.onnx" || -f "$cache_path/onnx/model.onnx" ]]
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

# Download a model into fastembed's cache format
# fastembed uses: ~/.cache/fastembed/{org_model} where / becomes _
download_fastembed_model() {
    local model="$1"
    local role="$2"
    local fastembed_cache="${HOME}/.cache/fastembed"
    local model_normalized="${model//\//_}"
    model_normalized="${model_normalized//:/_}"
    local cache_path="${fastembed_cache}/${model_normalized}"

    if [[ -d "$cache_path" ]] && [[ -f "$cache_path/model_optimized.onnx" || -f "$cache_path/model.onnx" ]]; then
        success "${role}: ${model} (fastembed cached)"
        return 0
    fi

    info "Pre-caching ${role}: ${model} for fastembed..."

    mkdir -p "$cache_path"

    local py
    py=$(get_venv_python)

    "$py" -c "
from huggingface_hub import snapshot_download
snapshot_download('${model}', local_dir='${cache_path}')
" 2>&1

    if [[ -d "$cache_path" ]] && [[ -f "$cache_path/model_optimized.onnx" || -f "$cache_path/model.onnx" || -f "$cache_path/onnx/model.onnx" ]]; then
        success "${role}: ${model} (fastembed cache ready)"
    else
        warn "${role}: ${model} — fastembed pre-cache may need the ONNX file"
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
            # For embed role, also check fastembed cache
            if [[ "$role" == "embed" ]]; then
                if check_fastembed_cached "$model"; then
                    echo -e "  ${GREEN}✓${NC} ${role}: ${model} (hf + fastembed)"
                else
                    echo -e "  ${YELLOW}△${NC} ${role}: ${model} (hf cached, fastembed not cached)"
                    all_cached=false
                fi
            else
                echo -e "  ${GREEN}✓${NC} ${role}: ${model}"
            fi
        else
            echo -e "  ${YELLOW}○${NC} ${role}: ${model} (not cached)"
            all_cached=false
        fi
    done

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

        # Also cache embed models in fastembed format for instant container startup
        if [[ "$role" == "embed" ]]; then
            download_fastembed_model "$model" "$role"
        fi
    done
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
            success "Model download complete"
            ;;
        fast|agent|embed|whisper|kokoro|flux|voice|transcribe|image)
            local model
            model=$(get_model_for_role "$target")
            if [[ -z "$model" ]]; then
                warn "${target}: not configured for ${TIER}/${BACKEND}"
            else
                download_hf_model "$model" "$target"
                # Also cache embed models in fastembed format
                if [[ "$target" == "embed" ]]; then
                    download_fastembed_model "$model" "$target"
                fi
            fi
            ;;
        *)
            echo "Usage: $0 [all|fast|agent|embed|voice|transcribe|image|--check]" >&2
            exit 1
            ;;
    esac
}

main "$@"
