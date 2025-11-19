#!/usr/bin/env bash
#
# Update Model Configuration Database
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Inspect downloaded models and update MODEL_CONFIG database
#
# This script analyzes downloaded models to detect:
# - Quantization (GPTQ, AWQ, BitsAndBytes)
# - Precision (fp32, fp16, bf16, int8, int4)
# - Actual GPU memory requirements
# - Parameter counts
#
# USAGE:
#   bash update-model-config.sh [model_path]
#
# If model_path is provided, analyzes that specific model.
# Otherwise, analyzes all models in HuggingFace cache.
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"
ROUTING_SCRIPT="${SCRIPT_DIR}/configure-vllm-model-routing.sh"
HUGGINGFACE_CACHE="/var/lib/llm-models/huggingface"
MODELS_DIR="${HUGGINGFACE_CACHE}/hub"
VENV_DIR="/opt/model-downloader"

# Check if Python venv exists
if [ ! -d "$VENV_DIR" ]; then
    error "Model downloader venv not found: $VENV_DIR"
    error "Run setup-llm-models.sh first"
    exit 1
fi

# Analyze a single model
analyze_model() {
    local model_name="$1"
    local model_path="$2"
    
    info "Analyzing: $model_name"
    
    # Find the actual model files
    local snapshot_dir=$(find "$model_path/snapshots" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)
    
    if [ -z "$snapshot_dir" ]; then
        warn "No snapshot directory found for $model_name"
        return 1
    fi
    
    # Detect quantization and precision
    local quantization="none"
    local precision="fp16"
    local model_size_bytes=0
    local safetensors_files=()
    local gptq_files=()
    local awq_files=()
    local gguf_files=()
    
    # Find model files
    if [ -d "$snapshot_dir" ]; then
        # Count safetensors files
        safetensors_files=($(find "$snapshot_dir" -name "*.safetensors" -type f 2>/dev/null))
        
        # Check for GPTQ files (usually named *-gptq-*.safetensors or config.json mentions GPTQ)
        gptq_files=($(find "$snapshot_dir" -name "*gptq*.safetensors" -o -name "*GPTQ*.safetensors" 2>/dev/null))
        
        # Check for AWQ files
        awq_files=($(find "$snapshot_dir" -name "*.awq" -o -name "*awq*.safetensors" 2>/dev/null))
        
        # Check for GGUF files
        gguf_files=($(find "$snapshot_dir" -name "*.gguf" -type f 2>/dev/null))
        
        # Calculate total model size
        model_size_bytes=$(du -sb "$snapshot_dir" 2>/dev/null | awk '{print $1}')
    fi
    
    # Check config.json for quantization info
    local config_file="$snapshot_dir/config.json"
    local quantization_config=""
    local dtype_config=""
    
    if [ -f "$config_file" ]; then
        # Use Python to parse JSON (more reliable than grep)
        quantization_config=$("${VENV_DIR}/bin/python3" << PYTHON_EOF
import json
import os

config_file = "${config_file}"
if os.path.exists(config_file):
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    # Check for quantization info
    quantization = config.get('quantization_config', {})
    if quantization:
        quant_method = quantization.get('quant_method', '')
        bits = quantization.get('bits', '')
        print(f"{quant_method}|{bits}")
    else:
        # Check for other quantization indicators
        if 'gptq' in str(config).lower():
            print("gptq|")
        elif 'awq' in str(config).lower():
            print("awq|")
        else:
            print("none|")
    
    # Check for dtype
    dtype = config.get('torch_dtype', '')
    if dtype:
        print(f"dtype:{dtype}")
PYTHON_EOF
)
        
        # Parse quantization config
        if echo "$quantization_config" | grep -q "gptq"; then
            quantization="gptq"
            # Try to detect bits from filename or config
            if [ ${#gptq_files[@]} -gt 0 ]; then
                local gptq_file="${gptq_files[0]}"
                if echo "$gptq_file" | grep -qi "int4\|4bit\|4-bit"; then
                    precision="int4"
                elif echo "$gptq_file" | grep -qi "int8\|8bit\|8-bit"; then
                    precision="int8"
                fi
            fi
        elif echo "$quantization_config" | grep -q "awq"; then
            quantization="awq"
            if [ ${#awq_files[@]} -gt 0 ]; then
                local awq_file="${awq_files[0]}"
                if echo "$awq_file" | grep -qi "int4\|4bit\|4-bit"; then
                    precision="int4"
                elif echo "$awq_file" | grep -qi "int8\|8bit\|8-bit"; then
                    precision="int8"
                fi
            fi
        elif [ ${#gguf_files[@]} -gt 0 ]; then
            quantization="gguf"
            # GGUF files often have quantization in filename
            local gguf_file="${gguf_files[0]}"
            if echo "$gguf_file" | grep -qi "q4\|Q4\|int4"; then
                precision="int4"
            elif echo "$gguf_file" | grep -qi "q8\|Q8\|int8"; then
                precision="int8"
            fi
        fi
        
        # Parse dtype from config
        if echo "$quantization_config" | grep -q "dtype:"; then
            dtype_config=$(echo "$quantization_config" | grep "dtype:" | cut -d: -f2 | tr -d ' "')
            case "$dtype_config" in
                *float32*|*fp32*) precision="fp32" ;;
                *float16*|*fp16*) precision="fp16" ;;
                *bfloat16*|*bf16*) precision="bf16" ;;
            esac
        fi
    fi
    
    # Estimate parameters from model size
    local model_size_gb=$(echo "scale=2; $model_size_bytes / 1024 / 1024 / 1024" | bc -l)
    
    # Estimate parameters based on size and precision
    local params_billions=0
    local bytes_per_param=2  # Default to fp16
    
    case "$precision" in
        fp32) bytes_per_param=4 ;;
        fp16|bf16) bytes_per_param=2 ;;
        int8) bytes_per_param=1 ;;
        int4) bytes_per_param=0.5 ;;
    esac
    
    # Rough estimate: model_size_gb / bytes_per_param = params_billions
    # But need to account for overhead (tokenizer, config, etc.) - assume 10% overhead
    params_billions=$(echo "scale=1; ($model_size_gb / $bytes_per_param) * 0.9" | bc -l)
    
    # Round to nearest integer
    params_billions=$(printf "%.0f" "$params_billions")
    
    # Estimate GPU size (model weights + overhead)
    # For quantized models, use actual size; for FP16, add overhead
    local gpu_size_gb
    if [ "$quantization" != "none" ]; then
        # Quantized: actual size + 20% overhead
        gpu_size_gb=$(echo "scale=1; $model_size_gb * 1.2" | bc -l)
    else
        # FP16: model weights + KV cache estimate + overhead
        gpu_size_gb=$(echo "scale=1; $model_size_gb * 1.3" | bc -l)
    fi
    
    # Round GPU size
    gpu_size_gb=$(printf "%.0f" "$gpu_size_gb")
    
    # Build notes
    local notes=""
    if [ "$quantization" != "none" ]; then
        notes="${quantization} ${precision}, ~${gpu_size_gb}GB GPU"
    else
        notes="${params_billions}B params, ${precision}, ~${gpu_size_gb}GB GPU"
    fi
    
    # Output configuration line
    echo "${params_billions}|${precision}|${quantization}|${gpu_size_gb}|${notes}"
}

