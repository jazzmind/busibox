#!/usr/bin/env bash
#
# Configure vLLM Model-to-GPU Routing
#
# EXECUTION CONTEXT: Proxmox host (as root) or admin workstation
# PURPOSE: Configure which models run on which GPUs in vLLM container
#
# This script helps configure LiteLLM routing and vLLM model placement
# based on GPU memory and model sizes.
#
# USAGE:
#   bash configure-vllm-model-routing.sh [--interactive] [--model=MODEL --gpu=GPU]
#
# EXAMPLES:
#   # Interactive mode (recommended)
#   bash configure-vllm-model-routing.sh --interactive
#
#   # Configure specific model routing
#   bash configure-vllm-model-routing.sh --model=phi-4 --gpu=1
#   bash configure-vllm-model-routing.sh --model=qwen3-30b-instruct --gpu=2,3
#
# REQUIREMENTS:
#   - vLLM container must exist and have GPU access
#   - Model registry must be configured
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "${PCT_DIR}/../.." && pwd)"
MODEL_REGISTRY="${REPO_ROOT}/provision/ansible/group_vars/all/model_registry.yml"
LITELLM_CONFIG="${REPO_ROOT}/provision/ansible/roles/litellm/defaults/main.yml"
VENV_DIR="/opt/model-downloader"

# Source container IDs
if [ -f "${PCT_DIR}/vars.env" ]; then
    source "${PCT_DIR}/vars.env"
    CT_VLLM="${CT_VLLM:-208}"
    IP_VLLM="${IP_VLLM:-10.96.200.208}"
else
    CT_VLLM="208"
    IP_VLLM="10.96.200.208"
fi

