#!/usr/bin/env bash
# Fix GPU passthrough configuration by completely removing old config and adding fresh
set -euo pipefail

OLLAMA_CTID="${1:-308}"
VLLM_CTID="${2:-309}"

echo "=========================================="
echo "Fixing GPU Passthrough Configuration"
echo "=========================================="
echo ""

# Function to fix a container's GPU config
fix_container_gpu() {
    local ctid=$1
    local gpu_num=$2
    local conf="/etc/pve/lxc/${ctid}.conf"
    
    echo "==> Fixing container ${ctid} (GPU ${gpu_num})..."
    
    # Stop container if running
    if pct status "${ctid}" 2>/dev/null | grep -q "running"; then
        echo "  Stopping container..."
        pct stop "${ctid}" || true
        sleep 3
    fi
    
    # Remove ALL GPU-related lines from config
    echo "  Removing old GPU configuration..."
    sed -i '/GPU Passthrough/d' "${conf}"
    sed -i '/lxc.cgroup2.devices.allow.*195/d' "${conf}"
    sed -i '/lxc.cgroup2.devices.allow.*234/d' "${conf}"
    sed -i '/lxc.cgroup2.devices.allow.*508/d' "${conf}"
    sed -i '/lxc.mount.entry.*nvidia/d' "${conf}"
    
    # Add fresh GPU configuration
    echo "  Adding fresh GPU configuration..."
    cat >> "${conf}" << EOF
# GPU Passthrough: NVIDIA GPU ${gpu_num}
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
lxc.mount.entry: /dev/nvidia${gpu_num} dev/nvidia${gpu_num} none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps dev/nvidia-caps none bind,optional,create=dir
EOF
    
    # Start container using systemctl to avoid the arch bug
    echo "  Starting container using systemctl..."
    systemctl start pve-container@${ctid} || {
        echo "  ⚠ systemctl failed, trying lxc-start..."
        lxc-start -n ${ctid} || echo "  ⚠ lxc-start also failed"
    }
    
    sleep 3
    
    # Verify it's running
    if pct status "${ctid}" 2>/dev/null | grep -q "running"; then
        echo "  ✓ Container ${ctid} is running"
    else
        echo "  ⚠ Container ${ctid} may not be running - check manually"
    fi
    
    echo ""
}

# Fix both containers
fix_container_gpu "${OLLAMA_CTID}" "0"
fix_container_gpu "${VLLM_CTID}" "1"

echo "=========================================="
echo "GPU Passthrough Fixed!"
echo "=========================================="
echo ""
echo "Verifying GPU devices in containers..."
echo ""

# Verify GPU devices
for ctid in "${OLLAMA_CTID}" "${VLLM_CTID}"; do
    if pct status "${ctid}" 2>/dev/null | grep -q "running"; then
        echo "Container ${ctid} GPU devices:"
        pct exec "${ctid}" -- ls -la /dev/nvidia* 2>/dev/null || echo "  ⚠ Could not list devices"
        echo ""
    fi
done

echo "✓ Done! Now run your Ansible playbook to test PyTorch."
echo ""

