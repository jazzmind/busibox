#!/bin/bash
#
# Diagnose Ingestion Issues
#
# This script checks all components of the ingestion pipeline to identify
# where the failure is occurring.
#
# Usage:
#   bash scripts/diagnose-ingestion.sh [test|production]
#

set -euo pipefail

ENVIRONMENT="${1:-test}"

if [ "$ENVIRONMENT" = "test" ]; then
    INGEST_IP="10.96.201.206"
    MINIO_IP="10.96.201.205"
    POSTGRES_IP="10.96.201.203"
    MILVUS_IP="10.96.201.204"
else
    INGEST_IP="10.96.200.206"
    MINIO_IP="10.96.200.205"
    POSTGRES_IP="10.96.200.203"
    MILVUS_IP="10.96.200.204"
fi

echo "=== Ingestion Pipeline Diagnostics ($ENVIRONMENT) ==="
echo ""

# Check if services are running
echo "1. Service Status:"
echo "   Checking ingest-api..."
if ssh root@$INGEST_IP 'systemctl is-active ingest-api' &>/dev/null; then
    echo "   ✓ ingest-api is running"
else
    echo "   ✗ ingest-api is NOT running"
fi

echo "   Checking ingest-worker..."
if ssh root@$INGEST_IP 'systemctl is-active ingest-worker' &>/dev/null; then
    echo "   ✓ ingest-worker is running"
else
    echo "   ✗ ingest-worker is NOT running"
fi

echo ""
echo "2. Recent Worker Logs (last 20 lines):"
ssh root@$INGEST_IP 'journalctl -u ingest-worker -n 20 --no-pager' 2>/dev/null || echo "   Could not fetch logs"

echo ""
echo "3. MinIO Connectivity:"
if ssh root@$INGEST_IP "curl -s -o /dev/null -w '%{http_code}' http://$MINIO_IP:9000/minio/health/live" | grep -q 200; then
    echo "   ✓ MinIO is accessible"
else
    echo "   ✗ MinIO is NOT accessible"
fi

echo ""
echo "4. PostgreSQL Connectivity:"
if ssh root@$INGEST_IP "pg_isready -h $POSTGRES_IP -p 5432" &>/dev/null; then
    echo "   ✓ PostgreSQL is accessible"
else
    echo "   ✗ PostgreSQL is NOT accessible"
fi

echo ""
echo "5. Milvus Connectivity:"
if ssh root@$INGEST_IP "curl -s http://$MILVUS_IP:9091/healthz" | grep -q "OK"; then
    echo "   ✓ Milvus is accessible"
else
    echo "   ✗ Milvus is NOT accessible"
fi

echo ""
echo "6. Redis Connectivity:"
if ssh root@$INGEST_IP "redis-cli -h localhost ping" | grep -q "PONG"; then
    echo "   ✓ Redis is accessible"
else
    echo "   ✗ Redis is NOT accessible"
fi

echo ""
echo "7. Check for failed jobs in Redis:"
ssh root@$INGEST_IP 'redis-cli XLEN ingestion:jobs' 2>/dev/null || echo "   Could not check Redis"

echo ""
echo "8. Recent ingest-api errors:"
ssh root@$INGEST_IP 'journalctl -u ingest-api -n 10 --no-pager | grep -i error' 2>/dev/null || echo "   No recent errors"

echo ""
echo "=== Diagnosis Complete ==="