# Update model registry with GPU and port configuration
update_model_registry_gpu_port() {
    local model_key="$1"  # Model key in available_models (e.g., "phi-4")
    local gpu_list="$2"    # GPU(s) for model (e.g., "1" or "2,3")
    local port="$3"        # vLLM port (e.g., 8000)
    
    if [ ! -f "$MODEL_REGISTRY" ]; then
        warn "Model registry not found: $MODEL_REGISTRY"
        return 1
    fi
    
    # Use Python to update YAML (preserves structure and comments)
    "${VENV_DIR}/bin/python3" << PYTHON_EOF
import yaml
import sys
import os
from pathlib import Path

registry_file = Path("${MODEL_REGISTRY}")
model_key = "${model_key}"
gpu_list = "${gpu_list}"
port = ${port}

try:
    # Read existing YAML
    with open(registry_file, 'r') as f:
        content = f.read()
        data = yaml.safe_load(content)
    
    # Ensure data is a dict (handle None or empty file)
    if data is None:
        data = {}
    
    # Initialize available_models if it doesn't exist or is None
    if 'available_models' not in data or data['available_models'] is None:
        data['available_models'] = {}
    
    # Ensure available_models is a dict
    if not isinstance(data['available_models'], dict):
        data['available_models'] = {}
    
    # Update or add model config
    if model_key not in data['available_models']:
        data['available_models'][model_key] = {}
    
    # Ensure model config entry is a dict
    if not isinstance(data['available_models'][model_key], dict):
        data['available_models'][model_key] = {}
    
    # Update GPU and port
    data['available_models'][model_key]['gpu'] = gpu_list
    data['available_models'][model_key]['port'] = port
    
    # Ensure provider is set if not already
    if 'provider' not in data['available_models'][model_key]:
        data['available_models'][model_key]['provider'] = 'vllm'
    
    # Write back (preserve structure)
    # Use ruamel.yaml if available for better comment preservation, otherwise use standard yaml
    try:
        from ruamel.yaml import YAML
        yaml_writer = YAML()
        yaml_writer.preserve_quotes = True
        yaml_writer.width = 4096
        with open(registry_file, 'w') as f:
            yaml_writer.dump(data, f)
    except ImportError:
        # Fallback to standard yaml (may lose some formatting)
        with open(registry_file, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    
    print(f"✓ Updated GPU and port for {model_key} in model registry")
except Exception as e:
    print(f"ERROR: Failed to update registry: {e}", file=sys.stderr)
    sys.exit(1)
PYTHON_EOF
    
    if [ $? -eq 0 ]; then
        success "Updated GPU and port for $model_key in model registry"
        return 0
    else
        error "Failed to update GPU and port for $model_key"
        return 1
    fi
}

# Load model configuration from model_registry.yml
# This replaces hardcoded MODEL_CONFIG, MODEL_NAMES, and MODEL_SIZES arrays
load_model_registry() {
    if [ ! -f "$MODEL_REGISTRY" ]; then
        echo "ERROR: Model registry not found: $MODEL_REGISTRY" >&2
        echo "ERROR: Current directory: $(pwd)" >&2
        echo "ERROR: REPO_ROOT: $REPO_ROOT" >&2
        echo "ERROR: Run this script from the busibox repository root" >&2
        exit 1
    fi
    
    echo "[INFO] Loading model registry from: $MODEL_REGISTRY" >&2
    
    # Ensure stderr (fd 2) goes to terminal for debug output
    # Python will output debug to stderr, array definitions to stdout
    exec 3>&2  # Save stderr to fd 3
    
    # Check if Python venv exists (for YAML parsing)
    if [ ! -d "$VENV_DIR" ] || ! "${VENV_DIR}/bin/python3" -c "import yaml" 2>/dev/null; then
        error "Python venv with PyYAML not found: $VENV_DIR"
        error "Run setup-llm-models.sh first to set up the environment"
        exit 1
    fi
    
    # Use Python to parse YAML and populate bash arrays
    local python_output
    python_output=$("${VENV_DIR}/bin/python3" << PYTHON_EOF 2>&2
import yaml
import sys
import os

registry_file = "${MODEL_REGISTRY}"
if not os.path.exists(registry_file):
    print("ERROR: Registry file not found", file=sys.stderr)
    sys.exit(1)

try:
    with open(registry_file, 'r') as f:
        data = yaml.safe_load(f)
    
    # Ensure data is a dict (handle None or empty file)
    if data is None:
        data = {}
    
    # Initialize output
    output_lines = []
    
    # Load available_models and model_purposes
    available_models = data.get('available_models') or {}
    purposes = data.get('model_purposes') or {}
    api_providers = {'bedrock', 'openai', 'anthropic'}
    
    # Ensure available_models is a dict
    if not isinstance(available_models, dict):
        available_models = {}
    
    # Build MODEL_NAMES from available_models (only vLLM models)
    vllm_models_found = []
    for model_key, model_config in available_models.items():
        # Ensure model_config is a dict
        if not isinstance(model_config, dict):
            continue
            
        provider = model_config.get('provider', '').lower()
        model_name = model_config.get('model_name', '')
        
        # Only include models that use vLLM (not API-based providers)
        # Accept both 'vllm' and 'litellm' as providers for local models
        if provider in ('vllm', 'litellm') and model_name:
            # Escape quotes in values
            model_key_escaped = model_key.replace('"', '\\"')
            model_name_escaped = model_name.replace('"', '\\"')
            output_lines.append(f'MODEL_NAMES["{model_key_escaped}"]="{model_name_escaped}"')
            vllm_models_found.append(model_key)
    
    # Load model_configs (technical details)
    # Ensure we always have a dict, not None
    configs = data.get('model_configs') or {}
    if not isinstance(configs, dict):
        configs = {}
    configs_empty = len(configs) == 0
    
    for model_name, config in configs.items():
        # Ensure config is a dict
        if not isinstance(config, dict):
            continue
        params = config.get('params_billions', 0)
        precision = config.get('precision', 'fp16')
        quantization = config.get('quantization', 'none')
        gpu_size = config.get('gpu_size_gb', 0)
        notes = config.get('notes', '')
        
        # Escape quotes in values
        model_name_escaped = model_name.replace('"', '\\"')
        precision_escaped = precision.replace('"', '\\"')
        quantization_escaped = quantization.replace('"', '\\"')
        notes_escaped = notes.replace('"', '\\"')
        
        # Format: params|precision|quantization|gpu_size|notes
        config_line = f"{params}|{precision_escaped}|{quantization_escaped}|{gpu_size}|{notes_escaped}"
        output_lines.append(f'MODEL_CONFIG["{model_name_escaped}"]="{config_line}"')
        output_lines.append(f'MODEL_SIZES["{model_name_escaped}"]="{gpu_size}"')
    
    # Output warnings if needed
    if configs_empty:
        print("WARNING: model_configs section is empty. Run update-model-config.sh to populate it.", file=sys.stderr)
    
    # Check model count
    model_count = len(vllm_models_found)
    if model_count == 0:
        print("WARNING: No vLLM models found in available_models. Check provider field.", file=sys.stderr)
    
    # Output array assignments to stdout (will be captured and eval'd)
    for line in output_lines:
        print(line)
    
except Exception as e:
    import traceback
    print(f"ERROR: Failed to parse registry: {e}", file=sys.stderr)
    print(f"Traceback: {traceback.format_exc()}", file=sys.stderr)
    sys.exit(1)
PYTHON_EOF
    ) 2>&2
    
    local python_exit=$?
    if [ $python_exit -ne 0 ]; then
        echo "ERROR: Failed to load model registry (Python exit code: $python_exit)" >&2
        exit 1
    fi
    
    # Evaluate the Python output to populate arrays
    eval "$python_output"
    
    # Check if any models were loaded
    if [ ${#MODEL_NAMES[@]} -eq 0 ]; then
        echo "ERROR: No vLLM models found in model registry!" >&2
        echo "ERROR: Check that available_models entries have provider: 'vllm'" >&2
        echo "ERROR: Registry file: $MODEL_REGISTRY" >&2
        echo "ERROR: Found ${#MODEL_NAMES[@]} models in MODEL_NAMES array" >&2
        echo "ERROR: Run with bash -x to see debug output" >&2
        exit 1
    fi
    
    echo "[INFO] Loaded ${#MODEL_NAMES[@]} vLLM model(s) from registry" >&2
    
    # Check if model_configs was empty (warning already printed by Python)
    if [ ${#MODEL_CONFIG[@]} -eq 0 ]; then
        warn "model_configs section is empty. Run update-model-config.sh to populate it."
        warn "Script will use fallback estimates for model configurations."
    fi
}

# Pre-declare arrays as global associative arrays BEFORE calling load_model_registry
# Note: Using declare -A (not -gA) at global scope to ensure compatibility
declare -A MODEL_CONFIG=()
declare -A MODEL_NAMES=()
declare -A MODEL_SIZES=()

# Load model registry at script startup
load_model_registry

# GPU memory sizes
declare -A GPU_MEMORY=()

# Estimate vLLM memory requirements with CPU offloading support
# Returns memory breakdown in GB (GPU|RAM|total)
# Format: gpu_weights|gpu_kv_cache|gpu_activations|gpu_overhead|ram_kv_cache|gpu_total|ram_total|combined_total
estimate_vllm_memory() {
    local params_billions="$1"
    local max_seq_length="${2:-8192}"
    local max_concurrent_seqs="${3:-256}"
    local precision="${4:-fp16}"
    local cpu_offload_gb="${5:-0}"  # CPU offload capacity (0 = no offload)
    
    # Bytes per parameter
    local bytes_per_param
    case "$precision" in
        fp32) bytes_per_param=4 ;;
        fp16) bytes_per_param=2 ;;
        int8) bytes_per_param=1 ;;
        int4) bytes_per_param=0.5 ;;
        *) bytes_per_param=2 ;; # Default to fp16
    esac
    
    # Model weights (GB) - always on GPU
    local model_weights=$(echo "$params_billions * $bytes_per_param" | bc -l)
    
    # KV cache per token (rough estimate: ~0.0005 GB per token)
    local kv_per_token_gb=0.0005
    
    # Total KV cache needed (all concurrent requests)
    local total_kv_cache=$(echo "$max_seq_length * $max_concurrent_seqs * $kv_per_token_gb" | bc -l)
    
    # Split KV cache between GPU and RAM if CPU offload is enabled
    local gpu_kv_cache
    local ram_kv_cache
    
    if [ "$(echo "$cpu_offload_gb > 0" | bc -l)" -eq 1 ]; then
        # With CPU offload: GPU holds hot cache, RAM holds cold cache
        # Estimate: GPU holds ~10-20% of total KV cache (hot requests)
        # The rest goes to RAM (up to cpu_offload_gb limit)
        local gpu_kv_percent=0.15  # 15% on GPU for hot requests
        gpu_kv_cache=$(echo "$total_kv_cache * $gpu_kv_percent" | bc -l)
        
        # RAM KV cache is limited by cpu_offload_gb, but can't exceed total needed
        local ram_needed=$(echo "$total_kv_cache - $gpu_kv_cache" | bc -l)
        if [ "$(echo "$ram_needed > $cpu_offload_gb" | bc -l)" -eq 1 ]; then
            ram_kv_cache="$cpu_offload_gb"
            warn "KV cache exceeds CPU offload capacity. Some requests may be queued."
        else
            ram_kv_cache="$ram_needed"
        fi
    else
        # No CPU offload: all KV cache on GPU
        gpu_kv_cache="$total_kv_cache"
        ram_kv_cache=0
    fi
    
    # Activations overhead (15%) - on GPU
    local activations=$(echo "($model_weights + $gpu_kv_cache) * 0.15" | bc -l)
    
    # vLLM engine overhead (5%) - on GPU
    local overhead=$(echo "($model_weights + $gpu_kv_cache) * 0.05" | bc -l)
    
    # GPU total (weights + GPU KV cache + activations + overhead)
    local gpu_total=$(echo "$model_weights + $gpu_kv_cache + $activations + $overhead" | bc -l)
    
    # RAM total (just the offloaded KV cache)
    local ram_total="$ram_kv_cache"
    
    # Combined total (for display purposes)
    local combined_total=$(echo "$gpu_total + $ram_total" | bc -l)
    
    echo "$model_weights|$gpu_kv_cache|$activations|$overhead|$ram_kv_cache|$gpu_total|$ram_total|$combined_total"
}

