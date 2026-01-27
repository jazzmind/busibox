#!/usr/bin/env bash
#
# warmup.sh - Pre-download models to cache for offline use
#
# Usage:
#   warmup.sh              # Check status and download missing models
#   warmup.sh --force      # Re-download models (interactive selection)
#
# This script downloads models to the host cache so Docker containers
# can use them immediately without network access.
#
# Models downloaded (based on environment):
#   - Development: bge-small-en-v1.5 (faster, smaller) + Qwen3-0.6B test model
#   - Staging/Prod: bge-large-en-v1.5 (better quality) + Qwen3-0.6B test model
#
# Other MLX models are managed by deploy-api and shown as informational.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${SCRIPT_DIR}/../lib/ui.sh"
source "${SCRIPT_DIR}/../lib/state.sh"

# Cache directories
HF_CACHE_DIR="${HOME}/.cache/huggingface/hub"
FASTEMBED_CACHE_DIR="${HOME}/.cache/fastembed"

# Flags
FORCE_MODE=false

# =============================================================================
# ARGUMENT PARSING
# =============================================================================

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)
            FORCE_MODE=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--force]"
            echo ""
            echo "Options:"
            echo "  --force   Re-download models (interactive selection)"
            echo ""
            echo "By default, shows cache status and downloads any missing models."
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# =============================================================================
# ENVIRONMENT DETECTION
# =============================================================================

get_embedding_model() {
    # Check state first
    local model_from_state
    model_from_state=$(get_state "FASTEMBED_MODEL" "")
    
    if [[ -n "$model_from_state" ]]; then
        echo "$model_from_state"
        return
    fi
    
    # Check environment variable
    if [[ -n "${FASTEMBED_MODEL:-}" ]]; then
        echo "$FASTEMBED_MODEL"
        return
    fi
    
    # Default based on environment
    local env
    env=$(get_state "ENVIRONMENT" "development")
    
    case "$env" in
        staging|production)
            echo "BAAI/bge-large-en-v1.5"
            ;;
        *)
            # development, demo, or unset
            echo "BAAI/bge-small-en-v1.5"
            ;;
    esac
}

get_warmup_mlx_model() {
    # For warmup, we only download a small test model
    # Other models are managed by deploy-api
    echo "mlx-community/Qwen3-0.6B-4bit"
}

# =============================================================================
# VIRTUAL ENVIRONMENT SETUP (for MLX models only)
# =============================================================================
# MLX models are downloaded using huggingface_hub in a local venv.
# Embedding models are downloaded using Docker (fastembed requires Python <3.13).

MLX_VENV_DIR="${HOME}/.busibox/mlx-venv"

setup_mlx_venv() {
    mkdir -p "${HOME}/.busibox"
    if [[ ! -d "$MLX_VENV_DIR" ]]; then
        info "Creating MLX virtual environment..."
        python3 -m venv "$MLX_VENV_DIR"
    fi
}

get_mlx_python() {
    echo "${MLX_VENV_DIR}/bin/python3"
}

get_mlx_pip() {
    echo "${MLX_VENV_DIR}/bin/pip3"
}

ensure_mlx_dependencies() {
    setup_mlx_venv
    local pip
    pip=$(get_mlx_pip)
    
    # Need huggingface_hub for MLX models
    if ! "$(get_mlx_python)" -c "import huggingface_hub" 2>/dev/null; then
        info "Installing huggingface_hub..."
        "$pip" install -q huggingface_hub || {
            error "Failed to install huggingface_hub"
            return 1
        }
    fi
}

# =============================================================================
# MODEL CACHE CHECKING
# =============================================================================

check_embedding_model() {
    local model="$1"
    
    # FastEmbed uses HuggingFace hub cache format
    # BAAI/bge-small-en-v1.5 is stored as qdrant/bge-small-en-v1.5-onnx-q
    # Extract the model size (small/base/large) to match the right cached model
    local model_size=""
    case "$model" in
        *small*) model_size="small" ;;
        *base*) model_size="base" ;;
        *large*) model_size="large" ;;
    esac
    
    # Check for matching model in cache
    if [[ -n "$model_size" ]]; then
        if find "${FASTEMBED_CACHE_DIR}" -name "model*.onnx" -path "*bge-${model_size}*" 2>/dev/null | grep -q .; then
            return 0
        fi
    fi
    
    # Also check the old-style cache format just in case
    local model_normalized="${model//\//_}"
    model_normalized="${model_normalized//:/_}"
    local model_dir="${FASTEMBED_CACHE_DIR}/${model_normalized}"
    
    if [[ -d "$model_dir" ]] && [[ -f "${model_dir}/model.onnx" || -f "${model_dir}/model_optimized.onnx" ]]; then
        return 0
    fi
    
    return 1
}

