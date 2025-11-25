#!/usr/bin/env bash
#
# Update Model Configuration Database
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Inspect downloaded models and update MODEL_CONFIG database
#
# This script analyzes downloaded models to detect:
# - Quantization (GPTQ, AWQ, BitsAndBytes, GGUF)
# - Precision (fp32, fp16, bf16, int8, int4)
# - Actual GPU memory requirements
# - Parameter counts
#
# IMPROVED DETECTION:
# - Uses HuggingFace API for accurate model metadata
# - Analyzes config.json for quantization settings
# - Inspects file names and sizes for quantization hints
# - Falls back to intelligent size-based estimation
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
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Debug mode (set by --debug flag)
DEBUG=false

info() {
    echo -e "${BLUE}[INFO]${NC} $1" >&2
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1" >&2
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1" >&2
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

debug() {
    if [ "$DEBUG" = true ]; then
        echo -e "${CYAN}[DEBUG]${NC} $1" >&2
    fi
}

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "${PCT_DIR}/../.." && pwd)"
MODEL_REGISTRY="${REPO_ROOT}/provision/ansible/group_vars/all/model_registry.yml"
MODEL_CONFIG="${REPO_ROOT}/provision/ansible/group_vars/all/model_config.yml"
HUGGINGFACE_CACHE="/var/lib/llm-models/huggingface"
MODELS_DIR="${HUGGINGFACE_CACHE}/hub"
VENV_DIR="/opt/model-downloader"

# HuggingFace token (optional, but helps with gated models and API access)
HF_TOKEN="${HF_TOKEN:-}"
if [[ -z "$HF_TOKEN" ]] && [[ -f "$HOME/.huggingface/token" ]]; then
    HF_TOKEN=$(cat "$HOME/.huggingface/token")
fi
export HF_TOKEN

# Check if Python venv exists
if [ ! -d "$VENV_DIR" ]; then
    error "Model downloader venv not found: $VENV_DIR"
    error "Run setup-llm-models.sh first"
    exit 1
fi

# Check if huggingface_hub is installed (for API access)
if ! "${VENV_DIR}/bin/python3" -c "import huggingface_hub" 2>/dev/null; then
    warn "huggingface_hub not installed - API-based model info will be unavailable"
    warn "Install with: ${VENV_DIR}/bin/pip install huggingface-hub"
    warn "Continuing with local file analysis only..."
fi

