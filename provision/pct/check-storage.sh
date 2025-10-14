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

# Show storage that supports containers (dir and zfspool types)
pvesm status | awk 'NR>1 && ($2 == "dir" || $2 == "zfspool" || $2 == "lvmthin") {print $1 " (" $2 ")"}'

echo ""
echo "=========================================="
echo "Recommended configuration:"
echo "=========================================="
echo ""

# Determine best storage
if pvesm status | grep -q "local-zfs.*zfspool"; then
  echo "✓ Recommended: STORAGE=local-zfs"
  echo "  (ZFS detected - best for containers)"
elif pvesm status | grep -q "local-lvm.*lvmthin"; then
  echo "✓ Recommended: STORAGE=local-lvm"
  echo "  (LVM thin detected)"
elif pvesm status | grep -q "local.*dir"; then
  echo "✓ Recommended: STORAGE=local"
  echo "  (Directory storage detected)"
else
  echo "⚠  No standard storage found"
  echo "  Choose from the list above"
fi

echo ""
echo "Edit provision/pct/vars.env or provision/pct/test-vars.env and set:"
echo "  STORAGE=<storage-name>"
echo ""