# Get model configuration
# Returns: params|precision|quantization|actual_gpu_size|notes
get_model_config() {
    local model_full="$1"
    
    if [ -n "${MODEL_CONFIG[$model_full]:-}" ]; then
        echo "${MODEL_CONFIG[$model_full]}"
    else
        # Fallback: try to extract from model name (for models not yet analyzed)
        warn "Model $model_full not found in model_configs. Using fallback estimates."
        warn "Run update-model-config.sh to analyze and populate model_configs."
        
        case "$model_full" in
            *Phi-4*|*phi-4*)
                echo "6|fp16|none|12|6B params, FP16 (fallback - run update-model-config.sh)"
                ;;
            *Qwen3-Embedding*|*qwen3-embedding*)
                echo "8|fp16|none|16|8B params, verify quantization (fallback)"
                ;;
            *Qwen3-30B*|*qwen3-30b*)
                echo "30|fp16|none|60|30B params, verify quantization (fallback)"
                ;;
            *Qwen3-VL-8B*|*qwen3-vl-8b*)
                echo "8|fp16|none|16|8B params, FP16 (fallback)"
                ;;
            *colpali*)
                echo "3|bf16|none|15|PaliGemma-3B + LoRA, BF16 (fallback)"
                ;;
            *)
                # Generic fallback
                warn "Unknown model: $model_full, using default estimates"
                echo "7|fp16|none|14|Default estimate - run update-model-config.sh to analyze"
                ;;
        esac
    fi
}

# Get model parameter count (billions)
get_model_params() {
    local model_full="$1"
    local config=$(get_model_config "$model_full")
    IFS='|' read -r params precision quantization actual_size notes <<< "$config"
    echo "$params"
}

# Get model precision (fp16, int8, int4, etc.)
get_model_precision() {
    local model_full="$1"
    local config=$(get_model_config "$model_full")
    IFS='|' read -r params precision quantization actual_size notes <<< "$config"
    echo "$precision"
}

# Get model quantization method
get_model_quantization() {
    local model_full="$1"
    local config=$(get_model_config "$model_full")
    IFS='|' read -r params precision quantization actual_size notes <<< "$config"
    echo "$quantization"
}

# Get actual GPU size (GB) - accounts for quantization
get_model_gpu_size() {
    local model_full="$1"
    local config=$(get_model_config "$model_full")
    IFS='|' read -r params precision quantization actual_size notes <<< "$config"
    echo "$actual_size"
}

# Display model memory estimates with CPU offloading
show_model_memory_estimates() {
    section "Model Memory Estimates (vLLM with CPU Offloading)"
    
    # Get CPU offload configuration (default from vLLM role)
    local default_cpu_offload=150  # Default from vllm/defaults/main.yml
    local cpu_offload="${1:-$default_cpu_offload}"
    
    echo "Memory requirements for each model (8K context, 256 concurrent):"
    echo "CPU Offload: ${cpu_offload}GB to system RAM"
    echo ""
    
    printf "%-25s %8s %8s %10s %10s %10s %10s %10s %10s\n" \
        "Model" "Params" "Precision" "GPU(GB)" "RAM(GB)" "Total(GB)" "GPU KV" "RAM KV" "Weights"
    echo "────────────────────────────────────────────────────────────────────────────────────────────────────────────"
    
    for model_short in "${!MODEL_NAMES[@]}"; do
        local model_full="${MODEL_NAMES[$model_short]}"
        local config=$(get_model_config "$model_full")
        IFS='|' read -r params precision quantization actual_size notes <<< "$config"
        
        # Use actual precision from config
        local memory_breakdown=$(estimate_vllm_memory "$params" 8192 256 "$precision" "$cpu_offload")
        IFS='|' read -r weights gpu_kv_cache activations overhead ram_kv_cache gpu_total ram_total combined_total <<< "$memory_breakdown"
        
        # Show quantization indicator
        local precision_display="$precision"
        if [ "$quantization" != "none" ]; then
            precision_display="${precision} (${quantization})"
        fi
        
        printf "%-25s %8s %8s %10.1f %10.1f %10.1f %10.1f %10.1f %10.1f\n" \
            "$model_short" \
            "${params}B" \
            "$precision_display" \
            "$gpu_total" \
            "$ram_total" \
            "$combined_total" \
            "$gpu_kv_cache" \
            "$ram_kv_cache" \
            "$weights"
        
        # Show notes if available
        if [ -n "$notes" ] && [ "$notes" != "none" ]; then
            echo "  └─ $notes"
        fi
    done
    
    echo ""
    echo "Memory Breakdown:"
    echo "  GPU: Model weights + Hot KV cache (active requests) + Activations + Overhead"
    echo "  RAM: Cold KV cache (queued requests, offloaded from GPU)"
    echo ""
    echo "⚠️  IMPORTANT: Verify quantization settings match your actual deployment!"
    echo "   - Check vLLM service configuration for --quantization flags"
    echo "   - Qwen models may be quantized (int8/int4) - update MODEL_CONFIG if needed"
    echo ""
    echo "Note: These estimates assume CPU offloading is enabled."
    echo "Actual memory usage depends on:"
    echo "  - Sequence length (longer = more KV cache)"
    echo "  - Concurrent requests (more = more KV cache)"
    echo "  - Precision/Quantization (fp16/int8/int4)"
    echo "  - Tensor parallelism (splits model weights across GPUs)"
    echo "  - CPU offload capacity (more RAM = more concurrent requests)"
    echo ""
    echo "With ${cpu_offload}GB CPU offload, you can handle 20-40x more concurrent requests"
    echo "with only +100-200ms latency for requests swapped from RAM to GPU."
    echo ""
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1" >&2
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1" >&2
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1" >&2
}

section() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
}

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Configure vLLM model-to-GPU routing and automatically update LiteLLM config.

OPTIONS:
    --interactive          Interactive mode - prompts for model routing
    --model=MODEL          Model name (e.g., phi-4, qwen3-30b-instruct)
    --gpu=GPUS             GPU(s) for model (e.g., "1" or "2,3")
    --auto-update          Automatically update LiteLLM config file (default: prompt)
    --no-auto-update       Don't update LiteLLM config, just show config snippets
    --help                 Show this help message

EXAMPLES:
    # Interactive mode (recommended) - will prompt to update LiteLLM config
    $0 --interactive

    # Interactive with auto-update (no prompts)
    $0 --interactive --auto-update

    # Configure specific model with auto-update
    $0 --model=phi-4 --gpu=1 --auto-update

MODEL ROUTING STRATEGY:
    Small models (phi-4, qwen3-embedding): GPU 1 (single GPU)
    Medium models (qwen3-30b): GPUs 2,3 (tensor parallelism)
    Large models (70B+): GPUs 2,3,4,5 (4+ GPUs)

LITELLM CONFIG:
    By default, the script will prompt to update LiteLLM config automatically.
    Use --auto-update to skip prompts, or --no-auto-update to only show snippets.

EOF
}

# Detect GPUs in vLLM container
detect_vllm_gpus() {
    info "Detecting GPUs in vLLM container..."
    
    if ! ssh root@"$IP_VLLM" "command -v nvidia-smi &>/dev/null" 2>/dev/null; then
        error "nvidia-smi not found in vLLM container. Install NVIDIA drivers first."
        return 1
    fi
    
    local gpu_count=$(ssh root@"$IP_VLLM" "nvidia-smi -L | wc -l")
    
    if [ "$gpu_count" -eq 0 ]; then
        error "No GPUs detected in vLLM container"
        return 1
    fi
    
    success "Found $gpu_count GPU(s) in vLLM container"
    
    # Get GPU memory sizes
    for i in $(seq 0 $((gpu_count - 1))); do
        local memory=$(ssh root@"$IP_VLLM" "nvidia-smi -i $i --query-gpu=memory.total --format=csv,noheader,nounits" 2>/dev/null | head -1)
        local memory_gb=$((memory / 1024))
        GPU_MEMORY["$i"]="$memory_gb"
        info "  GPU $i: ${memory_gb}GB"
    done
    
    echo ""
}

# Calculate model size
calculate_model_size() {
    local model_name="$1"
    
    # Check if model is in database
    if [ -n "${MODEL_SIZES[$model_name]:-}" ]; then
        echo "${MODEL_SIZES[$model_name]}"
        return 0
    fi
    
    # Try to extract parameter count
    if [[ "$model_name" =~ ([0-9]+\.?[0-9]*)B ]]; then
        local params="${BASH_REMATCH[1]}"
        local params_num=$(echo "$params" | awk '{print int($1)}')
        local size_gb=$((params_num * 2))
        echo "$size_gb"
        return 0
    fi
    
    warn "Unknown model: $model_name, using default estimate"
    echo "20"
}

