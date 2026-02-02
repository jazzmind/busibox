#!/bin/bash
#
# Pre-download Embedding Models to Proxmox Host
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Pre-download and cache FastEmbed embedding models to shared storage before container deployment
#
# USAGE:
#   bash setup-embedding-models.sh              # Download models from registry
#   bash setup-embedding-models.sh --cleanup    # Remove models not in registry
#
# WHAT IT DOES:
#   1. Creates shared embedding model directory on Proxmox host
#   2. Downloads FastEmbed models from HuggingFace
#   3. Models are mounted into LXC containers via bind mounts
#   4. (cleanup mode) Removes orphaned models with confirmation
#
# WHY:
#   - Avoids downloading models during container deployment
#   - Saves bandwidth and time
#   - Models shared across multiple containers (data-lxc, embedding-api-lxc)
#   - Follows same pattern as setup-llm-models.sh
#   - Cleanup saves disk space by removing unused models
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

# Parse command line arguments
CLEANUP_MODE=false
STAGE_ARG=""

# First arg might be stage (production/staging/development)
if [[ -n "${1:-}" ]] && [[ ! "$1" =~ ^-- ]]; then
    STAGE_ARG="$1"
    shift
fi

for arg in "$@"; do
    case "$arg" in
        --cleanup)
            CLEANUP_MODE=true
            ;;
        *)
            log_error "Unknown argument: $arg"
            echo "Usage: $0 [stage] [--cleanup]"
            echo "  stage: production|staging|development (optional)"
            exit 1
            ;;
    esac
done

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Host directory for shared embedding model cache
# IMPORTANT: Must match the path expected by Ansible roles
FASTEMBED_CACHE="/var/lib/embedding-models/fastembed"
VENV_DIR="/opt/embedding-downloader"

# Only show download header if not in cleanup mode
if [ "$CLEANUP_MODE" = false ]; then
    echo "=========================================="
    echo "Embedding Model Pre-Download for FastEmbed"
    echo "=========================================="
    log_info "This will download models to: ${FASTEMBED_CACHE}"
    echo ""
fi

# Check if running on Proxmox
if ! command -v pct &>/dev/null; then
    log_error "This script must be run on a Proxmox host"
    exit 1
fi

# Check if running as root
if [[ "$(id -u)" != "0" ]]; then
    log_error "This script must be run as root"
    exit 1
fi

# =============================================================================
# SETUP PYTHON ENVIRONMENT
# =============================================================================

setup_python_environment() {
    log_info "Setting up Python environment..."
    
    # Install Python if not present
    if ! command -v python3 &>/dev/null; then
        log_info "Installing Python..."
        apt-get update -qq
        apt-get install -y -qq python3 python3-venv python3-pip
    fi
    
    # Create virtual environment if it doesn't exist
    if [[ ! -d "$VENV_DIR" ]]; then
        log_info "Creating Python virtual environment..."
        python3 -m venv "$VENV_DIR"
    fi
    
    # Activate virtual environment
    source "${VENV_DIR}/bin/activate"
    
    # Install/upgrade fastembed
    log_info "Installing fastembed library..."
    pip install -q --upgrade pip
    pip install -q fastembed
    
    log_success "Python environment ready"
}

# =============================================================================
# HARDWARE & ENVIRONMENT DETECTION
# =============================================================================

detect_hardware() {
    # Detect if Apple Silicon
    if [[ "$(uname -m)" == "arm64" ]] && [[ "$(uname -s)" == "Darwin" ]]; then
        echo "apple_silicon"
    # Detect NVIDIA GPU
    elif command -v nvidia-smi &>/dev/null; then
        echo "nvidia"
    # Default to CPU
    else
        echo "cpu"
    fi
}

detect_stage() {
    # Use stage from command line arg if provided
    local stage="${STAGE_ARG}"
    
    # If not provided, try to detect from hostname or default to production
    if [[ -z "$stage" ]]; then
        if [[ "$(hostname)" =~ -stage- ]] || [[ "$(hostname)" =~ STAGE ]]; then
            stage="staging"
        elif [[ "$(hostname)" =~ -dev- ]] || [[ "$(hostname)" =~ DEV ]]; then
            stage="development"
        else
            stage="production"  # Default to production for safety
        fi
    fi
    
    echo "$stage"
}

HARDWARE=$(detect_hardware)
STAGE=$(detect_stage)

log_info "Hardware: ${HARDWARE}"
log_info "Stage: ${STAGE}"
echo ""

# =============================================================================
# MODEL REGISTRY
# =============================================================================

# FastEmbed models to download based on hardware and stage
# These match the models in provision/ansible/group_vars/all/model_registry.yml

determine_embedding_models() {
    local models=()
    
    case "${STAGE}" in
        development)
            # Dev only needs small model for fast testing
            models+=("BAAI/bge-small-en-v1.5")
            ;;
        staging|production)
            # Prod/staging need large model for best quality
            models+=("BAAI/bge-large-en-v1.5")
            ;;
    esac
    
    echo "${models[@]}"
}

# Get models for this environment
EMBEDDING_MODELS=($(determine_embedding_models))

