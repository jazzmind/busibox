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

# 6. Summary
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

