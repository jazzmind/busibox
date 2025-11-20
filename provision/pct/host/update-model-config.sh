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
REPO_ROOT="$(cd "${PCT_DIR}/../.." && pwd)"
MODEL_REGISTRY="${REPO_ROOT}/provision/ansible/group_vars/all/model_registry.yml"
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
import re

config_file = "${config_file}"
if os.path.exists(config_file):
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    quant_method = ""
    quant_bits = ""
    
    # Check for quantization_config section
    quantization = config.get('quantization_config', {})
    if quantization:
        quant_method = quantization.get('quant_method', '')
        quant_bits = str(quantization.get('bits', ''))
    
    # Check model_type for quantization hints
    model_type = config.get('model_type', '').lower()
    
    # Check for GPTQ in various places
    config_str = json.dumps(config).lower()
    if 'gptq' in config_str or 'gptq' in model_type:
        quant_method = 'gptq'
        # Try to extract bits from config or filenames
        if not quant_bits:
            if '4bit' in config_str or 'int4' in config_str:
                quant_bits = '4'
            elif '8bit' in config_str or 'int8' in config_str:
                quant_bits = '8'
    
    # Check for AWQ
    if 'awq' in config_str or 'awq' in model_type:
        quant_method = 'awq'
        if not quant_bits:
            if '4bit' in config_str or 'int4' in config_str:
                quant_bits = '4'
            elif '8bit' in config_str or 'int8' in config_str:
                quant_bits = '8'
    
    # Check for BitsAndBytes
    if 'bitsandbytes' in config_str or 'bnb' in config_str:
        quant_method = 'bitsandbytes'
        if not quant_bits:
            if '4bit' in config_str or 'int4' in config_str:
                quant_bits = '4'
            elif '8bit' in config_str or 'int8' in config_str:
                quant_bits = '8'
    
    print(f"{quant_method}|{quant_bits}")
    
    # Check for dtype
    dtype = config.get('torch_dtype', '')
    if not dtype:
        # Check in model config or other places
        dtype = config.get('dtype', '')
    if dtype:
        print(f"dtype:{dtype}")
