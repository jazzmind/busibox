#!/bin/bash
# Check Document Processing Status
# Run from: Admin workstation
# Usage: bash scripts/check-document-status.sh [file_id]

set -e

INGEST_IP="10.96.200.206"
OLLAMA_IP="10.96.200.210"
LITELLM_IP="10.96.200.207"

echo "================================"
echo "Document Processing Status"
echo "================================"
echo ""

if [ -n "$1" ]; then
  FILE_ID="$1"
  echo "=== Specific File: $FILE_ID ==="
  ssh root@${INGEST_IP} "psql -U busibox_user -d files -c \"SELECT file_id, filename, stage, progress, chunks_processed, total_chunks, error_message, updated_at FROM ingestion_status WHERE file_id = '$FILE_ID';\""
else
  echo "=== Recent Files (Last 10) ==="
  ssh root@${INGEST_IP} "psql -U busibox_user -d files -c \"SELECT file_id, filename, stage, progress, chunks_processed, total_chunks, error_message, updated_at FROM ingestion_status ORDER BY updated_at DESC LIMIT 10;\" | head -30"
fi

echo ""
echo "=== Worker Status ==="
ssh root@${INGEST_IP} "systemctl status ingest-worker --no-pager | head -10"

echo ""
echo "=== Recent Worker Logs (Last 10 lines) ==="
ssh root@${INGEST_IP} "journalctl -u ingest-worker -n 10 --no-pager | grep -E 'event|error|stage|embedding|chunk' || journalctl -u ingest-worker -n 10 --no-pager"

echo ""
echo "=== Ollama Status ==="
ssh root@${OLLAMA_IP} "curl -s http://localhost:11434/api/tags | jq '.models[] | {name: .name, size: .size}' 2>/dev/null || echo 'Ollama not responding or jq not installed'"

echo ""
echo "=== Ollama Recent Activity (Last 5 lines) ==="
ssh root@${OLLAMA_IP} "journalctl -u ollama -n 5 --no-pager || echo 'No Ollama logs'"

echo ""
echo "=== liteLLM Status ==="
ssh root@${LITELLM_IP} "systemctl status litellm --no-pager | head -5"

echo ""
echo "=== Redis Queue Length ==="
ssh root@${INGEST_IP} "redis-cli XLEN jobs:ingestion"

echo ""
echo "================================"
echo "To watch live logs:"
echo "  Worker:   ssh root@${INGEST_IP} 'journalctl -u ingest-worker -f'"
echo "  Ollama:   ssh root@${OLLAMA_IP} 'journalctl -u ollama -f'"
echo "  liteLLM:  ssh root@${LITELLM_IP} 'journalctl -u litellm -f'"
echo "================================"

