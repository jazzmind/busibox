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

# Source container IDs
if [ -f "${PCT_DIR}/vars.env" ]; then
    source "${PCT_DIR}/vars.env"
    CT_VLLM="${CT_VLLM:-208}"
    IP_VLLM="${IP_VLLM:-10.96.200.208}"
else
    CT_VLLM="208"
    IP_VLLM="10.96.200.208"
fi

# Model size database (same as configure-gpu-allocation.sh)
declare -A MODEL_SIZES=(
    ["microsoft/Phi-4-multimodal-instruct"]="12"
    ["Qwen/Qwen3-Embedding-8B"]="16"
    ["Qwen/Qwen3-30B-A3B-Instruct-2507"]="60"
    ["Qwen/Qwen3-VL-8B-Instruct"]="16"
    ["vidore/colpali-v1.3"]="15"
)

# Model name mappings (short name -> full HuggingFace path)
declare -A MODEL_NAMES=(
    ["phi-4"]="microsoft/Phi-4-multimodal-instruct"
    ["qwen3-embedding"]="Qwen/Qwen3-Embedding-8B"
    ["qwen3-30b-instruct"]="Qwen/Qwen3-30B-A3B-Instruct-2507"
    ["qwen3-vl-8b"]="Qwen/Qwen3-VL-8B-Instruct"
    ["colpali-v1.3"]="vidore/colpali-v1.3"
)

# GPU memory sizes
declare -A GPU_MEMORY=()

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
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

Configure vLLM model-to-GPU routing.

OPTIONS:
    --interactive          Interactive mode - prompts for model routing
    --model=MODEL          Model name (e.g., phi-4, qwen3-30b-instruct)
    --gpu=GPUS             GPU(s) for model (e.g., "1" or "2,3")
    --help                 Show this help message

EXAMPLES:
    # Interactive mode (recommended)
    $0 --interactive

    # Configure specific model
    $0 --model=phi-4 --gpu=1
    $0 --model=qwen3-30b-instruct --gpu=2,3

MODEL ROUTING STRATEGY:
    Small models (phi-4, qwen3-embedding): GPU 1 (single GPU)
    Medium models (qwen3-30b): GPUs 2,3 (tensor parallelism)
    Large models (70B+): GPUs 2,3,4,5 (4+ GPUs)

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

# Check if model fits on GPU(s)
check_model_fits() {
    local model_name="$1"
    local gpu_list="$2"
    local tensor_parallel="${3:-1}"
    
    local model_size=$(calculate_model_size "$model_name")
    info "Model $model_name requires ~${model_size}GB"
    
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
    
    # Calculate total GPU memory
    local total_memory=0
    for gpu in "${gpus[@]}"; do
        if [ -z "${GPU_MEMORY[$gpu]:-}" ]; then
            error "GPU $gpu not found in vLLM container"
            return 1
        fi
        total_memory=$((total_memory + ${GPU_MEMORY[$gpu]}))
    done
    
    local available_memory=$((total_memory * 90 / 100))
    
    info "Total GPU memory: ${total_memory}GB (${available_memory}GB usable)"
    info "Tensor parallelism: $tensor_parallel GPU(s)"
    
    if [ "$model_size" -le "$available_memory" ]; then
        success "Model fits on GPU(s) ${gpu_list}"
        return 0
    else
        error "Model ($model_size GB) does not fit on GPU(s) ${gpu_list} (${available_memory}GB available)"
        return 1
    fi
}

# Generate vLLM and LiteLLM routing configuration
generate_routing_config() {
    local model_short="$1"
    local gpu_list="$2"
    local tensor_parallel="${3:-1}"
    local vllm_port="${4:-8000}"  # Default vLLM port, can be overridden for separate instances
    
    local model_full="${MODEL_NAMES[$model_short]:-$model_short}"
    
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
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "2. LiteLLM Configuration (REQUIRED - LiteLLM does NOT auto-discover)"
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
    echo "# IMPORTANT: LiteLLM must be manually configured for each model."
    echo "# vLLM serves the model, but LiteLLM needs explicit routing configuration."
    echo ""
}

# Interactive routing configuration
interactive_routing() {
    section "Interactive Model Routing"
    
    detect_vllm_gpus
    
    echo "Available models:"
    for model in "${!MODEL_NAMES[@]}"; do
        local size=$(calculate_model_size "${MODEL_NAMES[$model]}")
        echo "  $model (~${size}GB)"
    done
    echo ""
    
    read -p "Select model to configure (or 'all' for all models): " selected_model
    
    if [ "$selected_model" = "all" ]; then
        # Configure all models
        info "Configuring all models..."
        
        # Small models on GPU 1
        if [ -n "${GPU_MEMORY[1]:-}" ]; then
            check_model_fits "${MODEL_NAMES[phi-4]}" "1" "1" && generate_routing_config "phi-4" "1" "1"
            check_model_fits "${MODEL_NAMES[qwen3-embedding]}" "1" "1" && generate_routing_config "qwen3-embedding" "1" "1"
        fi
        
        # Large models on multiple GPUs
        if [ ${#GPU_MEMORY[@]} -ge 2 ]; then
            local gpu_list="2"
            if [ ${#GPU_MEMORY[@]} -ge 3 ]; then
                gpu_list="2,3"
            fi
            check_model_fits "${MODEL_NAMES[qwen3-30b-instruct]}" "$gpu_list" "2" && generate_routing_config "qwen3-30b-instruct" "$gpu_list" "2"
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
        
        check_model_fits "$model_full" "$gpu_list" "$tensor_parallel" && generate_routing_config "$selected_model" "$gpu_list" "$tensor_parallel"
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
    info "Update Ansible variables and redeploy vLLM service:"
    echo "  cd provision/ansible"
    echo "  ansible-playbook -i inventory/production/hosts.yml site.yml --tags vllm,litellm"
}

# Parse arguments
INTERACTIVE=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --interactive)
            INTERACTIVE=true
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

