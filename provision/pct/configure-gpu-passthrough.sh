#!/usr/bin/env bash
# Configure GPU passthrough for LXC containers
# Usage: bash configure-gpu-passthrough.sh <ollama_ctid> <vllm_ctid>
set -euo pipefail

OLLAMA_CTID="${1:-308}"  # Default to test container
VLLM_CTID="${2:-309}"

echo "=========================================="
echo "GPU Passthrough Configuration"
echo "=========================================="
echo "Ollama container: ${OLLAMA_CTID} (GPU 0)"
echo "vLLM container: ${VLLM_CTID} (GPU 1)"
echo ""

# Verify containers exist
if ! pct status "${OLLAMA_CTID}" &>/dev/null; then
    echo "ERROR: Ollama container ${OLLAMA_CTID} not found"
    exit 1
fi

if ! pct status "${VLLM_CTID}" &>/dev/null; then
    echo "ERROR: vLLM container ${VLLM_CTID} not found"
    exit 1
fi

# Verify GPUs exist on host
echo "==> Checking host GPU availability..."
if ! command -v nvidia-smi &>/dev/null; then
    echo "ERROR: nvidia-smi not found. Install NVIDIA drivers on the host first."
    exit 1
fi

nvidia-smi
echo ""

# Track if any changes were made
CHANGES_MADE=false

# Check if already configured
if grep -q "GPU Passthrough" "/etc/pve/lxc/${OLLAMA_CTID}.conf" 2>/dev/null; then
    echo "✓ Ollama container ${OLLAMA_CTID} already has GPU passthrough configured"
else
    echo "==> Configuring Ollama container ${OLLAMA_CTID} for GPU 0..."
    cat >> "/etc/pve/lxc/${OLLAMA_CTID}.conf" << 'OLLAMA_EOF'
# GPU Passthrough: NVIDIA GPU 0
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps dev/nvidia-caps none bind,optional,create=dir
OLLAMA_EOF
    echo "    ✓ Ollama GPU 0 configuration added"
    CHANGES_MADE=true
fi

if grep -q "GPU Passthrough" "/etc/pve/lxc/${VLLM_CTID}.conf" 2>/dev/null; then
    echo "✓ vLLM container ${VLLM_CTID} already has GPU passthrough configured"
else
    echo "==> Configuring vLLM container ${VLLM_CTID} for GPU 1..."
    cat >> "/etc/pve/lxc/${VLLM_CTID}.conf" << 'VLLM_EOF'
# GPU Passthrough: NVIDIA GPU 1
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
lxc.mount.entry: /dev/nvidia1 dev/nvidia1 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps dev/nvidia-caps none bind,optional,create=dir
VLLM_EOF
    echo "    ✓ vLLM GPU 1 configuration added"
    CHANGES_MADE=true
fi

# Only restart containers if changes were made
if [[ "$CHANGES_MADE" == "true" ]]; then
    echo ""
    echo "==> Restarting containers to apply GPU passthrough..."
    pct stop "${OLLAMA_CTID}" 2>/dev/null || true
    pct stop "${VLLM_CTID}" 2>/dev/null || true
    sleep 2
    pct start "${OLLAMA_CTID}"
    pct start "${VLLM_CTID}"
    sleep 5
else
    echo ""
    echo "==> No configuration changes needed - containers already have GPU passthrough"
fi

echo ""
echo "=========================================="
echo "GPU passthrough configured successfully!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Install NVIDIA drivers in each container:"
echo "   ssh root@<container-ip>"
echo "   apt update && apt install -y nvidia-driver-535 cuda-toolkit-12-1"
echo ""
echo "2. Verify GPU is visible:"
echo "   nvidia-smi"
echo ""
echo "3. Deploy LLM services with Ansible"
echo ""

