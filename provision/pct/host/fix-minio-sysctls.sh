#!/bin/bash
#
# Fix MinIO Container Sysctl Support
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Add sysctl support to existing files-lxc container for Docker-in-LXC
#
# USAGE:
#   bash fix-minio-sysctls.sh [production|test]
#
# WHAT IT DOES:
#   1. Stops the files-lxc container
#   2. Adds lxc.mount.auto configuration for proc:rw sys:rw
#   3. Allows all devices and drops no capabilities
#   4. Restarts the container
#
# WHY:
#   - MinIO Docker container tries to set net.ipv4.ip_unprivileged_port_start
#   - Even privileged LXC containers need explicit sysctl mounting
#   - Required for Docker-in-LXC to modify sysctls
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Determine environment
MODE="${1:-production}"

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source variables
if [[ "$MODE" == "test" ]]; then
    source "$PCT_DIR/test-vars.env"
    CT_FILES="${CT_FILES_TEST}"
    PREFIX="TEST-"
else
    source "$PCT_DIR/vars.env"
    PREFIX=""
fi

echo "=========================================="
echo "Fix MinIO Container Sysctl Support"
echo "=========================================="
log_info "Environment: ${MODE}"
log_info "Container: ${PREFIX}files-lxc (${CT_FILES})"
echo ""

# Check if container exists
if ! pct status "$CT_FILES" &>/dev/null; then
    log_error "Container ${CT_FILES} does not exist"
    exit 1
fi

log_info "Step 1: Checking current configuration..."
CONF_FILE="/etc/pve/lxc/${CT_FILES}.conf"

if grep -q "lxc.mount.auto: proc:rw sys:rw" "$CONF_FILE"; then
    log_success "Sysctl support already configured"
    echo ""
    log_info "Current configuration:"
    grep "lxc.mount.auto" "$CONF_FILE" || echo "  No lxc.mount.auto found"
    echo ""
    log_info "No changes needed. Exiting."
    exit 0
fi

log_info "Step 2: Stopping container..."
pct stop "$CT_FILES"
sleep 2
log_success "Container stopped"
echo ""

log_info "Step 3: Backing up configuration..."
cp "$CONF_FILE" "${CONF_FILE}.backup.$(date +%Y%m%d-%H%M%S)"
log_success "Backup created"
echo ""

log_info "Step 4: Adding sysctl support..."
cat >> "$CONF_FILE" << 'EOF'
# Docker sysctl support - Added by fix-minio-sysctls.sh
lxc.cgroup2.devices.allow: a
lxc.cap.drop:
lxc.mount.auto: proc:rw sys:rw
EOF
log_success "Configuration updated"
echo ""

log_info "Step 5: Starting container..."
pct start "$CT_FILES"
sleep 3
log_success "Container started"
echo ""

log_info "Step 6: Verifying configuration..."
if pct status "$CT_FILES" | grep -q "running"; then
    log_success "Container is running"
else
    log_error "Container failed to start"
    exit 1
fi
echo ""

echo "=========================================="
log_success "MinIO container sysctl support configured!"
echo "=========================================="
echo ""
log_info "Next steps:"
echo ""
log_info "1. Redeploy MinIO with Ansible:"
log_info "   cd provision/ansible"
if [[ "$MODE" == "test" ]]; then
    log_info "   ansible-playbook -i inventory/test/hosts.yml site.yml --tags minio --limit TEST-files-lxc"
else
    log_info "   ansible-playbook -i inventory/production/hosts.yml site.yml --tags minio --limit files-lxc"
fi
echo ""
log_info "2. MinIO should now start without sysctl permission errors"
echo ""

