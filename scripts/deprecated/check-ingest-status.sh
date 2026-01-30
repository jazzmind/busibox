#!/bin/bash
# Check Data Service Status
# Run from: Admin workstation
# Usage: bash scripts/check/check-data-status.sh

set -e

DATA_IP="10.96.200.206"
POSTGRES_IP="10.96.200.203"

echo "================================"
echo "Data Service Status Check"
echo "================================"
echo ""

echo "=== 1. Service Status ==="
ssh root@${DATA_IP} "systemctl status data-api data-worker redis-server --no-pager | grep -E 'Active:|Main PID:|Memory:'"
echo ""

echo "=== 2. Recent Worker Logs (last 20 lines) ==="
ssh root@${DATA_IP} "journalctl -u data-worker -n 20 --no-pager | tail -15"
echo ""

echo "=== 3. Redis Queue Status ==="
ssh root@${DATA_IP} << 'EOF'
echo "Stream length:"
redis-cli XLEN jobs:data

echo ""
echo "Consumer groups:"
redis-cli XINFO GROUPS jobs:data 2>/dev/null || echo "No consumer group found"

echo ""
echo "Recent messages (last 3):"
redis-cli XREVRANGE jobs:data + - COUNT 3
EOF
echo ""

echo "=== 4. Recent Files in Database ==="
ssh root@${DATA_IP} << 'EOF'
# Get password from env file
PGPASS=$(grep POSTGRES_PASSWORD /srv/data/.env | cut -d= -f2)
export PGPASSWORD="$PGPASS"

echo "Recent files:"
psql -h 10.96.200.203 -U busibox_user -d files -c "
  SELECT 
    LEFT(file_id::text, 8) as file_id,
    LEFT(filename, 40) as filename,
    to_char(created_at, 'HH24:MI:SS') as time
  FROM data_files 
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
  FROM data_files f
  LEFT JOIN data_status s ON f.file_id = s.file_id
  ORDER BY f.created_at DESC 
  LIMIT 5;
" 2>/dev/null || echo "Could not query status"
EOF
echo ""

echo "=== 5. API Health Check ==="
curl -s http://${DATA_IP}:8002/health | jq -r '
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

