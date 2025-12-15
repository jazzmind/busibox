#!/bin/bash
#
# Fix Redis Consumer Group
#
# Manually creates the Redis consumer group for the ingestion worker.
# This is a workaround for when the worker fails to create it on startup.
#
# Usage:
#   bash scripts/fix-redis-consumer-group.sh [test|production]
#

set -euo pipefail

ENVIRONMENT="${1:-test}"

if [ "$ENVIRONMENT" = "test" ]; then
    INGEST_IP="10.96.201.206"
else
    INGEST_IP="10.96.200.206"
fi

echo "=== Fixing Redis Consumer Group ($ENVIRONMENT) ==="
echo ""

echo "1. Checking current state..."
echo "   Stream length:"
ssh root@$INGEST_IP 'redis-cli XLEN ingestion:jobs' || echo "   Stream doesn't exist yet"

echo ""
echo "   Consumer groups:"
ssh root@$INGEST_IP 'redis-cli XINFO GROUPS ingestion:jobs' 2>/dev/null || echo "   No consumer groups exist"

echo ""
echo "2. Creating consumer group..."
if ssh root@$INGEST_IP 'redis-cli XGROUP CREATE ingestion:jobs workers 0 MKSTREAM' 2>&1 | grep -q "BUSYGROUP"; then
    echo "   ✓ Consumer group already exists"
else
    echo "   ✓ Consumer group created"
fi

echo ""
echo "3. Verifying..."
ssh root@$INGEST_IP 'redis-cli XINFO GROUPS ingestion:jobs'

echo ""
echo "4. Restarting worker to pick up changes..."
ssh root@$INGEST_IP 'systemctl restart ingest-worker'
sleep 2

echo ""
echo "5. Checking worker status..."
if ssh root@$INGEST_IP 'systemctl is-active ingest-worker' | grep -q "active"; then
    echo "   ✓ Worker is running"
else
    echo "   ✗ Worker is NOT running"
    echo "   Check logs: ssh root@$INGEST_IP 'journalctl -u ingest-worker -n 50'"
fi

echo ""
echo "=== Fix Complete ==="
echo ""
echo "Now try uploading a file again. The worker should process it."