# Model size estimates
declare -A MODEL_SIZES=(
    ["BAAI/bge-small-en-v1.5"]="134MB"
    ["BAAI/bge-base-en-v1.5"]="438MB"
    ["BAAI/bge-large-en-v1.5"]="1.3GB"
)

# =============================================================================
# MODEL CHECKING
# =============================================================================

check_model_cached() {
    local model="$1"
    
    # FastEmbed stores models using HuggingFace Hub cache structure:
    # models--{org}--{model_name}/snapshots/{hash}/
    # e.g., models--BAAI--bge-small-en-v1.5/snapshots/.../model.onnx
    #
    # NOTE: FastEmbed may use different model sources (e.g., qdrant/... instead of BAAI/...)
    # so we search for any matching model name pattern
    
    # Extract just the model name (e.g., "bge-small-en-v1.5" from "BAAI/bge-small-en-v1.5")
    local model_name
    model_name="$(echo "$model" | cut -d'/' -f2)"
    
    # Check if cache directory exists
    if [[ ! -d "${FASTEMBED_CACHE}" ]]; then
        return 1
    fi
    
    # Look for any model directory containing this model name
    # This handles cases where FastEmbed uses different sources (qdrant vs BAAI, etc.)
    local matching_dirs
    matching_dirs=$(find "${FASTEMBED_CACHE}" -maxdepth 1 -type d -name "*${model_name}*" 2>/dev/null)
    
    if [[ -z "$matching_dirs" ]]; then
        return 1
    fi
    
    # Check if any matching directory has model files
    # HuggingFace Hub uses symlinks to blobs, so check for both files and symlinks
    while IFS= read -r dir; do
        [[ -z "$dir" ]] && continue
        
        # Look for .onnx files or symlinks (don't follow symlinks with -L, just find them)
        # Check snapshots directory specifically where the symlinks are
        if find "$dir/snapshots" -name "*.onnx" 2>/dev/null | grep -q .; then
            return 0
        fi
        
        # Also check for direct .onnx files (in case structure differs)
        if find "$dir" -name "*.onnx" 2>/dev/null | grep -q .; then
            return 0
        fi
    done <<< "$matching_dirs"
    
    return 1
}

# =============================================================================
# MODEL DOWNLOAD
# =============================================================================

download_model() {
    local model="$1"
    local size="${MODEL_SIZES[$model]}"
    
    log_info "Downloading model: ${model} (${size})"
    
    # Create cache directory
    mkdir -p "$FASTEMBED_CACHE"
    
    # Set cache directory for fastembed
    export FASTEMBED_CACHE_PATH="$FASTEMBED_CACHE"
    
    # Activate venv
    source "${VENV_DIR}/bin/activate"
    
    # Determine output redirection (suppress progress bars in background)
    local log_file="${FASTEMBED_CACHE}/.download-$(echo "$model" | tr '/' '_').log"
    local output_redirect=""
    if [[ ! -t 1 ]]; then
        # Not a terminal (running in background), redirect all output to log
        output_redirect=">> '${log_file}' 2>&1"
        log_info "  Log: ${log_file}"
    fi
    
    # Download model using Python script
    # This instantiates the model which triggers download
    # Note: We skip the warmup test as it can fail with symlink issues
    if [[ -n "$output_redirect" ]]; then
        # Background mode with output redirection
        python3 -c "
from fastembed import TextEmbedding
import os

model_name = '${model}'
cache_dir = '${FASTEMBED_CACHE}'

print(f'Downloading {model_name} to {cache_dir}...')
# Download by instantiating - this triggers the download
embedder = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
print(f'Download complete!')
" >> "$log_file" 2>&1 || {
            log_error "Failed to download ${model}"
            log_error "  See log: ${log_file}"
            return 1
        }
    else
        # Interactive mode - show output
        python3 -c "
from fastembed import TextEmbedding
import os

model_name = '${model}'
cache_dir = '${FASTEMBED_CACHE}'

print(f'Downloading {model_name} to {cache_dir}...')
# Download by instantiating - this triggers the download
embedder = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
print(f'Download complete!')
" || {
            log_error "Failed to download ${model}"
            return 1
        }
    fi
    
    log_success "Model downloaded: ${model}"
    
    # Show disk usage
    local model_slug
    model_slug=$(echo "$model" | tr '/' '_' | tr '[:upper:]' '[:lower:]')
    local model_path
    model_path=$(find "${FASTEMBED_CACHE}" -path "*${model_slug}*" -type d | head -1)
    if [[ -n "$model_path" ]]; then
        local size_on_disk
        size_on_disk=$(du -sh "$model_path" 2>/dev/null | cut -f1 || echo "unknown")
        log_info "  Location: ${model_path}"
        log_info "  Size on disk: ${size_on_disk}"
    fi
}

# =============================================================================
# MAIN DOWNLOAD LOGIC
# =============================================================================

