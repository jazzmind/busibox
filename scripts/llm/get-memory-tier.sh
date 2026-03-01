#!/usr/bin/env bash
#
# Get model tier based on available RAM/VRAM
#
# Returns: "minimal", "entry", "standard", "enhanced"
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

get_memory_tier() {
    local backend="${1:-}"
    local ram_gb=0
    local os arch
    
    os=$(uname -s)
    arch=$(uname -m)
    
    # Detect backend if not provided
    if [[ -z "$backend" ]]; then
        backend=$(bash "${SCRIPT_DIR}/detect-backend.sh")
    fi
    
    if [[ "$backend" == "mlx" ]]; then
        # Apple Silicon - use unified memory
        local ram_bytes
        ram_bytes=$(sysctl -n hw.memsize)
        ram_gb=$((ram_bytes / 1024 / 1024 / 1024))
    elif [[ "$backend" == "vllm" ]]; then
        # NVIDIA - use VRAM (sum of all GPUs)
        if command -v nvidia-smi &>/dev/null; then
            local vram_mb
            vram_mb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | awk '{s+=$1} END {print s}')
            ram_gb=$((vram_mb / 1024))
        fi
    else
        # Cloud - no local memory needed
        echo "cloud"
        return
    fi
    
    # Determine tier
    if [[ $ram_gb -ge 96 ]]; then
        echo "enhanced"
    elif [[ $ram_gb -ge 48 ]]; then
        echo "standard"
    elif [[ $ram_gb -ge 24 ]]; then
        echo "entry"
    else
        echo "minimal"
    fi
}

# If run directly, output the result
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    get_memory_tier "${1:-}"
fi