# Check if model fits on GPU(s) using vLLM memory estimation with CPU offloading
check_model_fits() {
    local model_full="$1"
    local gpu_list="$2"
    local tensor_parallel="${3:-1}"
    local max_seq_length="${4:-8192}"
    local max_concurrent_seqs="${5:-256}"
    local cpu_offload_gb="${6:-150}"  # Default CPU offload from vLLM config
    
    # Get model configuration (includes precision and quantization)
    local config=$(get_model_config "$model_full")
    IFS='|' read -r params precision quantization actual_size notes <<< "$config"
    
    # Show model info
    info "Model: $model_full"
    info "  Parameters: ${params}B"
    info "  Precision: $precision"
    if [ "$quantization" != "none" ]; then
        info "  Quantization: $quantization"
    fi
    info "  Actual GPU size: ${actual_size}GB"
    
    # Estimate memory requirements (with CPU offloading, using actual precision)
    local memory_breakdown=$(estimate_vllm_memory "$params" "$max_seq_length" "$max_concurrent_seqs" "$precision" "$cpu_offload_gb")
    IFS='|' read -r weights gpu_kv_cache activations overhead ram_kv_cache gpu_total ram_total combined_total <<< "$memory_breakdown"
    
    # Account for tensor parallelism (model weights are split, but KV cache is per GPU)
    local weights_per_gpu=$(echo "$weights / $tensor_parallel" | bc -l)
    local gpu_kv_per_gpu="$gpu_kv_cache"  # KV cache is per GPU, not split
    local gpu_total_per_gpu=$(echo "$weights_per_gpu + $gpu_kv_per_gpu + $activations + $overhead" | bc -l)
    
    info "Model GPU requirements: ~$(printf "%.1f" "$gpu_total_per_gpu")GB per GPU (tensor parallelism: $tensor_parallel)"
    if [ "$(echo "$cpu_offload_gb > 0" | bc -l)" -eq 1 ]; then
        info "Model RAM requirements: ~$(printf "%.1f" "$ram_total")GB system RAM (CPU offload)"
    fi
    
    # Parse GPU list
    local gpus=()
    if [[ "$gpu_list" =~ ^[0-9]+-[0-9]+$ ]]; then
        IFS='-' read -r START END <<< "$gpu_list"
        for ((i=START; i<=END; i++)); do
            gpus+=("$i")
        done
    elif [[ "$gpu_list" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
        IFS=',' read -ra gpus <<< "$gpu_list"
    else
        gpus=("$gpu_list")
    fi
    
    # Check each GPU has enough memory
    local all_fit=true
    for gpu in "${gpus[@]}"; do
        if [ -z "${GPU_MEMORY[$gpu]:-}" ]; then
            error "GPU $gpu not found in vLLM container"
            return 1
        fi
        
        local gpu_memory="${GPU_MEMORY[$gpu]}"
        local required=$(printf "%.0f" "$gpu_total_per_gpu")
        local available=$((gpu_memory * 90 / 100))  # 90% usable
        
        if [ "$required" -le "$available" ]; then
            success "GPU $gpu: ${gpu_memory}GB total (${available}GB usable, needs ${required}GB) ✓"
        else
            error "GPU $gpu: ${gpu_memory}GB total (${available}GB usable) but needs ${required}GB ✗"
            all_fit=false
        fi
    done
    
    # Note about RAM requirements (informational, not blocking)
    if [ "$(echo "$cpu_offload_gb > 0" | bc -l)" -eq 1 ] && [ "$(echo "$ram_total > 0" | bc -l)" -eq 1 ]; then
        local ram_required=$(printf "%.0f" "$ram_total")
        info "System RAM: Needs ~${ram_required}GB for KV cache offloading (check container has enough RAM)"
    fi
    
    if [ "$all_fit" = true ]; then
        return 0
    else
        return 1
    fi
}

# Update LiteLLM config file automatically
update_litellm_config() {
    local model_short="$1"
    local model_full="$2"
    local vllm_port="${3:-8000}"
    local auto_update="${4:-false}"
    
    if [ "$auto_update" != "true" ]; then
        return 0  # Skip if auto-update not requested
    fi
    
    if [ ! -f "$LITELLM_CONFIG" ]; then
        warn "LiteLLM config file not found: $LITELLM_CONFIG"
        warn "Skipping automatic update"
        return 1
    fi
    
    # Check if Python and PyYAML are available
    if ! python3 -c "import yaml" 2>/dev/null; then
        warn "PyYAML not available. Install with: pip3 install pyyaml"
        warn "Skipping automatic LiteLLM config update"
        return 1
    fi
    
    info "Updating LiteLLM config file automatically..."
    
    # Use Python to edit YAML (preserves Jinja2 templates)
    python3 << PYTHON_EOF
import yaml
import re
from pathlib import Path

config_file = Path("${LITELLM_CONFIG}")
model_short = "${model_short}"
model_full = "${model_full}"
vllm_port = "${vllm_port}"

# Read existing config as text to preserve structure
with open(config_file, 'r') as f:
    lines = f.readlines()

# Find litellm_models section
litellm_start = -1
litellm_end = -1
base_indent = 0

for i, line in enumerate(lines):
    if re.match(r'^litellm_models:', line):
        litellm_start = i
        base_indent = len(line) - len(line.lstrip())
        # Find end of litellm_models section (next top-level key or end of file)
        for j in range(i + 1, len(lines)):
            stripped = lines[j].lstrip()
            # Check if this is a new top-level key (starts at column 0, not a list item or comment)
            if stripped and not lines[j].startswith(' ') and not lines[j].startswith('#'):
                if not stripped.startswith('-'):
                    litellm_end = j
                    break
        if litellm_end == -1:
            litellm_end = len(lines)
        break

if litellm_start == -1:
    # litellm_models section doesn't exist, append it
    lines.append('\n')
    lines.append('litellm_models:\n')
    litellm_start = len(lines) - 1
    litellm_end = len(lines)
    base_indent = 0

# Check if model already exists in the section
model_exists = False
model_line_start = -1
model_line_end = -1
in_model_entry = False
entry_indent = base_indent + 2

for i in range(litellm_start + 1, litellm_end):
    line = lines[i]
    stripped = line.lstrip()
    
    # Check if this line starts a model entry
    if stripped.startswith('- model_name:'):
        # Check if this is our model
        if f'"{model_short}"' in line or f"'{model_short}'" in line or model_short in line:
            model_exists = True
            model_line_start = i
            in_model_entry = True
            entry_indent = len(line) - len(line.lstrip())
            continue
    
    if in_model_entry:
        # Check if we've reached the end of this model entry
        if stripped and not line.startswith(' ' * (entry_indent + 1)):
            if not stripped.startswith('#') and not stripped.startswith('-'):
                model_line_end = i
                break
        # If we hit the next model entry, this one ends
        if stripped.startswith('- model_name:'):
            model_line_end = i
            break

if model_line_end == -1 and in_model_entry:
    model_line_end = litellm_end

# Build new model entry
new_entry_lines = [
    ' ' * entry_indent + f'- model_name: "{model_short}"\n',
    ' ' * (entry_indent + 2) + 'litellm_params:\n',
    ' ' * (entry_indent + 4) + f'model: "openai/{model_full}"\n',
    ' ' * (entry_indent + 4) + f'api_base: "http://{{{{ vllm_ip }}}}:{vllm_port}/v1"\n',
    ' ' * (entry_indent + 4) + "api_key: \"EMPTY\"  # vLLM doesn't require authentication\n"
]

# Insert or replace
if model_exists and model_line_start >= 0 and model_line_end >= 0:
    # Replace existing entry
    lines[model_line_start:model_line_end] = new_entry_lines
    action = "updated"
else:
    # Insert before end of litellm_models section
    lines[litellm_end:litellm_end] = new_entry_lines
    action = "added"

# Write back
with open(config_file, 'w') as f:
    f.writelines(lines)

print(f"✓ {action.capitalize()} model '{model_short}' in LiteLLM config")
PYTHON_EOF
    
    if [ $? -eq 0 ]; then
        success "LiteLLM config updated: $model_short"
        return 0
    else
        error "Failed to update LiteLLM config"
        return 1
    fi
}

# Generate vLLM and LiteLLM routing configuration
generate_routing_config() {
    local model_short="$1"
    local gpu_list="$2"
    local tensor_parallel="${3:-1}"
    local vllm_port="${4:-8000}"  # Default vLLM port, can be overridden for separate instances
    local auto_update="${5:-false}"  # Whether to automatically update LiteLLM config
    local update_registry="${6:-false}"  # Whether to update model registry with GPU/port
    
    local model_full="${MODEL_NAMES[$model_short]:-$model_short}"
    
    # Update model registry with GPU and port if requested
    if [ "$update_registry" = "true" ]; then
        update_model_registry_gpu_port "$model_short" "$gpu_list" "$vllm_port"
    fi
    
    echo ""
    info "Configuration for $model_short:"
    echo "  Model: $model_full"
    echo "  GPUs: $gpu_list"
    echo "  Tensor Parallelism: $tensor_parallel"
    echo "  vLLM Port: $vllm_port"
    echo ""
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "1. vLLM Configuration (Ansible Variables)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "# In inventory/*/group_vars/all/00-main.yml"
    echo ""
    echo "# For main vLLM instance (if this is the primary model):"
    echo "vllm_cuda_visible_devices: \"$gpu_list\""
    echo "vllm_tensor_parallel_size: $tensor_parallel"
    echo ""
    echo "# OR for separate vLLM instances (if using multiple models):"
    echo "# Create separate vLLM service on different port:"
    echo "vllm_${model_short}_cuda_visible_devices: \"$gpu_list\""
    echo "vllm_${model_short}_tensor_parallel_size: $tensor_parallel"
    echo "vllm_${model_short}_port: $vllm_port"
    echo ""
    
    # Update LiteLLM config automatically if requested
    if [ "$auto_update" = "true" ]; then
        update_litellm_config "$model_short" "$model_full" "$vllm_port" "true"
    else
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "2. LiteLLM Configuration (Manual - use --auto-update to edit automatically)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "# In provision/ansible/roles/litellm/defaults/main.yml"
        echo "# Add to litellm_models list:"
        echo ""
        echo "  - model_name: \"$model_short\""
        echo "    litellm_params:"
        echo "      model: \"openai/$model_full\""
        echo "      api_base: \"http://{{ vllm_ip }}:${vllm_port}/v1\""
        echo "      api_key: \"EMPTY\"  # vLLM doesn't require authentication"
        echo ""
    fi
}

# Interactive routing configuration
interactive_routing() {
    section "Interactive Model Routing"
    
    # Get CPU offload configuration
    local default_cpu_offload=150
    echo "CPU Offload Configuration:"
    echo "  This allows KV cache to be offloaded to system RAM, dramatically increasing"
    echo "  concurrent request capacity (20-40x improvement) with +100-200ms latency for cache misses."
    echo ""
    read -p "CPU offload capacity (GB, default: ${default_cpu_offload}): " cpu_offload_input
    local cpu_offload="${cpu_offload_input:-$default_cpu_offload}"
    
    # Show model memory estimates upfront (with CPU offloading)
    show_model_memory_estimates "$cpu_offload"
    
    detect_vllm_gpus
    
    # Initialize routing assignments
    declare -A MODEL_GPU_ASSIGNMENTS
    declare -A MODEL_PORT_ASSIGNMENTS
    declare -A MODEL_TP_ASSIGNMENTS
    
    # Interactive assignment loop
    local done=false
    while [ "$done" = false ]; do
        echo ""
        section "Current GPU Assignments"
        show_gpu_allocation_table
        
        echo ""
        echo "Options:"
        echo "  1) Assign model to GPU"
        echo "  2) Remove model from GPU"
        echo "  3) Auto-assign all models"
        echo "  4) Save and exit"
        echo "  5) Exit without saving"
        echo ""
        read -p "Select option [1-5]: " option
        
        case "$option" in
            1)
                assign_model_to_gpu
                ;;
            2)
                remove_model_from_gpu
                ;;
            3)
                auto_assign_all_models "$cpu_offload"
                ;;
            4)
                save_routing_configuration "$cpu_offload"
                done=true
                ;;
            5)
                info "Exiting without saving changes"
                exit 0
                ;;
            *)
                error "Invalid option"
                ;;
        esac
    done
}

