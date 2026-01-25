#!/usr/bin/env bash
#
# Detect available LLM backend
#
# Returns: "mlx", "vllm", or "cloud"
#
# - mlx:   Apple Silicon detected (runs natively on host)
# - vllm:  NVIDIA GPU detected (runs in container)
# - cloud: No local AI hardware, use AWS Bedrock
#

set -euo pipefail

detect_backend() {
    local os arch
    os=$(uname -s)
    arch=$(uname -m)
    
    # Check for Apple Silicon
    if [[ "$os" == "Darwin" && ("$arch" == "arm64" || "$arch" == "aarch64") ]]; then
        echo "mlx"
        return 0
    fi
    
    # Check for NVIDIA GPU
    if command -v nvidia-smi &>/dev/null; then
        local gpu_count
        gpu_count=$(nvidia-smi -L 2>/dev/null | wc -l || echo "0")
        if [[ $gpu_count -gt 0 ]]; then
            echo "vllm"
            return 0
        fi
    fi
    
    # No local AI hardware available
    echo "cloud"
}

# If run directly, output the result
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    detect_backend
fi
