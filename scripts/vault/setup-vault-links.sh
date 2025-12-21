#!/bin/bash
# Setup vault symlinks for inventory variable loading
# Run this on the deployment server after creating vault.yml

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Setting up vault symlinks for inventory..."

# Check if vault exists
if [ ! -f "roles/secrets/vars/vault.yml" ]; then
    echo "ERROR: vault.yml not found at roles/secrets/vars/vault.yml"
    echo "Please create it from the template:"
    echo "  cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml"
    echo "  ansible-vault edit roles/secrets/vars/vault.yml"
    exit 1
fi

# Create group_vars/all directories
mkdir -p inventory/production/group_vars/all
mkdir -p inventory/test/group_vars/all

# Move main config files if they're in the wrong place
if [ -f "inventory/production/group_vars/all.yml" ]; then
    echo "Moving inventory/production/group_vars/all.yml to all/00-main.yml"
    mv inventory/production/group_vars/all.yml inventory/production/group_vars/all/00-main.yml
fi

if [ -f "inventory/test/group_vars/all.yml" ]; then
    echo "Moving inventory/test/group_vars/all.yml to all/00-main.yml"
    mv inventory/test/group_vars/all.yml inventory/test/group_vars/all/00-main.yml
fi

# Create symlinks
echo "Creating symlink: inventory/production/group_vars/all/vault.yml"
cd inventory/production/group_vars/all
ln -sf ../../../../roles/secrets/vars/vault.yml vault.yml || true
cd "$SCRIPT_DIR"

echo "Creating symlink: inventory/test/group_vars/all/vault.yml"
cd inventory/test/group_vars/all
ln -sf ../../../../roles/secrets/vars/vault.yml vault.yml || true
cd "$SCRIPT_DIR"

echo ""
echo "✓ Vault symlinks created successfully"
echo ""
echo "Verification:"
ls -la inventory/production/group_vars/all/vault.yml
ls -la inventory/test/group_vars/all/vault.yml
echo ""
echo "Test variable loading:"
echo "  ansible-inventory -i inventory/test --list | grep network_base_octets"
echo ""