PYTHON_EOF
)
        
        # Parse quantization config
        local quant_method=$(echo "$quantization_config" | head -1 | cut -d'|' -f1)
        local quant_bits=$(echo "$quantization_config" | head -1 | cut -d'|' -f2)
        
        if [ "$quant_method" != "none" ] && [ -n "$quant_method" ]; then
            quantization="$quant_method"
            
            # Set precision based on quantization bits
            if [ "$quant_bits" = "4" ]; then
                precision="int4"
            elif [ "$quant_bits" = "8" ]; then
                precision="int8"
            fi
        fi
        
        # Also check filenames for quantization hints (backup detection)
        if [ "$quantization" = "none" ] || [ -z "$quantization" ]; then
            if [ ${#gptq_files[@]} -gt 0 ]; then
                quantization="gptq"
                local gptq_file="${gptq_files[0]}"
                if echo "$gptq_file" | grep -qi "int4\|4bit\|4-bit\|q4"; then
                    precision="int4"
                elif echo "$gptq_file" | grep -qi "int8\|8bit\|8-bit\|q8"; then
                    precision="int8"
                fi
            elif [ ${#awq_files[@]} -gt 0 ]; then
                quantization="awq"
                local awq_file="${awq_files[0]}"
                if echo "$awq_file" | grep -qi "int4\|4bit\|4-bit\|q4"; then
                    precision="int4"
                elif echo "$awq_file" | grep -qi "int8\|8bit\|8-bit\|q8"; then
                    precision="int8"
                fi
            elif [ ${#gguf_files[@]} -gt 0 ]; then
                quantization="gguf"
                local gguf_file="${gguf_files[0]}"
                if echo "$gguf_file" | grep -qi "q4\|Q4\|int4"; then
                    precision="int4"
                elif echo "$gguf_file" | grep -qi "q8\|Q8\|int8"; then
                    precision="int8"
                fi
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
    
    # Try to get parameters from config.json first
    local params_from_config=0
    if [ -f "$config_file" ]; then
        params_from_config=$("${VENV_DIR}/bin/python3" << PYTHON_EOF
import json
import os

config_file = "${config_file}"
if os.path.exists(config_file):
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    # Try various parameter count fields
    params = config.get('num_parameters', 0)
    if not params:
        params = config.get('num_parameters_total', 0)
    if not params:
        params = config.get('parameters', 0)
    
    # Convert to billions
    if params:
        params_billions = params / 1_000_000_000
        print(f"{params_billions:.1f}")
    else:
        print("0")
PYTHON_EOF
)
    fi
    
    # Estimate parameters based on size and precision
    local params_billions=0
    local bytes_per_param=2  # Default to fp16
    
    case "$precision" in
        fp32) bytes_per_param=4 ;;
        fp16|bf16) bytes_per_param=2 ;;
        int8) bytes_per_param=1 ;;
        int4) bytes_per_param=0.5 ;;
    esac
    
    # Use config if available, otherwise estimate from size
    if [ "$(echo "$params_from_config > 0" | bc -l)" -eq 1 ]; then
        params_billions=$(printf "%.0f" "$params_from_config")
        info "  Parameters from config.json: ${params_billions}B"
    else
        # Rough estimate: model_size_gb / bytes_per_param = params_billions
        # Account for overhead (tokenizer, config, etc.) - assume 15% overhead
        params_billions=$(echo "scale=1; ($model_size_gb / $bytes_per_param) * 0.85" | bc -l)
        params_billions=$(printf "%.0f" "$params_billions")
        info "  Estimated parameters from size: ${params_billions}B"
    fi
    
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

# Update model_configs in model_registry.yml
update_model_registry() {
    local model_name="$1"
    local params_billions="$2"
    local precision="$3"
    local quantization="$4"
    local gpu_size_gb="$5"
    local notes="$6"
    
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
model_name = "${model_name}"
params_billions = ${params_billions}
precision = "${precision}"
quantization = "${quantization}"
gpu_size_gb = ${gpu_size_gb}
notes = "${notes}"

try:
    # Read existing YAML
    with open(registry_file, 'r') as f:
        content = f.read()
        data = yaml.safe_load(content)
    
    # Initialize model_configs if it doesn't exist
    if 'model_configs' not in data:
        data['model_configs'] = {}
    
    # Update or add model config
    data['model_configs'][model_name] = {
        'params_billions': params_billions,
        'precision': precision,
        'quantization': quantization,
        'gpu_size_gb': gpu_size_gb,
        'notes': notes
    }
    
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
    
    print(f"✓ Updated model_configs for {model_name}")
except Exception as e:
    print(f"ERROR: Failed to update registry: {e}", file=sys.stderr)
    sys.exit(1)
PYTHON_EOF
    
    if [ $? -eq 0 ]; then
        success "Updated model_configs for $model_name"
        return 0
    else
        error "Failed to update model_configs for $model_name"
        return 1
    fi
}

# Main execution
main() {
    # If called non-interactively (from setup-llm-models.sh), skip prompts
    local interactive=true
    if [ "${1:-}" = "--non-interactive" ]; then
        interactive=false
        shift
    fi
    
    if [ "$interactive" = true ]; then
        echo "=========================================="
        echo "Model Configuration Updater"
        echo "=========================================="
        echo ""
    fi
    
    # Check if specific model provided
    if [ $# -gt 0 ]; then
        local model_name="$1"
        local model_dir=$(echo "$model_name" | sed 's/\//--/g')
        local model_path="${MODELS_DIR}/models--${model_dir}"
        
        if [ ! -d "$model_path" ]; then
            if [ "$interactive" = true ]; then
                error "Model not found: $model_path"
                error "Run setup-llm-models.sh to download models first"
                exit 1
            else
                # Non-interactive: just return silently
                return 1
            fi
        fi
        
        if [ "$interactive" = true ]; then
            info "Analyzing model: $model_name"
        fi
        local config_line=$(analyze_model "$model_name" "$model_path")
        
        if [ -n "$config_line" ]; then
            # Parse config line: params|precision|quantization|gpu_size|notes
            IFS='|' read -r params precision quantization gpu_size notes <<< "$config_line"
            
            if [ "$interactive" = true ]; then
                echo ""
                info "Configuration: $config_line"
                echo "  Parameters: ${params}B"
                echo "  Precision: $precision"
                echo "  Quantization: $quantization"
                echo "  GPU Size: ${gpu_size}GB"
                echo "  Notes: $notes"
                echo ""
                read -p "Update model_configs in model_registry.yml? (Y/n): " -n 1 -r
                echo ""
                if [[ ! $REPLY =~ ^[Nn]$ ]]; then
                    update_model_registry "$model_name" "$params" "$precision" "$quantization" "$gpu_size" "$notes"
                fi
            else
                # Non-interactive: auto-update
                update_model_registry "$model_name" "$params" "$precision" "$quantization" "$gpu_size" "$notes" 2>/dev/null || true
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
                # Parse config line: params|precision|quantization|gpu_size|notes
                IFS='|' read -r params precision quantization gpu_size notes <<< "$config_line"
                echo "  $model_name: $config_line"
                update_model_registry "$model_name" "$params" "$precision" "$quantization" "$gpu_size" "$notes"
                updated=$((updated + 1))
            fi
        done
        
        echo ""
        success "Updated $updated model configurations"
    fi
    
    echo ""
    info "Next steps:"
    echo "  1. Review updated model_configs in: $MODEL_REGISTRY"
    echo "  2. Test memory estimation:"
    echo "     bash ${SCRIPT_DIR}/configure-vllm-model-routing.sh --interactive"
    echo ""
}

main "$@"

