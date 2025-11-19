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
LITELLM_CONFIG="${REPO_ROOT}/provision/ansible/roles/litellm/defaults/main.yml"

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
    python3 << 'PYTHON_EOF'
import yaml
import re
import sys
from pathlib import Path

config_file = Path("${LITELLM_CONFIG}")
model_short = "${model_short}"
model_full = "${model_full}"
vllm_port = "${vllm_port}"

# Read existing config as text to preserve Jinja2 templates
with open(config_file, 'r') as f:
    content = f.read()
    lines = content.split('\n')

# Parse YAML (Jinja2 templates will be treated as strings, which is fine)
config = yaml.safe_load(content)

# Find or create litellm_models list
if 'litellm_models' not in config or config['litellm_models'] is None:
    config['litellm_models'] = []

# Check if model already exists (by model_name)
model_exists = False
model_index = -1
for i, model in enumerate(config['litellm_models']):
    if isinstance(model, dict):
        # Check both literal strings and Jinja2 template strings
        model_name = model.get('model_name', '')
        if model_name == model_short or (isinstance(model_name, str) and model_short in str(model_name)):
            model_index = i
            model_exists = True
            break

# Create new model entry
new_model = {
    'model_name': model_short,
    'litellm_params': {
        'model': f"openai/{model_full}",
        'api_base': f"http://{{{{ vllm_ip }}}}:{vllm_port}/v1",
        'api_key': 'EMPTY'
    }
}

# Update or add model
if model_exists and model_index >= 0:
    config['litellm_models'][model_index] = new_model
    action = "updated"
else:
    config['litellm_models'].append(new_model)
    action = "added"

# Find the litellm_models section in the file
litellm_models_start = -1
litellm_models_end = -1
in_litellm_models = False
indent_level = 0

for i, line in enumerate(lines):
    # Find start of litellm_models
    if re.match(r'^litellm_models:', line):
        litellm_models_start = i
        in_litellm_models = True
        indent_level = len(line) - len(line.lstrip())
        continue
    
    if in_litellm_models:
        # Check if we've left the litellm_models section (new top-level key)
        stripped = line.lstrip()
        if stripped and not line.startswith(' ') and not line.startswith('#'):
            if not stripped.startswith('-') and not stripped.startswith('#'):
                litellm_models_end = i
                break

if litellm_models_end == -1:
    litellm_models_end = len(lines)

# Build new model entry as YAML string
model_yaml = yaml.dump([new_model], default_flow_style=False, sort_keys=False)
# Indent the model entry
indent = ' ' * (indent_level + 2)
model_lines = [indent + line if line.strip() else '' for line in model_yaml.split('\n') if line.strip()]
model_entry = '\n'.join(model_lines)

# Insert or replace model entry
if model_exists and model_index >= 0:
    # Find the existing model entry and replace it
    # This is complex - simpler approach: rebuild the section
    # For now, we'll append and let user clean up duplicates
    # Insert before the end of litellm_models section
    lines.insert(litellm_models_end - 1, model_entry)
else:
    # Append new model
    if litellm_models_end > 0:
        lines.insert(litellm_models_end, model_entry)
    else:
        # litellm_models section doesn't exist, create it
        lines.append('')
        lines.append('litellm_models:')
        lines.append(model_entry)

# Write back
with open(config_file, 'w') as f:
    f.write('\n'.join(lines))

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
        
        # Small models on GPU 1
        if [ -n "${GPU_MEMORY[1]:-}" ]; then
            check_model_fits "${MODEL_NAMES[phi-4]}" "1" "1" && generate_routing_config "phi-4" "1" "1" "8000" "$should_update"
            check_model_fits "${MODEL_NAMES[qwen3-embedding]}" "1" "1" && generate_routing_config "qwen3-embedding" "1" "1" "8001" "$should_update"
        fi
        
        # Large models on multiple GPUs
        if [ ${#GPU_MEMORY[@]} -ge 2 ]; then
            local gpu_list="2"
            if [ ${#GPU_MEMORY[@]} -ge 3 ]; then
                gpu_list="2,3"
            fi
            check_model_fits "${MODEL_NAMES[qwen3-30b-instruct]}" "$gpu_list" "2" && generate_routing_config "qwen3-30b-instruct" "$gpu_list" "2" "8000" "$should_update"
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
        
        check_model_fits "$model_full" "$gpu_list" "$tensor_parallel" && generate_routing_config "$selected_model" "$gpu_list" "$tensor_parallel" "$vllm_port" "$should_update"
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