# Update MODEL_CONFIG in routing script
update_routing_script() {
    local model_name="$1"
    local config_line="$2"
    
    if [ ! -f "$ROUTING_SCRIPT" ]; then
        warn "Routing script not found: $ROUTING_SCRIPT"
        return 1
    fi
    
    # Escape model name for sed
    local escaped_name=$(echo "$model_name" | sed 's/[[\.*^$()+?{|]/\\&/g')
    
    # Check if model already exists in MODEL_CONFIG
    if grep -q "\[\"${escaped_name}\"\]=" "$ROUTING_SCRIPT"; then
        info "Updating existing entry for $model_name"
        # Update existing entry
        sed -i "s|\[\"${escaped_name}\"\]=\".*\"|[\"${escaped_name}\"]=\"${config_line}\"|" "$ROUTING_SCRIPT"
    else
        info "Adding new entry for $model_name"
        # Find MODEL_CONFIG array and add entry before closing
        # Insert before the closing parenthesis
        sed -i "/^declare -A MODEL_CONFIG=(/,/^)$/{
            /^)$/i\\
    [\"${model_name}\"]=\"${config_line}\"
        }" "$ROUTING_SCRIPT"
    fi
    
    success "Updated MODEL_CONFIG for $model_name"
}

# Main execution
main() {
    echo "=========================================="
    echo "Model Configuration Updater"
    echo "=========================================="
    echo ""
    
    # Check if specific model provided
    if [ $# -gt 0 ]; then
        local model_name="$1"
        local model_dir=$(echo "$model_name" | sed 's/\//--/g')
        local model_path="${MODELS_DIR}/models--${model_dir}"
        
        if [ ! -d "$model_path" ]; then
            error "Model not found: $model_path"
            error "Run setup-llm-models.sh to download models first"
            exit 1
        fi
        
        info "Analyzing model: $model_name"
        local config_line=$(analyze_model "$model_name" "$model_path")
        
        if [ -n "$config_line" ]; then
            echo ""
            info "Configuration: $config_line"
            echo ""
            read -p "Update MODEL_CONFIG in routing script? (Y/n): " -n 1 -r
            echo ""
            if [[ ! $REPLY =~ ^[Nn]$ ]]; then
                update_routing_script "$model_name" "$config_line"
            fi
        fi
    else
        # Analyze all models
        info "Analyzing all models in cache..."
        echo ""
        
        local updated=0
        for model_dir in "${MODELS_DIR}"/models--*; do
            if [ ! -d "$model_dir" ]; then
                continue
            fi
            
            local model_name=$(basename "$model_dir" | sed 's/models--//g' | sed 's/--/\//g')
            local config_line=$(analyze_model "$model_name" "$model_dir")
            
            if [ -n "$config_line" ]; then
                echo "  $model_name: $config_line"
                update_routing_script "$model_name" "$config_line"
                updated=$((updated + 1))
            fi
        done
        
        echo ""
        success "Updated $updated model configurations"
    fi
    
    echo ""
    info "Next steps:"
    echo "  1. Review updated MODEL_CONFIG in: $ROUTING_SCRIPT"
    echo "  2. Test memory estimation:"
    echo "     bash $ROUTING_SCRIPT --interactive"
    echo ""
}

main "$@"