# Analyze a single model
analyze_model() {
    local model_name="$1"
    local model_path="$2"
    
    info "Analyzing: $model_name" >&2
    debug "Model path: $model_path" >&2
    
    # Find the actual model files
    # HuggingFace cache structure: models--org--model/snapshots/<hash>/
    local snapshot_dir=$(find "$model_path/snapshots" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)
    
    if [ -z "$snapshot_dir" ]; then
        warn "No snapshot directory found for $model_name" >&2
        debug "Snapshots path: $model_path/snapshots" >&2
        warn "  Model path: $model_path" >&2
        warn "  Snapshots dir exists: $([ -d "$model_path/snapshots" ] && echo "yes" || echo "no")" >&2
        if [ -d "$model_path/snapshots" ]; then
            warn "  Snapshots found: $(ls -1 "$model_path/snapshots" 2>/dev/null | wc -l | tr -d ' ')" >&2
        fi
        return 1
    fi
    
    info "  Snapshot directory: $snapshot_dir" >&2
    debug "Listing snapshot contents:" >&2
    if [ "$DEBUG" = true ]; then
        ls -lh "$snapshot_dir" 2>/dev/null | head -20 >&2
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
        # HuggingFace cache structure: models--org--model/snapshots/<hash>/ contains symlinks to blobs/
        # Use du -sbL to follow symlinks and get actual disk usage
        model_size_bytes=$(du -sbL "$snapshot_dir" 2>/dev/null | awk '{print $1}')
        
        # If du failed or returned 0/empty, try alternative methods
        if [ -z "$model_size_bytes" ] || [ "$model_size_bytes" = "0" ] || ! [[ "$model_size_bytes" =~ ^[0-9]+$ ]]; then
            # Method 2: Sum individual model files (safetensors, bin, etc.)
            # Use stat -f%z on macOS, stat -c%s on Linux
            if stat -f%z "$snapshot_dir" >/dev/null 2>&1; then
                # macOS
                model_size_bytes=$(find "$snapshot_dir" -type f \( -name "*.safetensors" -o -name "*.bin" -o -name "*.pt" -o -name "*.pth" -o -name "*.gguf" -o -name "*.onnx" \) -exec stat -f%z {} \; 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
            else
                # Linux
                model_size_bytes=$(find "$snapshot_dir" -type f \( -name "*.safetensors" -o -name "*.bin" -o -name "*.pt" -o -name "*.pth" -o -name "*.gguf" -o -name "*.onnx" \) -exec stat -c%s {} \; 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
            fi
        fi
        
        # If still 0, try summing ALL files in snapshot directory
        if [ -z "$model_size_bytes" ] || [ "$model_size_bytes" = "0" ] || ! [[ "$model_size_bytes" =~ ^[0-9]+$ ]]; then
            if stat -f%z "$snapshot_dir" >/dev/null 2>&1; then
                # macOS - sum all files
                model_size_bytes=$(find "$snapshot_dir" -type f -exec stat -f%z {} \; 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
            else
                # Linux - sum all files
                model_size_bytes=$(find "$snapshot_dir" -type f -exec stat -c%s {} \; 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
            fi
        fi
        
        # Debug output if still 0
        if [ -z "$model_size_bytes" ] || [ "$model_size_bytes" = "0" ] || ! [[ "$model_size_bytes" =~ ^[0-9]+$ ]]; then
            warn "  Warning: Could not determine model size for $model_name" >&2
            warn "  Snapshot dir: $snapshot_dir" >&2
            warn "  Directory exists: $([ -d "$snapshot_dir" ] && echo "yes" || echo "no")" >&2
            local file_count=$(find "$snapshot_dir" -type f 2>/dev/null | wc -l | tr -d ' ')
            warn "  Files found: $file_count" >&2
            if [ "$file_count" -gt 0 ]; then
                warn "  Sample files:" >&2
                find "$snapshot_dir" -type f -name "*.safetensors" -o -name "*.bin" 2>/dev/null | head -3 | while read -r f; do
                    if [ -f "$f" ]; then
                        local fsize=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo "unknown")
                        warn "    $(basename "$f"): ${fsize} bytes" >&2
                    fi
                done
            fi
            model_size_bytes=0
        fi
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
    # Ensure model_size_bytes is a valid number
    if [ -z "$model_size_bytes" ] || ! [[ "$model_size_bytes" =~ ^[0-9]+$ ]] || [ "$model_size_bytes" -eq 0 ]; then
        model_size_bytes=0
        model_size_gb=0
        warn "  Model size calculation failed or returned 0 - will try to estimate from config.json" >&2
    else
        model_size_gb=$(echo "scale=2; $model_size_bytes / 1024 / 1024 / 1024" | bc -l)
        info "  Model size: ${model_size_bytes} bytes (${model_size_gb}GB)" >&2
    fi
    
    # Try to get comprehensive model info from HuggingFace API first (most reliable)
    local params_from_hf=0
    local hf_model_size_gb=0
    local hf_quantization=""
    local hf_precision=""
    
    # Check if huggingface_hub is available
    if "${VENV_DIR}/bin/python3" -c "import huggingface_hub" 2>/dev/null; then
        info "  Fetching model info from HuggingFace API..." >&2
        
        # Get comprehensive model metadata from HuggingFace in one call
        local hf_result=$("${VENV_DIR}/bin/python3" << PYTHON_EOF
import json
import os
import sys
import re

model_name = "${model_name}"

try:
    from huggingface_hub import model_info, hf_hub_download
    
    result = {
        'params': 0,
        'size_gb': 0,
        'quantization': '',
        'precision': '',
        'error': ''
    }
    
    try:
        info = model_info(model_name, token=os.environ.get('HF_TOKEN'), files_metadata=True)
        
        # 1. Get parameter count from multiple sources
        params = None
        
        # Try model name parsing (e.g., "Qwen3-30B" -> 30B)
        name_match = re.search(r'(\d+\.?\d*)B', model_name, re.IGNORECASE)
        if name_match:
            params = float(name_match.group(1)) * 1_000_000_000
        
        # Try tags (often most reliable for parameter count)
        if hasattr(info, 'tags') and info.tags:
            for tag in info.tags:
                tag_lower = tag.lower()
                # Look for tags like "30b", "6.7b", "70b-instruct"
                tag_match = re.search(r'(\d+\.?\d*)b', tag_lower)
                if tag_match:
                    params = float(tag_match.group(1)) * 1_000_000_000
                    break
        
        # Try config from model card
        if not params and hasattr(info, 'config') and info.config:
            config = info.config
            for key in ['num_parameters', 'num_parameters_total', 'parameters', 'n_params']:
                if key in config and config[key]:
                    params = config[key]
                    break
        
        # Try cardData
        if not params and hasattr(info, 'cardData') and info.cardData:
            card_data = info.cardData if isinstance(info.cardData, dict) else {}
            # Check for parameter count in various places
            for key in ['parameters', 'model_parameters', 'num_parameters']:
                if key in card_data:
                    val = card_data[key]
                    if isinstance(val, (int, float)):
                        params = val
                        break
                    elif isinstance(val, str):
                        # Parse strings like "6.7B" or "6700000000"
                        val_match = re.search(r'(\d+\.?\d*)\s*([BMK])?', val, re.IGNORECASE)
                        if val_match:
                            num = float(val_match.group(1))
                            unit = val_match.group(2).upper() if val_match.group(2) else ''
                            if unit == 'B':
                                params = num * 1_000_000_000
                            elif unit == 'M':
                                params = num * 1_000_000
                            elif unit == 'K':
                                params = num * 1_000
                            else:
                                params = num
                            break
        
        if params and params > 0:
            result['params'] = params / 1_000_000_000  # Convert to billions
        
        # 2. Get file sizes and detect quantization from filenames
        total_size_bytes = 0
        quantization_hints = set()
        precision_hints = set()
        
        if hasattr(info, 'siblings') and info.siblings:
            for sibling in info.siblings:
                if hasattr(sibling, 'rfilename') and sibling.rfilename:
                    filename = sibling.rfilename.lower()
                    
                    # Count model file sizes
                    if any(ext in filename for ext in ['.safetensors', '.bin', '.pt', '.pth', '.gguf', '.onnx']):
                        if hasattr(sibling, 'size') and sibling.size:
                            total_size_bytes += sibling.size
                    
                    # Detect quantization from filenames
                    if any(q in filename for q in ['gptq', 'gptq-int4', 'gptq-4bit']):
                        quantization_hints.add('gptq')
                    if any(q in filename for q in ['awq', 'awq-int4', 'awq-4bit']):
                        quantization_hints.add('awq')
                    if 'gguf' in filename:
                        quantization_hints.add('gguf')
                    if any(q in filename for q in ['bnb', 'bitsandbytes', '8bit', '4bit']):
                        quantization_hints.add('bitsandbytes')
                    
                    # Detect precision from filenames
                    if any(p in filename for p in ['int4', '4bit', 'q4_']):
                        precision_hints.add('int4')
                    elif any(p in filename for p in ['int8', '8bit', 'q8_']):
                        precision_hints.add('int8')
                    elif 'fp16' in filename or 'float16' in filename:
                        precision_hints.add('fp16')
                    elif 'bf16' in filename or 'bfloat16' in filename:
                        precision_hints.add('bf16')
                    elif 'fp32' in filename or 'float32' in filename:
                        precision_hints.add('fp32')
        
        if total_size_bytes > 0:
            result['size_gb'] = total_size_bytes / (1024 ** 3)
        
        # 3. Check tags for quantization info
        if hasattr(info, 'tags') and info.tags:
            for tag in info.tags:
                tag_lower = tag.lower()
                if 'gptq' in tag_lower:
                    quantization_hints.add('gptq')
                if 'awq' in tag_lower:
                    quantization_hints.add('awq')
                if 'gguf' in tag_lower:
                    quantization_hints.add('gguf')
                if 'quantized' in tag_lower or '4bit' in tag_lower or '8bit' in tag_lower:
                    if 'gptq' not in tag_lower and 'awq' not in tag_lower and 'gguf' not in tag_lower:
                        quantization_hints.add('bitsandbytes')
        
        # 4. Set quantization (prefer most specific)
        if quantization_hints:
            # Priority: GPTQ > AWQ > GGUF > BitsAndBytes
            if 'gptq' in quantization_hints:
                result['quantization'] = 'gptq'
            elif 'awq' in quantization_hints:
                result['quantization'] = 'awq'
            elif 'gguf' in quantization_hints:
                result['quantization'] = 'gguf'
            elif 'bitsandbytes' in quantization_hints:
                result['quantization'] = 'bitsandbytes'
        
        # 5. Set precision
        if precision_hints:
            # Priority: int4 > int8 > fp16 > bf16 > fp32
            if 'int4' in precision_hints:
                result['precision'] = 'int4'
            elif 'int8' in precision_hints:
                result['precision'] = 'int8'
            elif 'fp16' in precision_hints:
                result['precision'] = 'fp16'
            elif 'bf16' in precision_hints:
                result['precision'] = 'bf16'
            elif 'fp32' in precision_hints:
                result['precision'] = 'fp32'
        
        # Output JSON result
        print(json.dumps(result))
        
    except Exception as e:
        result['error'] = str(e)
        print(json.dumps(result))
        
except ImportError as e:
    print(json.dumps({'error': 'huggingface_hub not available', 'params': 0, 'size_gb': 0, 'quantization': '', 'precision': ''}))
except Exception as e:
    print(json.dumps({'error': str(e), 'params': 0, 'size_gb': 0, 'quantization': '', 'precision': ''}))
PYTHON_EOF
)
        
        # Parse JSON result
        if [ -n "$hf_result" ]; then
            debug "Raw HuggingFace API result: $hf_result" >&2
            
            # Try to parse JSON result
            params_from_hf=$(echo "$hf_result" | "${VENV_DIR}/bin/python3" -c "import json, sys; data=json.load(sys.stdin); print(data.get('params', 0))" 2>/dev/null || echo "0")
            hf_model_size_gb=$(echo "$hf_result" | "${VENV_DIR}/bin/python3" -c "import json, sys; data=json.load(sys.stdin); print(data.get('size_gb', 0))" 2>/dev/null || echo "0")
            hf_quantization=$(echo "$hf_result" | "${VENV_DIR}/bin/python3" -c "import json, sys; data=json.load(sys.stdin); print(data.get('quantization', ''))" 2>/dev/null || echo "")
            hf_precision=$(echo "$hf_result" | "${VENV_DIR}/bin/python3" -c "import json, sys; data=json.load(sys.stdin); print(data.get('precision', ''))" 2>/dev/null || echo "")
            local hf_error=$(echo "$hf_result" | "${VENV_DIR}/bin/python3" -c "import json, sys; data=json.load(sys.stdin); print(data.get('error', ''))" 2>/dev/null || echo "")
            
            if [ -n "$hf_error" ]; then
                warn "  HuggingFace API: $hf_error" >&2
            fi
            
            # Validate numeric results
            if [ -z "$params_from_hf" ] || ! [[ "$params_from_hf" =~ ^[0-9]+\.?[0-9]*$ ]]; then
                params_from_hf=0
            fi
            if [ -z "$hf_model_size_gb" ] || ! [[ "$hf_model_size_gb" =~ ^[0-9]+\.?[0-9]*$ ]]; then
                hf_model_size_gb=0
            fi
            
            # Report findings
            if [ "$(echo "$params_from_hf > 0" | bc -l)" -eq 1 ]; then
                info "  ✓ Parameters from HuggingFace: ${params_from_hf}B" >&2
            fi
            if [ "$(echo "$hf_model_size_gb > 0" | bc -l)" -eq 1 ]; then
                info "  ✓ Model size from HuggingFace: ${hf_model_size_gb}GB" >&2
            fi
            if [ -n "$hf_quantization" ]; then
                info "  ✓ Quantization detected from HF: $hf_quantization" >&2
                quantization="$hf_quantization"
            fi
            if [ -n "$hf_precision" ]; then
                info "  ✓ Precision detected from HF: $hf_precision" >&2
                precision="$hf_precision"
            fi
            
            debug "After HF API: quantization=$quantization, precision=$precision" >&2
        fi
    else
        warn "  huggingface_hub not available - install with: ${VENV_DIR}/bin/pip install huggingface-hub" >&2
        debug "Skipping HuggingFace API analysis" >&2
    fi
    
    # Try to get parameters from config.json as fallback
    local params_from_config=0
    if [ -z "$params_from_hf" ] || [ "$(echo "$params_from_hf == 0" | bc -l)" -eq 1 ]; then
    if [ -f "$config_file" ]; then
        params_from_config=$("${VENV_DIR}/bin/python3" << PYTHON_EOF
import json
import os

config_file = "${config_file}"
if os.path.exists(config_file):
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        
        # Try various parameter count fields
        params = config.get('num_parameters', 0)
        if not params:
            params = config.get('num_parameters_total', 0)
        if not params:
            params = config.get('parameters', 0)
        
        # Convert to billions
        if params and params > 0:
            params_billions = params / 1_000_000_000
            print(f"{params_billions:.1f}")
        else:
            print("0")
    except Exception as e:
        print("0")
else:
    print("0")
PYTHON_EOF
)
        fi
    fi
    
    # Determine final parameter count from best available source
    local params_billions=0
    local bytes_per_param=2  # Default to fp16
    
    # Update precision based on HuggingFace findings if not already set by config
    if [ -n "$hf_precision" ] && [ "$precision" = "fp16" ]; then
        precision="$hf_precision"
    fi
    
    case "$precision" in
        fp32) bytes_per_param=4 ;;
        fp16|bf16) bytes_per_param=2 ;;
        int8) bytes_per_param=1 ;;
        int4) bytes_per_param=0.5 ;;
    esac
    
    # Priority: HuggingFace API > config.json > size-based estimation
    if [ -n "$params_from_hf" ] && [ "$(echo "$params_from_hf > 0" | bc -l)" -eq 1 ]; then
        params_billions=$(printf "%.0f" "$params_from_hf")
        info "  → Using parameters from HuggingFace API: ${params_billions}B" >&2
        
        # Prefer HF model size if available (more accurate for quantized models)
        if [ -n "$hf_model_size_gb" ] && [ "$(echo "$hf_model_size_gb > 0" | bc -l)" -eq 1 ]; then
            model_size_gb="$hf_model_size_gb"
            info "  → Using model size from HuggingFace API: ${model_size_gb}GB" >&2
        fi
    elif [ -n "$params_from_config" ] && [ "$(echo "$params_from_config > 0" | bc -l)" -eq 1 ]; then
        params_billions=$(printf "%.0f" "$params_from_config")
        info "  → Using parameters from config.json: ${params_billions}B" >&2
    elif [ -n "$model_size_gb" ] && [ "$(echo "$model_size_gb > 0" | bc -l)" -eq 1 ]; then
        # Estimate from size: For quantized models, this is less accurate
        # but better than nothing
        if [ "$quantization" != "none" ] && [ -n "$quantization" ]; then
            # For quantized models, estimate conservatively
            # Quantized model size ≈ params * bytes_per_param * compression_ratio
            # We reverse this to estimate params
            params_billions=$(echo "scale=1; ($model_size_gb / $bytes_per_param) * 0.9" | bc -l)
            params_billions=$(printf "%.0f" "$params_billions")
            info "  → Estimated parameters from quantized size: ~${params_billions}B (${quantization}, ${model_size_gb}GB)" >&2
        else
            # For non-quantized models, more straightforward calculation
        # Account for overhead (tokenizer, config, etc.) - assume 15% overhead
        params_billions=$(echo "scale=1; ($model_size_gb / $bytes_per_param) * 0.85" | bc -l)
        params_billions=$(printf "%.0f" "$params_billions")
            info "  → Estimated parameters from size: ${params_billions}B (${precision}, ${model_size_gb}GB)" >&2
        fi
    else
        warn "  Could not determine parameters - no size or parameter data available" >&2
        params_billions=0
    fi
    
    # Estimate GPU memory requirements more accurately
    # Factors: model weights + KV cache + activation memory + CUDA kernels
    local gpu_size_gb
    
    if [ -z "$model_size_gb" ] || [ "$(echo "$model_size_gb == 0" | bc -l)" -eq 1 ]; then
        # Fallback: estimate from params and precision
        if [ "$(echo "$params_billions > 0" | bc -l)" -eq 1 ]; then
            model_size_gb=$(echo "scale=2; $params_billions * $bytes_per_param" | bc -l)
            info "  → Calculated model size from params: ${model_size_gb}GB" >&2
        else
            gpu_size_gb=0
        fi
    fi
    
    if [ "$(echo "$model_size_gb > 0" | bc -l)" -eq 1 ]; then
        # Quantized models need less overhead
        if [ "$quantization" != "none" ] && [ -n "$quantization" ]; then
            case "$quantization" in
                gptq|awq|gguf)
                    # Quantized models: weights + 15% overhead (KV cache, activations)
                    gpu_size_gb=$(echo "scale=1; $model_size_gb * 1.15" | bc -l)
                    ;;
                bitsandbytes)
                    # BitsAndBytes has slightly more overhead due to dynamic dequantization
                    gpu_size_gb=$(echo "scale=1; $model_size_gb * 1.20" | bc -l)
                    ;;
                *)
                    # Unknown quantization, be conservative
                    gpu_size_gb=$(echo "scale=1; $model_size_gb * 1.25" | bc -l)
                    ;;
            esac
        else
            # Non-quantized models need more memory
            case "$precision" in
                fp32)
                    # FP32: weights + 40% overhead (larger KV cache, activations)
                    gpu_size_gb=$(echo "scale=1; $model_size_gb * 1.40" | bc -l)
                    ;;
                bf16|fp16)
                    # FP16/BF16: weights + 30% overhead
                    gpu_size_gb=$(echo "scale=1; $model_size_gb * 1.30" | bc -l)
                    ;;
                *)
                    # Unknown precision, use conservative estimate
                    gpu_size_gb=$(echo "scale=1; $model_size_gb * 1.35" | bc -l)
                    ;;
            esac
    fi
    
        # Round up to nearest GB (always round up for safety)
        gpu_size_gb=$(echo "$gpu_size_gb" | awk '{print int($1 + 0.99)}')
    else
        gpu_size_gb=0
    fi
    
    # Build descriptive notes
    local notes=""
    if [ "$quantization" != "none" ] && [ -n "$quantization" ]; then
        # Quantized model
        notes="${params_billions}B params, ${quantization^^} ${precision}, ~${gpu_size_gb}GB GPU"
    else
        # Non-quantized model
        notes="${params_billions}B params, ${precision^^}, ~${gpu_size_gb}GB GPU"
    fi
    
    # Add model size to notes if available
    if [ "$(echo "$model_size_gb > 0" | bc -l)" -eq 1 ]; then
        notes="${notes} (${model_size_gb}GB disk)"
    fi
    
    debug "Final values: params=$params_billions, precision=$precision, quant=$quantization, gpu=$gpu_size_gb, size=$model_size_gb" >&2
    
    # Output configuration line: params|precision|quantization|gpu_size|notes
    echo "${params_billions}|${precision}|${quantization}|${gpu_size_gb}|${notes}"
}