check_mlx_model() {
    local model="$1"
    # HuggingFace cache format: models--{owner}--{repo} where / becomes --
    local model_dir="${HF_CACHE_DIR}/models--${model//\//--}"
    
    if [[ -d "$model_dir" ]]; then
        return 0
    fi
    return 1
}

# Get other cached MLX models (informational only)
get_other_cached_mlx_models() {
    local warmup_model="$1"
    # HuggingFace cache format: models--{owner}--{repo} where / becomes --
    local warmup_normalized="models--${warmup_model//\//--}"
    
    if [[ ! -d "$HF_CACHE_DIR" ]]; then
        return
    fi
    
    # Find cached MLX models (mlx-community)
    for dir in "$HF_CACHE_DIR"/models--mlx-community--*; do
        if [[ -d "$dir" ]]; then
            local dirname=$(basename "$dir")
            # Skip the warmup model
            if [[ "$dirname" != "$warmup_normalized" ]]; then
                # Convert back to model name
                # models--mlx-community--Qwen3-0.6B-4bit -> mlx-community/Qwen3-0.6B-4bit
                local model_name="${dirname#models--}"
                model_name="${model_name/--//}"  # First -- becomes /
                echo "$model_name"
            fi
        fi
    done
}

# =============================================================================
# STATUS DISPLAY
# =============================================================================

# Track what needs downloading
MISSING_REQUIRED=()

show_cache_status() {
    MISSING_REQUIRED=()
    
    local embedding_model
    embedding_model=$(get_embedding_model)
    
    local warmup_mlx_model
    warmup_mlx_model=$(get_warmup_mlx_model)
    
    local env
    env=$(get_state "ENVIRONMENT" "development")
    
    echo ""
    header "Model Cache Status"
    echo ""
    echo -e "${DIM}Environment: ${env}${NC}"
    echo ""
    
    # Embedding model
    echo -e "${BOLD}Embedding Model:${NC}"
    if check_embedding_model "$embedding_model"; then
        echo -e "  ${GREEN}✓${NC} ${embedding_model}"
        local model_normalized="${embedding_model//\//_}"
        model_normalized="${model_normalized//:/_}"
        echo -e "    ${DIM}Location: ${FASTEMBED_CACHE_DIR}/${model_normalized}${NC}"
    else
        echo -e "  ${YELLOW}○${NC} ${embedding_model} ${DIM}(not cached)${NC}"
        MISSING_REQUIRED+=("embedding:${embedding_model}")
    fi
    echo ""
    
    # MLX models (only on Apple Silicon)
    if [[ "$(uname -m)" == "arm64" && "$(uname)" == "Darwin" ]]; then
        echo -e "${BOLD}MLX Test Model (for warmup):${NC}"
        if check_mlx_model "$warmup_mlx_model"; then
            echo -e "  ${GREEN}✓${NC} ${warmup_mlx_model}"
        else
            echo -e "  ${YELLOW}○${NC} ${warmup_mlx_model} ${DIM}(not cached)${NC}"
            MISSING_REQUIRED+=("mlx:${warmup_mlx_model}")
        fi
        echo ""
        
        # Show other cached models (informational)
        local other_models
        other_models=$(get_other_cached_mlx_models "$warmup_mlx_model")
        if [[ -n "$other_models" ]]; then
            echo -e "${BOLD}Other Cached MLX Models:${NC} ${DIM}(managed by deploy-api)${NC}"
            while IFS= read -r model; do
                if [[ -n "$model" ]]; then
                    echo -e "  ${GREEN}✓${NC} ${model}"
                fi
            done <<< "$other_models"
            echo ""
        fi
    fi
    
    # Summary
    if [[ ${#MISSING_REQUIRED[@]} -eq 0 ]]; then
        success "All required models cached - ready for offline use"
    else
        warn "${#MISSING_REQUIRED[@]} required model(s) not cached"
    fi
}

# =============================================================================
# DOWNLOAD FUNCTIONS
# =============================================================================

# Get embedding dimension based on model
get_embedding_dimension() {
    local model="$1"
    case "$model" in
        *small*) echo "384" ;;
        *base*) echo "768" ;;
        *large*) echo "1024" ;;
        *) echo "1024" ;;  # Default to large dimension
    esac
}