download_models() {
    log_info "Checking for cached models..."
    echo ""
    
    local to_download=()
    local already_cached=()
    local total_size=0
    
    for model in "${EMBEDDING_MODELS[@]}"; do
        if check_model_cached "$model"; then
            already_cached+=("$model")
            log_success "  ✓ ${model} (${MODEL_SIZES[$model]})"
        else
            to_download+=("$model")
            log_warning "  ✗ ${model} (${MODEL_SIZES[$model]}) - not cached"
        fi
    done
    
    echo ""
    
    if [[ ${#already_cached[@]} -gt 0 ]]; then
        log_success "${#already_cached[@]} model(s) already cached"
    fi
    
    if [[ ${#to_download[@]} -eq 0 ]]; then
        log_success "All models are cached!"
        return 0
    fi
    
    # Calculate total download size
    for model in "${to_download[@]}"; do
        local size_str="${MODEL_SIZES[$model]}"
        # Extract numeric part (e.g., "134MB" -> 134)
        local size_num=$(echo "$size_str" | grep -o '[0-9.]*')
        if [[ "$size_str" == *"GB"* ]]; then
            size_num=$(echo "$size_num * 1024" | bc)
        fi
        total_size=$(echo "$total_size + $size_num" | bc)
    done
    
    local total_display
    if (( $(echo "$total_size > 1024" | bc -l) )); then
        total_display="$(echo "scale=1; $total_size / 1024" | bc)GB"
    else
        total_display="${total_size}MB"
    fi
    
    log_info "Will download ${#to_download[@]} model(s) (approximately ${total_display})"
    echo ""
    
    # Setup Python environment
    setup_python_environment
    echo ""
    
    # Download models
    local success_count=0
    local fail_count=0
    
    for model in "${to_download[@]}"; do
        if download_model "$model"; then
            ((success_count++))
        else
            ((fail_count++))
            log_error "Failed to download ${model}"
        fi
        echo ""
    done
    
    # Summary
    echo "=========================================="
    log_success "Downloaded: ${success_count} model(s)"
    if [[ $fail_count -gt 0 ]]; then
        log_error "Failed: ${fail_count} model(s)"
    fi
    echo "=========================================="
}

# =============================================================================
# CLEANUP MODE
# =============================================================================

cleanup_models() {
    echo "=========================================="
    echo "Embedding Model Cleanup"
    echo "=========================================="
    log_info "Scanning for models to remove..."
    echo ""
    
    if [[ ! -d "$FASTEMBED_CACHE" ]]; then
        log_warning "Cache directory does not exist: ${FASTEMBED_CACHE}"
        return 0
    fi
    
    # Get list of cached models
    local cached_models=()
    for model in "${EMBEDDING_MODELS[@]}"; do
        local model_slug
        model_slug=$(echo "$model" | tr '/' '_' | tr '[:upper:]' '[:lower:]')
        local model_path
        model_path=$(find "${FASTEMBED_CACHE}" -path "*${model_slug}*" -type d -name "models--*" 2>/dev/null | head -1)
        if [[ -n "$model_path" ]]; then
            cached_models+=("$model_path")
        fi
    done
    
    if [[ ${#cached_models[@]} -eq 0 ]]; then
        log_info "No embedding models found in cache"
        return 0
    fi
    
    # Find all model directories
    local all_models
    all_models=$(find "${FASTEMBED_CACHE}" -type d -name "models--*" 2>/dev/null)
    
    local to_remove=()
    while IFS= read -r model_path; do
        # Check if this model is in our registry
        local in_registry=false
        for cached in "${cached_models[@]}"; do
            if [[ "$model_path" == "$cached" ]]; then
                in_registry=true
                break
            fi
        done
        
        if [[ "$in_registry" == "false" ]]; then
            to_remove+=("$model_path")
        fi
    done <<< "$all_models"
    
    if [[ ${#to_remove[@]} -eq 0 ]]; then
        log_success "No orphaned models found!"
        return 0
    fi
    
    log_warning "Found ${#to_remove[@]} orphaned model(s):"
    echo ""
    
    local total_size=0
    for model_path in "${to_remove[@]}"; do
        local size
        size=$(du -sh "$model_path" 2>/dev/null | cut -f1)
        echo "  - $(basename "$model_path") (${size})"
        
        # Add to total (convert to MB for calculation)
        local size_mb
        if [[ "$size" == *"G"* ]]; then
            size_mb=$(echo "$size" | grep -o '[0-9.]*')
            size_mb=$(echo "$size_mb * 1024" | bc)
        else
            size_mb=$(echo "$size" | grep -o '[0-9.]*')
        fi
        total_size=$(echo "$total_size + $size_mb" | bc)
    done
    
    echo ""
    log_info "Total space to reclaim: $(echo "scale=1; $total_size / 1024" | bc)GB"
    echo ""
    
    read -p "Remove these models? [y/N] " -n 1 -r
    echo ""
    
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Cleanup cancelled"
        return 0
    fi
    
    # Remove models
    for model_path in "${to_remove[@]}"; do
        log_info "Removing $(basename "$model_path")..."
        rm -rf "$model_path"
    done
    
    log_success "Cleanup complete!"
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    if [ "$CLEANUP_MODE" = true ]; then
        cleanup_models
    else
        download_models
    fi
}

main