# Show current GPU allocation in a table
show_gpu_allocation_table() {
    echo ""
    printf "%-8s %-10s %-25s %-10s %-8s %-8s\n" "GPU" "Memory" "Model" "Size(GB)" "Port" "TP"
    echo "────────────────────────────────────────────────────────────────────────────────────"
    
    # Show each GPU
    for gpu_id in $(seq 0 $((${#GPU_MEMORY[@]} - 1))); do
        local gpu_mem="${GPU_MEMORY[$gpu_id]:-0}"
        local gpu_used=0
        local models_on_gpu=""
        
        # Find models assigned to this GPU
        for model in "${!MODEL_GPU_ASSIGNMENTS[@]}"; do
            local assigned_gpus="${MODEL_GPU_ASSIGNMENTS[$model]}"
            if [[ ",$assigned_gpus," == *",$gpu_id,"* ]]; then
                local model_full="${MODEL_NAMES[$model]}"
                local model_size=$(calculate_model_size "$model_full")
                local port="${MODEL_PORT_ASSIGNMENTS[$model]:-unset}"
                local tp="${MODEL_TP_ASSIGNMENTS[$model]:-1}"
                
                if [ -z "$models_on_gpu" ]; then
                    printf "%-8s %-10s %-25s %-10s %-8s %-8s\n" \
                        "GPU $gpu_id" "${gpu_mem}GB" "$model" "${model_size}GB" "$port" "$tp"
                    models_on_gpu="$model"
                else
                    printf "%-8s %-10s %-25s %-10s %-8s %-8s\n" \
                        "" "" "$model" "${model_size}GB" "$port" "$tp"
                    models_on_gpu="$models_on_gpu,$model"
                fi
                gpu_used=$(echo "$gpu_used + $model_size" | bc -l)
            fi
        done
        
        # Show empty GPU
        if [ -z "$models_on_gpu" ]; then
            printf "%-8s %-10s %-25s %-10s %-8s %-8s\n" \
                "GPU $gpu_id" "${gpu_mem}GB" "(empty)" "-" "-" "-"
        else
            # Show usage summary for this GPU
            local usage_pct=$(echo "scale=1; $gpu_used / $gpu_mem * 100" | bc -l)
            printf "%-8s %-10s %-25s %-10s\n" \
                "" "" "  └─ Total:" "${gpu_used}GB / ${gpu_mem}GB (${usage_pct}%)"
        fi
    done
    
    # Show unassigned models
    echo ""
    echo "Unassigned models:"
    local has_unassigned=false
    for model in "${!MODEL_NAMES[@]}"; do
        if [ -z "${MODEL_GPU_ASSIGNMENTS[$model]:-}" ]; then
            local model_full="${MODEL_NAMES[$model]}"
            local model_size=$(calculate_model_size "$model_full")
            echo "  - $model (~${model_size}GB)"
            has_unassigned=true
        fi
    done
    
    if [ "$has_unassigned" = false ]; then
        echo "  (none)"
    fi
}

# Assign a model to GPU(s)
assign_model_to_gpu() {
    echo ""
    echo "Available models:"
    local i=1
    local model_list=()
    for model in "${!MODEL_NAMES[@]}"; do
        local model_full="${MODEL_NAMES[$model]}"
        local model_size=$(calculate_model_size "$model_full")
        local assigned="${MODEL_GPU_ASSIGNMENTS[$model]:-unassigned}"
        echo "  $i) $model (~${model_size}GB) [currently: $assigned]"
        model_list+=("$model")
        ((i++))
    done
    
    echo ""
    read -p "Select model number [1-${#model_list[@]}]: " model_num
    
    if ! [[ "$model_num" =~ ^[0-9]+$ ]] || [ "$model_num" -lt 1 ] || [ "$model_num" -gt "${#model_list[@]}" ]; then
        error "Invalid model number"
        return
    fi
    
    local selected_model="${model_list[$((model_num - 1))]}"
    local model_full="${MODEL_NAMES[$selected_model]}"
    local model_size=$(calculate_model_size "$model_full")
    
    echo ""
    info "Model: $selected_model (~${model_size}GB)"
    echo ""
    echo "Available GPUs:"
    for gpu_id in $(seq 0 $((${#GPU_MEMORY[@]} - 1))); do
        echo "  GPU $gpu_id: ${GPU_MEMORY[$gpu_id]}GB"
    done
    
    echo ""
    read -p "GPU(s) for this model (e.g., '1' or '2,3' for tensor parallelism): " gpu_list
    read -p "vLLM port (e.g., 8000, 8001, 8002): " port
    
    # Calculate tensor parallelism from GPU list
    local tp=1
    if [[ "$gpu_list" == *","* ]]; then
        tp=$(echo "$gpu_list" | tr ',' '\n' | wc -l)
    fi
    
    MODEL_GPU_ASSIGNMENTS["$selected_model"]="$gpu_list"
    MODEL_PORT_ASSIGNMENTS["$selected_model"]="$port"
    MODEL_TP_ASSIGNMENTS["$selected_model"]="$tp"
    
    success "Assigned $selected_model to GPU(s) $gpu_list (port $port, TP=$tp)"
}

# Remove a model from GPU assignment
remove_model_from_gpu() {
    echo ""
    echo "Currently assigned models:"
    local i=1
    local assigned_models=()
    for model in "${!MODEL_GPU_ASSIGNMENTS[@]}"; do
        local gpus="${MODEL_GPU_ASSIGNMENTS[$model]}"
        local port="${MODEL_PORT_ASSIGNMENTS[$model]:-unset}"
        echo "  $i) $model (GPU: $gpus, Port: $port)"
        assigned_models+=("$model")
        ((i++))
    done
    
    if [ "${#assigned_models[@]}" -eq 0 ]; then
        warn "No models currently assigned"
        return
    fi
    
    echo ""
    read -p "Select model number to remove [1-${#assigned_models[@]}]: " model_num
    
    if ! [[ "$model_num" =~ ^[0-9]+$ ]] || [ "$model_num" -lt 1 ] || [ "$model_num" -gt "${#assigned_models[@]}" ]; then
        error "Invalid model number"
        return
    fi
    
    local selected_model="${assigned_models[$((model_num - 1))]}"
    unset MODEL_GPU_ASSIGNMENTS["$selected_model"]
    unset MODEL_PORT_ASSIGNMENTS["$selected_model"]
    unset MODEL_TP_ASSIGNMENTS["$selected_model"]
    
    success "Removed $selected_model from GPU assignment"
}

# Auto-assign all models based on GPU memory
auto_assign_all_models() {
    local cpu_offload="$1"
    
    info "Auto-assigning models based on GPU memory..."
    
    # Clear existing assignments
    MODEL_GPU_ASSIGNMENTS=()
    MODEL_PORT_ASSIGNMENTS=()
    MODEL_TP_ASSIGNMENTS=()
    
    local port_counter=8000
    
    # Strategy: Put small models on GPU 0/1, large models on remaining GPUs with TP
    # Sort models by size
    local small_models=()
    local large_models=()
    
    for model in "${!MODEL_NAMES[@]}"; do
        local model_full="${MODEL_NAMES[$model]}"
        local params=$(get_model_params "$model_full")
        
        if [ "$(echo "$params < 10" | bc -l)" -eq 1 ]; then
            small_models+=("$model")
        else
            large_models+=("$model")
        fi
    done
    
    # Assign small models to GPU 1 (if available)
    local small_gpu=0
    if [ "${#GPU_MEMORY[@]}" -gt 1 ]; then
        small_gpu=1
    fi
    
    for model in "${small_models[@]}"; do
        MODEL_GPU_ASSIGNMENTS["$model"]="$small_gpu"
        MODEL_PORT_ASSIGNMENTS["$model"]="$port_counter"
        MODEL_TP_ASSIGNMENTS["$model"]="1"
        ((port_counter++))
        success "Assigned $model to GPU $small_gpu (port $port_counter)"
    done
    
    # Assign large models with tensor parallelism
    if [ "${#GPU_MEMORY[@]}" -ge 2 ]; then
        local large_gpu_start=0
        if [ "${#GPU_MEMORY[@]}" -gt 2 ]; then
            large_gpu_start=2
        fi
        
        for model in "${large_models[@]}"; do
            if [ "${#GPU_MEMORY[@]}" -ge 3 ]; then
                # Use GPUs 2,3 for large models with TP
                MODEL_GPU_ASSIGNMENTS["$model"]="2,3"
                MODEL_TP_ASSIGNMENTS["$model"]="2"
            else
                # Use remaining GPU
                MODEL_GPU_ASSIGNMENTS["$model"]="$large_gpu_start"
                MODEL_TP_ASSIGNMENTS["$model"]="1"
            fi
            MODEL_PORT_ASSIGNMENTS["$model"]="$port_counter"
            ((port_counter++))
            success "Assigned $model to GPU(s) ${MODEL_GPU_ASSIGNMENTS[$model]} (port $port_counter, TP=${MODEL_TP_ASSIGNMENTS[$model]})"
        done
    fi
}

# Save routing configuration
save_routing_configuration() {
    local cpu_offload="$1"
    
    section "Saving Configuration"
    
    # Determine auto-update behavior
    local should_update="$AUTO_UPDATE"
    if [ -z "$should_update" ]; then
        echo ""
        read -p "Update LiteLLM config file? (Y/n): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            should_update="true"
        else
            should_update="false"
        fi
    fi
    
    echo ""
    read -p "Update model registry with GPU and port assignments? (Y/n): " -n 1 -r
    echo
    local update_registry="false"
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        update_registry="true"
    fi
    
    # Save each assignment
    for model in "${!MODEL_GPU_ASSIGNMENTS[@]}"; do
        local gpus="${MODEL_GPU_ASSIGNMENTS[$model]}"
        local port="${MODEL_PORT_ASSIGNMENTS[$model]}"
        local tp="${MODEL_TP_ASSIGNMENTS[$model]}"
        
        info "Generating routing config for $model..."
        generate_routing_config "$model" "$gpus" "$tp" "$port" "$should_update" "$update_registry"
    done
    
    success "Configuration saved"
}

# Legacy "configure all" or single model mode - keeping for backwards compatibility
interactive_routing_legacy() {
    read -p "Select model to configure (or 'all' for all models): " selected_model
    
    if [ "$selected_model" = "all" ]; then
        # Configure all models
        info "Configuring all models..."
        
        # Determine auto-update behavior
        local should_update="$AUTO_UPDATE"
        if [ -z "$should_update" ]; then
            echo ""
            read -p "Automatically update LiteLLM config file for all models? (Y/n): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Nn]$ ]]; then
                should_update="true"
            else
                should_update="false"
            fi
        fi
        
        echo ""
        read -p "Update model registry with GPU and port configuration? (Y/n): " -n 1 -r
        echo
        local update_registry="false"
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            update_registry="true"
        fi
        
        # Small models on GPU 1
        if [ -n "${GPU_MEMORY[1]:-}" ]; then
            check_model_fits "${MODEL_NAMES[phi-4]}" "1" "1" "8192" "256" "$cpu_offload" && generate_routing_config "phi-4" "1" "1" "8002" "$should_update" "$update_registry"
            check_model_fits "${MODEL_NAMES[qwen3-embedding]}" "1" "1" "8192" "256" "$cpu_offload" && generate_routing_config "qwen3-embedding" "1" "1" "8001" "$should_update" "$update_registry"
        fi
        
        # Large models on multiple GPUs
        if [ ${#GPU_MEMORY[@]} -ge 2 ]; then
            local gpu_list="2"
            if [ ${#GPU_MEMORY[@]} -ge 3 ]; then
                gpu_list="2,3"
            fi
            check_model_fits "${MODEL_NAMES[qwen3-30b-instruct]}" "$gpu_list" "2" "8192" "256" "$cpu_offload" && generate_routing_config "qwen3-30b-instruct" "$gpu_list" "2" "8003" "$should_update" "$update_registry"
        fi
    else
        # Configure single model
        if [ -z "${MODEL_NAMES[$selected_model]:-}" ]; then
            error "Unknown model: $selected_model"
            exit 1
        fi
        
        local model_full="${MODEL_NAMES[$selected_model]}"
        local model_size=$(calculate_model_size "$model_full")
        
        echo ""
        info "Model: $selected_model ($model_full)"
        info "Size: ~${model_size}GB"
        echo ""
        
        read -p "GPU(s) for this model (e.g., 1 or 2,3): " gpu_list
        read -p "Tensor parallelism (number of GPUs, default: 1): " tensor_parallel
        tensor_parallel="${tensor_parallel:-1}"
        read -p "vLLM port (default: 8000): " vllm_port
        vllm_port="${vllm_port:-8000}"
        
        # Determine auto-update behavior
        local should_update="$AUTO_UPDATE"
        if [ -z "$should_update" ]; then
            echo ""
            read -p "Automatically update LiteLLM config file? (Y/n): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Nn]$ ]]; then
                should_update="true"
            else
                should_update="false"
            fi
        fi
        
        echo ""
        read -p "Update model registry with GPU and port configuration? (Y/n): " -n 1 -r
        echo
        local update_registry="false"
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            update_registry="true"
        fi
        
        check_model_fits "$model_full" "$gpu_list" "$tensor_parallel" "8192" "256" "$cpu_offload" && generate_routing_config "$selected_model" "$gpu_list" "$tensor_parallel" "$vllm_port" "$should_update" "$update_registry"
    fi
}

# Main execution
main() {
    section "vLLM Model Routing Configuration"
    
    if [ "$INTERACTIVE" = true ]; then
        interactive_routing
    else
        error "Non-interactive mode not yet implemented. Use --interactive"
        exit 1
    fi
    
    section "Configuration Complete"
    
    if [ "$AUTO_UPDATE" = "true" ] || [ -n "$(grep -l "✓ Updated LiteLLM config" <<< "$(generate_routing_config 2>&1)")" ]; then
        success "LiteLLM config file has been updated automatically"
        echo ""
        info "Review changes:"
        echo "  git diff ${LITELLM_CONFIG}"
        echo ""
    fi
    
    info "Next steps:"
    echo "  1. Update Ansible variables (inventory/*/group_vars/all/00-main.yml)"
    echo "     Set vLLM GPU allocation variables as shown above"
    echo ""
    echo "  2. Redeploy services:"
    echo "     cd provision/ansible"
    echo "     ansible-playbook -i inventory/production/hosts.yml site.yml --tags vllm,litellm"
    echo ""
    echo "  3. Verify LiteLLM routing:"
    echo "     curl http://<litellm-ip>:4000/v1/models"
    echo "     # Should list all configured models"
}

# Parse arguments
INTERACTIVE=false
AUTO_UPDATE=""  # Empty = prompt, "true" = auto-update, "false" = no update
while [[ $# -gt 0 ]]; do
    case $1 in
        --interactive)
            INTERACTIVE=true
            shift
            ;;
        --auto-update)
            AUTO_UPDATE="true"
            shift
            ;;
        --no-auto-update)
            AUTO_UPDATE="false"
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Run main function
main