# Save FastEmbed config to both state and env file
save_fastembed_config() {
    local model="$1"
    local env_file
    env_file=$(get_env_file_path)
    
    # Determine embedding dimension based on model
    local dimension
    dimension=$(get_embedding_dimension "$model")
    
    # Save to state
    set_state "FASTEMBED_MODEL" "$model"
    set_state "FASTEMBED_HOST_CACHE" "$FASTEMBED_CACHE_DIR"
    set_state "EMBEDDING_DIMENSION" "$dimension"
    
    # Also save to env file for Docker Compose
    if [[ -f "$env_file" ]]; then
        # Update FASTEMBED_MODEL
        if grep -q "^FASTEMBED_MODEL=" "$env_file" 2>/dev/null; then
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sed -i '' "s|^FASTEMBED_MODEL=.*|FASTEMBED_MODEL=${model}|" "$env_file"
            else
                sed -i "s|^FASTEMBED_MODEL=.*|FASTEMBED_MODEL=${model}|" "$env_file"
            fi
        else
            echo "" >> "$env_file"
            echo "# FastEmbed model for embedding-api" >> "$env_file"
            echo "FASTEMBED_MODEL=${model}" >> "$env_file"
        fi
        
        # Update FASTEMBED_HOST_CACHE
        if grep -q "^FASTEMBED_HOST_CACHE=" "$env_file" 2>/dev/null; then
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sed -i '' "s|^FASTEMBED_HOST_CACHE=.*|FASTEMBED_HOST_CACHE=${FASTEMBED_CACHE_DIR}|" "$env_file"
            else
                sed -i "s|^FASTEMBED_HOST_CACHE=.*|FASTEMBED_HOST_CACHE=${FASTEMBED_CACHE_DIR}|" "$env_file"
            fi
        else
            echo "FASTEMBED_HOST_CACHE=${FASTEMBED_CACHE_DIR}" >> "$env_file"
        fi
        
        # Update EMBEDDING_DIMENSION
        if grep -q "^EMBEDDING_DIMENSION=" "$env_file" 2>/dev/null; then
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sed -i '' "s|^EMBEDDING_DIMENSION=.*|EMBEDDING_DIMENSION=${dimension}|" "$env_file"
            else
                sed -i "s|^EMBEDDING_DIMENSION=.*|EMBEDDING_DIMENSION=${dimension}|" "$env_file"
            fi
        else
            echo "EMBEDDING_DIMENSION=${dimension}" >> "$env_file"
        fi
        
        info "Set embedding dimension to ${dimension} for model ${model}"
    fi
}

download_embedding_model() {
    local model="$1"
    
    if check_embedding_model "$model"; then
        success "Embedding model already cached: ${model}"
        # Still save to state and env
        save_fastembed_config "$model"
        return 0
    fi
    
    info "Downloading embedding model: ${model}"
    
    # Estimate size based on model
    local size_info=""
    case "$model" in
        *small*) size_info="(~134MB)" ;;
        *base*) size_info="(~438MB)" ;;
        *large*) size_info="(~1.3GB)" ;;
    esac
    [[ -n "$size_info" ]] && info "Size: ${size_info}"
    
    # Check if Docker is available
    if ! command -v docker &>/dev/null; then
        warn "Docker not available - embedding model will be downloaded on first container start"
        return 0
    fi
    
    # Use Docker to download the model (fastembed requires Python <3.13)
    # Mount the local cache directory so the model persists
    mkdir -p "$FASTEMBED_CACHE_DIR"
    
    info "Using Docker to download model (fastembed requires Python <3.13)..."
    
    docker run --rm \
        -v "${FASTEMBED_CACHE_DIR}:/root/.cache/fastembed" \
        python:3.11-slim \
        bash -c "
            pip install -q fastembed && \
            python -c \"
from fastembed import TextEmbedding
model = '${model}'
cache_dir = '/root/.cache/fastembed'
print(f'Downloading {model}...')
embedder = TextEmbedding(model_name=model, cache_dir=cache_dir)
list(embedder.embed(['warmup test']))
print('Download complete!')
\"
        " || {
        error "Failed to download embedding model"
        return 1
    }
    
    success "Embedding model downloaded: ${model}"
    
    # Save to state and env
    save_fastembed_config "$model"
}

download_mlx_model() {
    local model="$1"
    
    if check_mlx_model "$model"; then
        success "MLX model already cached: ${model}"
        return 0
    fi
    
    info "Downloading MLX model: ${model}"
    
    # Ensure huggingface_hub is installed (in MLX venv)
    ensure_mlx_dependencies || return 1
    
    "$(get_mlx_python)" -c "
from huggingface_hub import snapshot_download
import sys

model = '${model}'
print(f'Downloading {model}...')
try:
    snapshot_download(model, local_dir_use_symlinks=True)
    print('Download complete!')
except Exception as e:
    print(f'Download failed: {e}', file=sys.stderr)
    exit(1)
" || {
        warn "Failed to download: ${model}"
        return 1
    }
    
    success "MLX model downloaded: ${model}"
}

