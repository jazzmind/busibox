#!/bin/bash
# Check Ingestion Service Status
# Run from: Admin workstation
# Usage: bash scripts/check/check-ingest-status.sh

set -e

INGEST_IP="10.96.200.206"
POSTGRES_IP="10.96.200.203"

echo "================================"
echo "Ingestion Service Status Check"
echo "================================"
echo ""

echo "=== 1. Service Status ==="
ssh root@${INGEST_IP} "systemctl status ingest-api ingest-worker redis-server --no-pager | grep -E 'Active:|Main PID:|Memory:'"
echo ""

echo "=== 2. Recent Worker Logs (last 20 lines) ==="
ssh root@${INGEST_IP} "journalctl -u ingest-worker -n 20 --no-pager | tail -15"
echo ""

echo "=== 3. Redis Queue Status ==="
ssh root@${INGEST_IP} << 'EOF'
echo "Stream length:"
redis-cli XLEN jobs:ingestion

echo ""
echo "Consumer groups:"
redis-cli XINFO GROUPS jobs:ingestion 2>/dev/null || echo "No consumer group found"

echo ""
echo "Recent messages (last 3):"
redis-cli XREVRANGE jobs:ingestion + - COUNT 3
EOF
echo ""

echo "=== 4. Recent Files in Database ==="
ssh root@${INGEST_IP} << 'EOF'
# Get password from env file
PGPASS=$(grep POSTGRES_PASSWORD /srv/ingest/.env | cut -d= -f2)
export PGPASSWORD="$PGPASS"

echo "Recent files:"
psql -h 10.96.200.203 -U busibox_user -d files -c "
  SELECT 
    LEFT(file_id::text, 8) as file_id,
    LEFT(filename, 40) as filename,
    to_char(created_at, 'HH24:MI:SS') as time
  FROM ingestion_files 
  ORDER BY created_at DESC 
  LIMIT 5;
" 2>/dev/null || echo "Could not connect to database"

echo ""
echo "Processing status:"
psql -h 10.96.200.203 -U busibox_user -d files -c "
  SELECT 
    LEFT(f.file_id::text, 8) as file_id,
    s.stage,
    s.progress,
    LEFT(COALESCE(s.error_message, ''), 50) as error
  FROM ingestion_files f
  LEFT JOIN ingestion_status s ON f.file_id = s.file_id
  ORDER BY f.created_at DESC 
  LIMIT 5;
" 2>/dev/null || echo "Could not query status"
EOF
echo ""

echo "=== 5. API Health Check ==="
curl -s http://${INGEST_IP}:8002/health | jq -r '
  "Overall: \(.status)",
  "PostgreSQL: \(.checks.postgres.status)",
  "MinIO: \(.checks.minio.status)",
  "Redis: \(.checks.redis.status)",
  "Milvus: \(.checks.milvus.status)",
  "liteLLM: \(.checks.litellm.status)"
' 2>/dev/null || echo "Could not reach API"
echo ""

echo "================================"
echo "Status check complete!"
echo "================================"

