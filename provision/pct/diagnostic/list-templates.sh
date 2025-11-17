#!/usr/bin/env bash
#
# List available LXC templates on Proxmox
#

echo "=========================================="
echo "Available LXC Templates"
echo "=========================================="
echo ""

# List templates from pveam
echo "Templates available for download:"
pveam available | grep -E "(ubuntu|debian)" | head -20

echo ""
echo "=========================================="
echo "Already downloaded templates:"
echo "=========================================="
echo ""

# List what's actually on disk
ls -lh /var/lib/vz/template/cache/ 2>/dev/null || echo "No templates directory found"

echo ""
echo "=========================================="
echo "To download Ubuntu 22.04:"
echo "=========================================="
echo ""
echo "  pveam update"
echo "  pveam download local ubuntu-22.04-standard_22.04-1_amd64.tar.zst"
echo ""
echo "Or for Debian 12:"
echo ""
echo "  pveam download local debian-12-standard_12.7-1_amd64.tar.zst"
echo ""