# =============================================================================
# FORCE MODE - Interactive model selection
# =============================================================================

show_force_menu() {
    local embedding_model
    embedding_model=$(get_embedding_model)
    
    local warmup_mlx_model
    warmup_mlx_model=$(get_warmup_mlx_model)
    
    echo ""
    header "Select Models to Re-download"
    echo ""
    
    local options=()
    local i=1
    
    # Add embedding model
    local emb_status="cached"
    check_embedding_model "$embedding_model" || emb_status="missing"
    options+=("embedding:${embedding_model}")
    echo -e "  ${BOLD}${i})${NC} Embedding: ${embedding_model} ${DIM}(${emb_status})${NC}"
    ((i++))
    
    # Add MLX warmup model (only on Apple Silicon)
    if [[ "$(uname -m)" == "arm64" && "$(uname)" == "Darwin" ]]; then
        local mlx_status="cached"
        check_mlx_model "$warmup_mlx_model" || mlx_status="missing"
        options+=("mlx:${warmup_mlx_model}")
        echo -e "  ${BOLD}${i})${NC} MLX: ${warmup_mlx_model} ${DIM}(${mlx_status})${NC}"
        ((i++))
    fi
    
    echo ""
    echo -e "  ${BOLD}a)${NC} All models"
    echo -e "  ${BOLD}q)${NC} Quit"
    echo ""
    
    read -p "Select model(s) to download (e.g., 1,2 or a for all): " selection
    
    if [[ "$selection" == "q" || "$selection" == "Q" ]]; then
        echo "Cancelled."
        exit 0
    fi
    
    # Dependencies are installed per-download function now
    
    if [[ "$selection" == "a" || "$selection" == "A" ]]; then
        # Download all
        for opt in "${options[@]}"; do
            local type="${opt%%:*}"
            local model="${opt#*:}"
            if [[ "$type" == "embedding" ]]; then
                download_embedding_model_force "$model"
            else
                download_mlx_model_force "$model"
            fi
        done
    else
        # Parse comma-separated selection
        IFS=',' read -ra selections <<< "$selection"
        for sel in "${selections[@]}"; do
            sel=$(echo "$sel" | tr -d ' ')  # trim whitespace
            if [[ "$sel" =~ ^[0-9]+$ ]] && [[ $sel -ge 1 ]] && [[ $sel -le ${#options[@]} ]]; then
                local opt="${options[$((sel-1))]}"
                local type="${opt%%:*}"
                local model="${opt#*:}"
                if [[ "$type" == "embedding" ]]; then
                    download_embedding_model_force "$model"
                else
                    download_mlx_model_force "$model"
                fi
            else
                warn "Invalid selection: $sel"
            fi
        done
    fi
    
    echo ""
    show_cache_status
}

# Force download embedding model (removes cache first)
download_embedding_model_force() {
    local model="$1"
    local model_normalized="${model//\//_}"
    model_normalized="${model_normalized//:/_}"
    local model_dir="${FASTEMBED_CACHE_DIR}/${model_normalized}"
    
    if [[ -d "$model_dir" ]]; then
        info "Removing cached embedding model..."
        rm -rf "$model_dir"
    fi
    
    download_embedding_model "$model"
}

# Force download MLX model (removes cache first)
download_mlx_model_force() {
    local model="$1"
    local model_dir="${HF_CACHE_DIR}/models--${model//\//-}"
    
    if [[ -d "$model_dir" ]]; then
        info "Removing cached MLX model..."
        rm -rf "$model_dir"
    fi
    
    download_mlx_model "$model"
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    echo ""
    header "Busibox Model Warmup"
    
    # Show current status first
    show_cache_status
    
    if [[ "$FORCE_MODE" == true ]]; then
        show_force_menu
        exit 0
    fi
    
    # If all required models are cached, we're done
    if [[ ${#MISSING_REQUIRED[@]} -eq 0 ]]; then
        echo ""
        info "All required models are cached. Use --force to re-download."
        exit 0
    fi
    
    # Download missing models
    echo ""
    header "Downloading Missing Models"
    echo ""
    
    # Dependencies are installed per-download function now
    
    local download_failed=false
    
    for entry in "${MISSING_REQUIRED[@]}"; do
        local type="${entry%%:*}"
        local model="${entry#*:}"
        
        if [[ "$type" == "embedding" ]]; then
            download_embedding_model "$model" || download_failed=true
        else
            download_mlx_model "$model" || download_failed=true
        fi
        echo ""
    done
    
    # Show final status
    show_cache_status
    
    if [[ "$download_failed" == true ]]; then
        exit 1
    fi
}

main "$@"
