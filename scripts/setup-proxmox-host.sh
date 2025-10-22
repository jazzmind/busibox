#!/usr/bin/env bash
#
# Proxmox Host Setup Script
#
# This script prepares a Proxmox host for running Busibox tests
# Run this ONCE on the Proxmox host before running tests
#

set -euo pipefail

echo "=========================================="
echo "Busibox Proxmox Host Setup"
echo "=========================================="
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "❌ This script must be run as root"
   exit 1
fi

# Check if running on Proxmox
if ! command -v pct &> /dev/null; then
    echo "❌ This script must run on a Proxmox host"
    exit 1
fi

echo "✓ Running on Proxmox host"
echo ""

# 1. Install Ansible
echo "=========================================="
echo "Step 1: Installing Ansible"
echo "=========================================="
if command -v ansible &> /dev/null; then
    echo "✓ Ansible already installed: $(ansible --version | head -1)"
else
    echo "Installing Ansible..."
    apt update
    apt install -y ansible
    echo "✓ Ansible installed: $(ansible --version | head -1)"
fi
echo ""

# 2. Install other dependencies
echo "=========================================="
echo "Step 2: Installing Dependencies"
echo "=========================================="
echo "Installing: curl, git, jq, psql, python3-pip..."
apt install -y curl git jq postgresql-client python3-pip
echo "✓ Dependencies installed"
echo ""

# 3. Update template list
echo "=========================================="
echo "Step 3: Updating Template List"
echo "=========================================="
pveam update
echo "✓ Template list updated"
echo ""

# 4. Check for Debian template
echo "=========================================="
echo "Step 4: Checking for LXC Template"
echo "=========================================="
TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
if [[ -f "/var/lib/vz/template/cache/${TEMPLATE}" ]]; then
    echo "✓ Template already downloaded: ${TEMPLATE}"
else
    echo "Downloading Debian 12 template..."
    pveam download local "${TEMPLATE}"
    echo "✓ Template downloaded"
fi
echo ""

# 5. Generate SSH key if not exists
echo "=========================================="
echo "Step 5: Checking SSH Key"
echo "=========================================="
if [[ -f "/root/.ssh/id_rsa.pub" ]]; then
    echo "✓ SSH key already exists"
else
    echo "Generating SSH key..."
    ssh-keygen -t rsa -b 4096 -f /root/.ssh/id_rsa -N ""
    echo "✓ SSH key generated"
fi
echo ""

# 6. Check and install NVIDIA drivers
echo "=========================================="
echo "Step 6: Checking NVIDIA Drivers"
echo "=========================================="

if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    DRIVER_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
    CUDA_VERSION=$(nvidia-smi | grep "CUDA Version" | awk '{print $9}')
    echo "✓ NVIDIA drivers already installed"
    echo "  Driver version: ${DRIVER_VERSION}"
    echo "  CUDA version: ${CUDA_VERSION}"
    
    # List GPUs
    echo ""
    echo "Available GPUs:"
    nvidia-smi -L
else
    echo "⚠ NVIDIA drivers not found or not working"
    echo ""
    read -p "Install latest NVIDIA drivers? (y/N): " -n 1 -r
    echo ""
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Installing latest NVIDIA drivers from NVIDIA repository..."
        
        # Detect Debian version
        DEBIAN_VERSION=$(cat /etc/debian_version | cut -d. -f1)
        if [[ "$DEBIAN_VERSION" == "13" ]] || [[ "$DEBIAN_VERSION" == "12" ]]; then
            DEBIAN_CODENAME="debian12"
            echo "  Detected Debian ${DEBIAN_VERSION} - using debian12 NVIDIA repository"
        else
            echo "  ❌ Unsupported Debian version: $DEBIAN_VERSION"
            exit 1
        fi
        
        # Clean up any existing installations
        echo "  Cleaning up old NVIDIA installations..."
        rm -rf /etc/apt/sources.list.d/cuda* /etc/apt/sources.list.d/nvidia*
        rm -rf /usr/share/keyrings/cuda* /usr/share/keyrings/nvidia*
        apt-get purge -y 'nvidia-*' 'cuda-*' 'libnvidia-*' 'libcuda*' 2>/dev/null || true
        apt-get autoremove -y
        apt-get clean
        
        # Install CUDA keyring
        echo "  Installing NVIDIA CUDA repository..."
        cd /tmp
        wget -q https://developer.download.nvidia.com/compute/cuda/repos/${DEBIAN_CODENAME}/x86_64/cuda-keyring_1.1-1_all.deb
        dpkg -i cuda-keyring_1.1-1_all.deb
        rm cuda-keyring_1.1-1_all.deb
        cd -
        
        # Update and install
        echo "  Installing NVIDIA drivers and CUDA toolkit..."
        apt-get update
        apt-get install -y cuda-drivers cuda-toolkit
        
        echo ""
        echo "✓ NVIDIA drivers installed!"
        echo ""
        echo "⚠ ⚠ ⚠  REBOOT REQUIRED  ⚠ ⚠ ⚠"
        echo ""
        echo "After reboot:"
        echo "  1. Run: nvidia-smi"
        echo "  2. Verify GPUs are detected"
        echo "  3. Re-run this script to continue setup"
        echo ""
        read -p "Reboot now? (y/N): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            reboot
        else
            exit 0
        fi
    else
        echo "⚠ Skipping NVIDIA driver installation"
        echo "  Note: GPU passthrough to LXC containers requires NVIDIA drivers on host"
    fi
fi
echo ""

# 7. Summary
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Your Proxmox host is ready for Busibox deployment."
echo ""
echo "Next steps:"
echo "  1. Review configuration: vim provision/pct/test-vars.env"
echo "  2. Run tests: bash test-infrastructure.sh full"
echo "  3. Or provision production: cd provision/pct && bash create_lxc_base.sh"
echo ""
echo "Available templates:"
ls -1 /var/lib/vz/template/cache/*.tar.* 2>/dev/null || echo "  (none found)"
echo ""