# Update model_config.yml with technical analysis results
# Preserves existing GPU/port assignments from configure-vllm-model-routing.sh
update_model_config() {
    local model_name="$1"
    local params_billions="$2"
    local precision="$3"
    local quantization="$4"
    local gpu_size_gb="$5"
    local disk_size_gb="${6:-0}"
    local notes="$7"
    
    if [ ! -f "$MODEL_REGISTRY" ]; then
        warn "Model registry not found: $MODEL_REGISTRY"
        return 1
    fi
    
    # Use Python to update model_config.yml
    "${VENV_DIR}/bin/python3" << PYTHON_EOF
import yaml
import sys
import os
from pathlib import Path

registry_file = Path("${MODEL_REGISTRY}")
config_file = Path("${MODEL_CONFIG}")
model_name = "${model_name}"
params_billions = ${params_billions}
precision = "${precision}"
quantization = "${quantization}"
gpu_size_gb = ${gpu_size_gb}
disk_size_gb = ${disk_size_gb}
notes = "${notes}"

try:
    # Read model_registry.yml to get provider and model_key
    with open(registry_file, 'r') as f:
        registry_data = yaml.safe_load(f) or {}
    
    available_models = registry_data.get('available_models') or {}
    
    # Find model_key, provider, and tuning parameters for this model_name
    model_key = None
    provider = None
    # vLLM tuning parameters from registry
    gpu_memory_utilization = None
    max_model_len = None
    max_num_seqs = None
    cpu_offload_gb = None
    # Tool calling parameters from registry
    tool_calling = None
    tool_call_parser = None
    tool_chat_template = None
    
    for key, config in available_models.items():
        if config.get('model_name') == model_name:
            model_key = key
            provider = config.get('provider', 'vllm')
            # Extract tuning parameters (only for vLLM models)
            if provider == 'vllm':
                gpu_memory_utilization = config.get('gpu_memory_utilization')
                max_model_len = config.get('max_model_len')
                max_num_seqs = config.get('max_num_seqs')
                cpu_offload_gb = config.get('cpu_offload_gb')
                # Extract tool calling parameters
                tool_calling = config.get('tool_calling', False)
                tool_call_parser = config.get('tool_call_parser')
                tool_chat_template = config.get('tool_chat_template')
            break
    
    # If not found in registry, auto-detect provider from model name
    if not provider:
        model_name_lower = model_name.lower()
        if 'colpali' in model_name_lower or 'vidore/colpali' in model_name_lower:
            provider = 'colpali'
            model_key = 'colpali-v1.3'
        elif 'vikp/surya' in model_name_lower or 'surya_det' in model_name_lower:
            provider = 'marker'
            model_key = 'surya-det2'
        else:
            provider = 'vllm'
    
    # Read existing model_config.yml if it exists
    if config_file.exists():
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f) or {}
    else:
        config_data = {}
    
    # Initialize models dict
    if 'models' not in config_data:
        config_data['models'] = {}
    
    if not isinstance(config_data['models'], dict):
        config_data['models'] = {}
    
    # Get existing entry to preserve GPU/port assignments
    existing = config_data['models'].get(model_name, {})
    
    # Update or create model entry
    config_data['models'][model_name] = {
        # Technical details (from analysis)
        'params_billions': params_billions,
        'precision': precision,
        'quantization': quantization,
        'gpu_size_gb': gpu_size_gb,
        'disk_size_gb': disk_size_gb,
        'notes': notes,
        'analyzed': True,
        
        # Preserve existing GPU/port assignments (if any)
        'gpu': existing.get('gpu'),
        'port': existing.get('port'),
        'tensor_parallel': existing.get('tensor_parallel'),
        'assigned': existing.get('assigned', False),
        
        # Reference info from model_registry.yml
        'provider': provider or 'vllm',
        'model_key': model_key or model_name
    }
    
    # Add vLLM tuning parameters if available (only for vLLM models)
    if provider == 'vllm':
        if gpu_memory_utilization is not None:
            config_data['models'][model_name]['gpu_memory_utilization'] = gpu_memory_utilization
        if max_model_len is not None:
            config_data['models'][model_name]['max_model_len'] = max_model_len
        if max_num_seqs is not None:
            config_data['models'][model_name]['max_num_seqs'] = max_num_seqs
        if cpu_offload_gb is not None:
            config_data['models'][model_name]['cpu_offload_gb'] = cpu_offload_gb
        # Add tool calling parameters if configured
        if tool_calling is not None:
            config_data['models'][model_name]['tool_calling'] = tool_calling
        if tool_call_parser is not None:
            config_data['models'][model_name]['tool_call_parser'] = tool_call_parser
        if tool_chat_template is not None:
            config_data['models'][model_name]['tool_chat_template'] = tool_chat_template
    
    # Write back
    try:
        from ruamel.yaml import YAML
        yaml_writer = YAML()
        yaml_writer.preserve_quotes = True
        yaml_writer.width = 4096
        yaml_writer.default_flow_style = False
        with open(config_file, 'w') as f:
            yaml_writer.dump(config_data, f)
    except ImportError:
        # Fallback to standard yaml
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    
    print(f"✓ Updated model_config.yml for {model_name}")
