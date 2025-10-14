#!/usr/bin/env bash
#
# Check available Proxmox storage for LXC containers
#

echo "=========================================="
echo "Available Proxmox Storage"
echo "=========================================="
echo ""

# List all storage
pvesm status

echo ""
echo "=========================================="
echo "Storage suitable for LXC containers:"
echo "=========================================="
echo ""

# Show storage that supports containers
pvesm status | awk 'NR>1 && ($6 == "yes" || $6 ~ /rootdir/) {print $1}'

echo ""
echo "=========================================="
echo "Recommended configuration:"
echo "=========================================="
echo ""
echo "Edit provision/pct/vars.env and set:"
echo "  STORAGE=<name from above>"
echo ""
echo "Common options:"
echo "  - local (directory storage)"
echo "  - local-lvm (LVM thin)"
echo "  - local-zfs (ZFS)"
echo "  - dir (custom directory)"
echo ""

