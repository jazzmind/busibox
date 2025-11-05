#!/usr/bin/env bash
#
# Destroy Test Containers
#
# This script safely destroys all TEST containers created by create_lxc_base.sh test
# It will NOT touch production containers (IDs 201-207)
#
# Usage:
#   bash destroy_test.sh           # Destroy all test containers
#   bash destroy_test.sh --force   # Skip confirmation prompt

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
source "${SCRIPT_DIR}/test-vars.env"

# Parse arguments
FORCE=false
if [[ "${1:-}" == "--force" ]]; then
  FORCE=true
fi

# Array of test container IDs
TEST_CONTAINERS=(
  "$CT_PROXY_TEST"
  "$CT_APPS_TEST"
  "$CT_AGENT_TEST"
  "$CT_PG_TEST"
  "$CT_MILVUS_TEST"
  "$CT_FILES_TEST"
  "$CT_INGEST_TEST"
)

# Safety check - ensure we're only destroying test containers (ID >= 300)
for CTID in "${TEST_CONTAINERS[@]}"; do
  if [[ "$CTID" -lt 300 ]]; then
    echo "ERROR: Container ID $CTID is below 300 (production range)!"
    echo "This script only destroys TEST containers (IDs 300+)"
    exit 1
  fi
done

# Confirmation prompt (unless --force)
if [[ "$FORCE" != "true" ]]; then
  echo "=========================================="
  echo "Test Container Destruction"
  echo "=========================================="
  echo ""
  echo "This will DESTROY the following TEST containers:"
  for CTID in "${TEST_CONTAINERS[@]}"; do
    if pct status "$CTID" &>/dev/null; then
      NAME=$(pct config "$CTID" | grep "^hostname:" | awk '{print $2}')
      echo "  - $NAME (ID: $CTID)"
    fi
  done
  echo ""
  echo "WARNING: All data in these containers will be PERMANENTLY DELETED!"
  echo ""
  read -p "Are you sure you want to continue? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi
fi

echo ""
echo "Destroying test containers..."
echo ""

# Destroy each container
DESTROYED_COUNT=0
SKIPPED_COUNT=0

# Temporarily disable pipefail to continue through errors
set +e

for CTID in "${TEST_CONTAINERS[@]}"; do
  if pct status "$CTID" &>/dev/null; then
    NAME=$(pct config "$CTID" 2>/dev/null | grep "^hostname:" | awk '{print $2}' || echo "unknown")
    echo "==> Destroying $NAME ($CTID)"
    
    # Stop container if running
    if pct status "$CTID" 2>/dev/null | grep -q "running"; then
      echo "    Stopping container..."
      pct stop "$CTID" 2>/dev/null || {
        echo "    Warning: Failed to stop gracefully, forcing..."
        pct stop "$CTID" --force 2>/dev/null || true
      }
      sleep 2
    fi
    
    # Destroy container
    echo "    Deleting container..."
    if pct destroy "$CTID" --purge 2>/dev/null; then
      echo "    ✓ Destroyed $NAME ($CTID)"
      ((DESTROYED_COUNT++))
    else
      echo "    ✗ Failed to destroy $NAME ($CTID)"
      # Try force destroy
      echo "    Attempting force destroy..."
      if pct destroy "$CTID" --purge --force 2>/dev/null; then
        echo "    ✓ Force destroyed $NAME ($CTID)"
        ((DESTROYED_COUNT++))
      else
        echo "    ✗ Could not destroy container $CTID"
      fi
    fi
  else
    echo "==> Container $CTID does not exist, skipping"
    ((SKIPPED_COUNT++))
  fi
done

# Re-enable error handling
set -e

echo ""
echo "=========================================="
echo "Test Container Destruction Complete"
echo "=========================================="
echo "Destroyed: $DESTROYED_COUNT containers"
echo "Skipped:   $SKIPPED_COUNT containers (didn't exist)"
echo "=========================================="