except Exception as e:
    print(f"ERROR: Failed to update model_config.yml: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
PYTHON_EOF
    
    if [ $? -eq 0 ]; then
        success "Updated model_config.yml for $model_name"
        return 0
    else
        error "Failed to update model_config.yml for $model_name"
        return 1
    fi
}

# Initialize model_config.yml with all models from model_registry.yml
# Creates entries for unanalyzed models and API models
initialize_model_config() {
    if [ ! -f "$MODEL_REGISTRY" ]; then
        warn "Model registry not found: $MODEL_REGISTRY"
        return 1
    fi
    
    "${VENV_DIR}/bin/python3" << PYTHON_EOF
import yaml
import sys
import os
from pathlib import Path

registry_file = Path("${MODEL_REGISTRY}")
config_file = Path("${MODEL_CONFIG}")

try:
    # Read model_registry.yml
    with open(registry_file, 'r') as f:
        registry_data = yaml.safe_load(f) or {}
    
    available_models = registry_data.get('available_models') or {}
    api_providers = {'bedrock', 'openai', 'anthropic'}
    
    # Read existing model_config.yml if it exists
    if config_file.exists():
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f) or {}
    else:
        config_data = {}
    
    if 'models' not in config_data:
        config_data['models'] = {}
    
    if not isinstance(config_data['models'], dict):
        config_data['models'] = {}
    
    # Step 1: Build list of valid model names from registry
    valid_model_names = set()
    for model_key, model_config in available_models.items():
        model_name = model_config.get('model_name')
        if model_name:
            valid_model_names.add(model_name)
    
    # Step 2: Remove models from config that are no longer in registry
    models_to_remove = []
    for model_name in config_data['models'].keys():
        if model_name not in valid_model_names:
            models_to_remove.append(model_name)
    
    removed_count = 0
    for model_name in models_to_remove:
        print(f"  Removing obsolete model: {model_name}")
        del config_data['models'][model_name]
        removed_count += 1
    
    if removed_count > 0:
        print(f"  Removed {removed_count} obsolete model(s) from config")
    
    # Step 3: Ensure all models from registry are in config
    added_count = 0
    for model_key, model_config in available_models.items():
        model_name = model_config.get('model_name')
        provider = model_config.get('provider', 'vllm').lower()
        
        if not model_name:
            continue
        
        # Get existing entry or create new
        existing = config_data['models'].get(model_name, {})
        is_new = len(existing) == 0
        
        # Only create/update if not already analyzed
        if not existing.get('analyzed', False):
            if is_new:
                added_count += 1
                print(f"  Adding new model: {model_name}")
            
            if provider in api_providers:
                # API-based model
                config_data['models'][model_name] = {
                    'provider': provider,
                    'model_key': model_key,
                    'gpu': None,
                    'port': None,
                    'tensor_parallel': None,
                    'assigned': False,
                    'analyzed': False,
                    'params_billions': 0,
                    'precision': 'unknown',
                    'quantization': 'none',
                    'gpu_size_gb': 0,
                    'disk_size_gb': 0,
                    'notes': f'API-based model ({provider})'
                }
            else:
                # Local model (not yet analyzed)
                # Auto-detect provider from model name if not in registry
                if provider is None or provider == 'vllm':
                    model_name_lower = model_name.lower()
                    if 'colpali' in model_name_lower or 'vidore/colpali' in model_name_lower:
                        provider = 'colpali'
                    elif 'vikp/surya' in model_name_lower or 'surya_det' in model_name_lower:
                        provider = 'marker'
                    else:
                        provider = 'vllm'
                
                config_data['models'][model_name] = {
                    'provider': provider,
                    'model_key': model_key,
                    'gpu': None,
                    'port': None,
                    'tensor_parallel': None,
                    'assigned': False,
                    'analyzed': False,
                    'params_billions': 0,
                    'precision': 'fp16',
                    'quantization': 'none',
                    'gpu_size_gb': 0,
                    'disk_size_gb': 0,
                    'notes': 'Not yet analyzed - run update-model-config.sh'
                }
                
                # Add vLLM tuning parameters from registry (if available)
                if provider == 'vllm':
                    if 'gpu_memory_utilization' in model_config:
                        config_data['models'][model_name]['gpu_memory_utilization'] = model_config['gpu_memory_utilization']
                    if 'max_model_len' in model_config:
                        config_data['models'][model_name]['max_model_len'] = model_config['max_model_len']
                    if 'max_num_seqs' in model_config:
                        config_data['models'][model_name]['max_num_seqs'] = model_config['max_num_seqs']
                    if 'cpu_offload_gb' in model_config:
                        config_data['models'][model_name]['cpu_offload_gb'] = model_config['cpu_offload_gb']
        else:
            # Preserve existing analyzed data, but update provider/model_key if changed
            config_data['models'][model_name]['provider'] = provider
            config_data['models'][model_name]['model_key'] = model_key
    
    # Write back
    try:
        from ruamel.yaml import YAML
        yaml_writer = YAML()
        yaml_writer.preserve_quotes = True
        yaml_writer.width = 4096
        yaml_writer.default_flow_style = False
        with open(config_file, 'w') as f:
            yaml_writer.dump(config_data, f)
    except ImportError:
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    
    # Summary
    total_models = len(config_data.get('models', {}))
    if added_count > 0:
        print(f"  Added {added_count} new model(s) to config")
    print(f"✓ Initialized model_config.yml with {total_models} model(s)")
