#!/usr/bin/env bash
# =============================================================================
# Detect System Architecture and RAM
# =============================================================================
#
# Detects system architecture and available RAM, determines the optimal
# model tier for the demo.
#
# Usage:
#   source detect-system.sh && echo $DEMO_TIER
#   ./detect-system.sh  # outputs key=value pairs
#
# Exports:
#   DEMO_BACKEND - "mlx" for Apple Silicon, "vllm" for x86/Linux
#   DEMO_TIER - one of: minimal, standard, enhanced, professional, enterprise, ultra
#   DEMO_RAM_GB - detected RAM in GB
#
# =============================================================================

set -euo pipefail

# Detect architecture
ARCH=$(uname -m)
OS=$(uname -s)

if [[ "$OS" == "Darwin" && ("$ARCH" == "arm64" || "$ARCH" == "aarch64") ]]; then
    DEMO_BACKEND="mlx"
else
    DEMO_BACKEND="vllm"
fi

# Detect RAM (in GB)
if [[ "$OS" == "Darwin" ]]; then
    # macOS: sysctl returns bytes
    RAM_BYTES=$(sysctl -n hw.memsize)
    RAM_GB=$((RAM_BYTES / 1024 / 1024 / 1024))
else
    # Linux: /proc/meminfo returns kB
    RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    RAM_GB=$((RAM_KB / 1024 / 1024))
fi

# Determine tier based on RAM
if [[ $RAM_GB -ge 256 ]]; then
    DEMO_TIER="ultra"
elif [[ $RAM_GB -ge 128 ]]; then
    DEMO_TIER="enterprise"
elif [[ $RAM_GB -ge 96 ]]; then
    DEMO_TIER="professional"
elif [[ $RAM_GB -ge 48 ]]; then
    DEMO_TIER="enhanced"
elif [[ $RAM_GB -ge 24 ]]; then
    DEMO_TIER="standard"
else
    DEMO_TIER="minimal"
fi

# Export for use by other scripts
export DEMO_BACKEND
export DEMO_TIER
export DEMO_RAM_GB=$RAM_GB

# If called directly (not sourced), output info
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "backend=$DEMO_BACKEND"
    echo "tier=$DEMO_TIER"
    echo "ram_gb=$RAM_GB"
fi
