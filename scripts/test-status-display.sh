#!/usr/bin/env bash
#
# Test script for status display system
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/services.sh"
source "${REPO_ROOT}/scripts/lib/status.sh"

echo "========================================="
echo "Status Display System Test"
echo "========================================="
echo ""

# Test 1: Service registry
echo "Test 1: Service Registry"
echo "-------------------------"
echo "AuthZ container ID (prod): $(get_service_container_id authz production)"
echo "AuthZ health URL (staging): $(get_service_health_url authz staging proxmox)"
echo "All services: $ALL_SERVICES"
echo ""

# Test 2: Initialize cache
echo "Test 2: Initialize Cache"
echo "-------------------------"
init_cache_dir
echo "Cache directory: $CACHE_DIR"
ls -la "$CACHE_DIR" 2>/dev/null || echo "(empty)"
echo ""

# Test 3: Kick off background refresh
echo "Test 3: Background Refresh"
echo "-------------------------"
echo "Launching background refresh for local Docker environment..."
refresh_all_services_async "local" "docker" &
echo "Background jobs launched (PID: $!)"
echo ""

# Test 4: Wait a moment and check cache
echo "Test 4: Wait for Cache Updates"
echo "-------------------------"
echo "Waiting 3 seconds for background checks..."
sleep 3
echo ""
echo "Cache files created:"
ls -lh "$CACHE_DIR" 2>/dev/null || echo "(none yet)"
echo ""

# Test 5: Render status dashboard
echo "Test 5: Render Status Dashboard"
echo "========================================="
render_status_dashboard "local" "docker"
echo ""

echo "========================================="
echo "Test Complete"
echo "========================================="