except Exception as e:
    print(f"ERROR: Failed to initialize model_config.yml: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
PYTHON_EOF
    
    if [ $? -eq 0 ]; then
        return 0
    else
        return 1
    fi
}

# Main execution
main() {
    # Parse options
    local interactive=true
    local force=false
    
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --non-interactive)
        interactive=false
        shift
                ;;
            --force)
                force=true
                shift
                ;;
            --debug)
                DEBUG=true
                shift
                ;;
            --help|-h)
                echo "Usage: $0 [OPTIONS] [MODEL_NAME]"
                echo ""
                echo "Options:"
                echo "  --non-interactive    Run without prompts (auto-update)"
                echo "  --force              Force re-analysis even if model already configured"
                echo "  --debug              Enable debug output"
                echo "  --help, -h           Show this help message"
                echo ""
                echo "Examples:"
                echo "  $0                                     # Analyze all models (interactive)"
                echo "  $0 Qwen/Qwen3-30B-Instruct            # Analyze specific model"
                echo "  $0 --force Qwen/Qwen3-30B-Instruct    # Force re-analysis"
                echo "  $0 --non-interactive                   # Auto-analyze all"
                echo "  $0 --debug Qwen/Qwen3-30B-Instruct    # Debug mode"
                exit 0
                ;;
            -*)
                error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
            *)
                break
                ;;
        esac
    done
    
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
        # Capture only stdout (config line), stderr (info messages) goes to terminal
        # Command substitution only captures stdout by default, so stderr from info() won't be captured
        # Capture stdout only (config line), stderr (info messages) goes to terminal
        # Command substitution only captures stdout by default, stderr goes to terminal
        local config_line=$(analyze_model "$model_name" "$model_path")
        
        # Filter out any non-config lines (safety check - should not be needed)
        config_line=$(echo "$config_line" | grep -E '^[0-9]+\|[^|]+\|[^|]+\|[0-9]+\|' | head -1)
        
        if [ -n "$config_line" ]; then
            # Parse config line: params|precision|quantization|gpu_size|notes
            IFS='|' read -r params precision quantization gpu_size notes <<< "$config_line"
            
            if [ "$interactive" = true ]; then
                echo ""
                echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                info "Model Configuration Summary"
                echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                echo "  Model: $model_name"
                echo "  Parameters: ${params}B"
                echo "  Precision: ${precision^^}"
                echo "  Quantization: ${quantization}"
                echo "  Estimated GPU Memory: ${gpu_size}GB"
                echo ""
                echo "  Full Notes: $notes"
                echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                echo ""
                read -p "Update model_config.yml? (Y/n): " -n 1 -r
                echo ""
                if [[ ! $REPLY =~ ^[Nn]$ ]]; then
                    update_model_config "$model_name" "$params" "$precision" "$quantization" "$gpu_size" "0" "$notes"
                fi
            else
                # Non-interactive: auto-update
                update_model_config "$model_name" "$params" "$precision" "$quantization" "$gpu_size" "0" "$notes" 2>/dev/null || true
            fi
        else
            if [ "$interactive" = true ]; then
                error "Failed to analyze model"
            fi
        fi
    else
        # Initialize model_config.yml with all models from registry
        info "Initializing model_config.yml with models from registry..."
        initialize_model_config
        
        # Analyze all models
        info "Analyzing all models in cache..."
        echo ""
        
        local updated=0
        for model_dir in "${MODELS_DIR}"/models--*; do
            if [ ! -d "$model_dir" ]; then
                continue
            fi
            
            local model_name=$(basename "$model_dir" | sed 's/models--//g' | sed 's/--/\//g')
            # Capture stdout only (config line), stderr (info messages) goes to terminal
            local config_line=$(analyze_model "$model_name" "$model_dir")
            
            # Filter out any non-config lines (safety check - should not be needed)
            config_line=$(echo "$config_line" | grep -E '^[0-9]+\|[^|]+\|[^|]+\|[0-9]+\|' | head -1)
            
            if [ -n "$config_line" ]; then
                # Parse config line: params|precision|quantization|gpu_size|notes
                IFS='|' read -r params precision quantization gpu_size notes <<< "$config_line"
                
                # Calculate disk size
                local disk_size_gb=0
                local snapshot_dir=$(find "$model_dir/snapshots" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)
                if [ -n "$snapshot_dir" ]; then
                    local size_bytes=$(du -sbL "$snapshot_dir" 2>/dev/null | awk '{print $1}')
                    if [ -n "$size_bytes" ] && [[ "$size_bytes" =~ ^[0-9]+$ ]] && [ "$size_bytes" -gt 0 ]; then
                        disk_size_gb=$(echo "scale=2; $size_bytes / 1024 / 1024 / 1024" | bc -l)
                    fi
                fi
                
                echo "  $model_name: $config_line"
                update_model_config "$model_name" "$params" "$precision" "$quantization" "$gpu_size" "$disk_size_gb" "$notes"
                updated=$((updated + 1))
            fi
        done
        
        echo ""
        success "Updated $updated model configurations"
    fi
    
    echo ""
    info "Next steps:"
    echo "  1. Review updated model_config.yml: $MODEL_CONFIG"
    echo "  2. Configure GPU/port assignments:"
    echo "     bash ${SCRIPT_DIR}/configure-vllm-model-routing.sh --interactive"
    echo ""
}

main "$@"

