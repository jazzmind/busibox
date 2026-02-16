#!/bin/bash
# Clear Failed Jobs from Redis Queue
# Run from: Admin workstation
# Usage: bash scripts/clear-failed-jobs.sh [--all|--failed-only]

set -e

DATA_IP="10.96.200.206"

echo "================================"
echo "Clear Failed Data Jobs"
echo "================================"
echo ""

# Parse arguments
CLEAR_ALL=false
if [[ "$1" == "--all" ]]; then
  CLEAR_ALL=true
  echo "⚠️  WARNING: This will clear ALL jobs from the queue!"
  echo ""
  read -p "Are you sure? (yes/no): " confirm
  if [[ "$confirm" != "yes" ]]; then
    echo "Aborted."
    exit 0
  fi
elif [[ "$1" == "--failed-only" ]] || [[ -z "$1" ]]; then
  echo "Clearing only failed/stuck jobs (safe mode)"
else
  echo "Usage: $0 [--all|--failed-only]"
  echo "  --all          Clear ALL jobs from queue (dangerous!)"
  echo "  --failed-only  Clear only failed jobs (default, safe)"
  exit 1
fi

echo ""
echo "=== Current Queue Status ==="
ssh root@${DATA_IP} << 'EOF'
echo "Stream length:"
redis-cli XLEN jobs:data

echo ""
echo "Consumer group info:"
redis-cli XINFO GROUPS jobs:data 2>/dev/null || echo "No consumer group"

echo ""
echo "Pending messages:"
redis-cli XPENDING jobs:data workers - + 10 2>/dev/null || echo "No pending messages"
EOF

echo ""
echo "=== Clearing Jobs ==="

if [[ "$CLEAR_ALL" == "true" ]]; then
  # Nuclear option - delete entire stream
  ssh root@${DATA_IP} << 'EOF'
echo "Deleting entire stream..."
redis-cli DEL jobs:data
echo "Recreating consumer group..."
redis-cli XGROUP CREATE jobs:data workers 0 MKSTREAM
echo "✅ All jobs cleared and stream reset"
EOF
else
  # Safe option - acknowledge pending messages
  ssh root@${DATA_IP} << 'EOF'
echo "Getting pending messages..."
PENDING=$(redis-cli XPENDING jobs:data workers - + 100)

if [[ -z "$PENDING" ]] || [[ "$PENDING" == *"no pending"* ]]; then
  echo "No pending messages to clear"
else
  echo "Acknowledging pending messages..."
  # Extract message IDs and acknowledge them
  redis-cli XPENDING jobs:data workers - + 100 | while read -r line; do
    if [[ $line =~ ^[0-9]+-[0-9]+$ ]]; then
      echo "Acknowledging message: $line"
      redis-cli XACK jobs:data workers "$line"
    fi
  done
  echo "✅ Pending messages acknowledged"
fi

# Also trim old processed messages
echo ""
echo "Trimming old messages (keeping last 100)..."
redis-cli XTRIM jobs:data MAXLEN ~ 100
echo "✅ Old messages trimmed"
EOF
fi

echo ""
echo "=== Updated Queue Status ==="
ssh root@${DATA_IP} << 'EOF'
echo "Stream length:"
redis-cli XLEN jobs:data

echo ""
echo "Pending messages:"
redis-cli XPENDING jobs:data workers - + 10 2>/dev/null || echo "No pending messages"
EOF

echo ""
echo "=== Restart Worker ==="
ssh root@${DATA_IP} "systemctl restart data-worker"
echo "✅ Worker restarted"

echo ""
echo "================================"
echo "Queue cleanup complete!"
echo ""
echo "Next steps:"
echo "1. Check worker logs: ssh root@${DATA_IP} 'journalctl -u data-worker -f'"
echo "2. Re-upload failed documents from Busibox Portal"
echo "3. Or manually reset document status in database if needed"
echo "================================"

