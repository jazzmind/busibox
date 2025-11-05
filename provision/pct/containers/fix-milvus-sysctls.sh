#!/usr/bin/env bash
#
# Fix Milvus LXC for Docker Sysctls
#
# Description:
#   Adds required LXC configuration to allow Docker containers inside Milvus LXC
#   to set sysctls (specifically net.ipv4.ip_unprivileged_port_start).
#
# Execution Context: Proxmox VE Host (as root)
# Dependencies: pct
#
# Usage:
#   bash provision/pct/containers/fix-milvus-sysctls.sh [test|production]
#
# Notes:
#   - Container must be stopped before applying configuration
#   - This is required for Milvus Docker container to start properly in LXC

set -euo pipefail

# Determine mode from argument
MODE="${1:-production}"

# Get script directory and source dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source configuration
if [[ "$MODE" == "test" ]]; then
  echo "==> Fixing TEST Milvus container for sysctls"
  source "${PCT_DIR}/test-vars.env"
  CT_MILVUS="$CT_MILVUS_TEST"
  NAME="${TEST_PREFIX}milvus-lxc"
else
  echo "==> Fixing PRODUCTION Milvus container for sysctls"
  source "${PCT_DIR}/vars.env"
  CT_MILVUS="$CT_MILVUS"
  NAME="milvus-lxc"
fi

CONFIG_FILE="/etc/pve/lxc/${CT_MILVUS}.conf"

# Check if container exists
if ! pct status "$CT_MILVUS" &>/dev/null; then
  echo "ERROR: Container $NAME ($CT_MILVUS) does not exist"
  echo "Run: bash provision/pct/containers/create-data-services.sh $MODE"
  exit 1
fi

# Check if fix is already applied
if grep -q "# Docker sysctls support" "$CONFIG_FILE"; then
  echo "==> Fix already applied to $NAME ($CT_MILVUS)"
  echo "    Configuration includes Docker sysctls support"
  exit 0
fi

# Stop container if running
if pct status "$CT_MILVUS" | grep -q "running"; then
  echo "==> Stopping container $NAME ($CT_MILVUS)..."
  pct stop "$CT_MILVUS"
  sleep 3
fi

# Add sysctls configuration
echo "==> Adding Docker sysctls support to $NAME ($CT_MILVUS)..."

cat >> "$CONFIG_FILE" << 'EOF'
# Docker sysctls support
# Allow Docker containers inside this LXC to set network sysctls
lxc.apparmor.profile: unconfined
lxc.cgroup2.devices.allow: a
lxc.cap.drop:
lxc.mount.auto: proc:rw sys:rw
EOF

echo "    Added sysctls configuration"

# Start container
echo "==> Starting container $NAME ($CT_MILVUS)..."
pct start "$CT_MILVUS"
sleep 3

echo ""
echo "=========================================="
echo "Milvus container fixed successfully!"
echo "Mode: ${MODE}"
echo "Container: ${NAME} ($CT_MILVUS)"
echo ""
echo "Next steps:"
echo "  1. Re-run Ansible to deploy Milvus:"
echo "     cd provision/ansible"
echo "     ansible-playbook -i inventory/${MODE}/hosts.yml site.yml --tags milvus"
echo "=========================================="

