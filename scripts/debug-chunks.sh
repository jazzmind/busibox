#!/bin/bash
# Debug chunk insertion issues
# Usage: bash debug-chunks.sh <file_id>

set -euo pipefail

FILE_ID="${1:-}"

if [ -z "$FILE_ID" ]; then
    echo "Usage: $0 <file_id>"
    echo ""
    echo "Get file_id from AI Portal or run:"
    echo "  ssh root@10.96.200.205 'sudo -u postgres psql busibox -c \"SELECT file_id, original_filename, chunk_count FROM files ORDER BY created_at DESC LIMIT 5;\"'"
    exit 1
fi

echo "=== Checking chunks for file: $FILE_ID ==="
echo ""

echo "1. PostgreSQL chunks table:"
ssh root@10.96.200.205 "sudo -u postgres psql busibox -c \"
SELECT 
    chunk_index,
    LEFT(text, 50) as text_preview,
    LENGTH(text) as text_length,
    page_number
FROM chunks
WHERE file_id = '$FILE_ID'
ORDER BY chunk_index
LIMIT 10;
\""

echo ""
echo "2. Total chunks in PostgreSQL:"
ssh root@10.96.200.205 "sudo -u postgres psql busibox -c \"
SELECT COUNT(*) as total_chunks
FROM chunks
WHERE file_id = '$FILE_ID';
\""

echo ""
echo "3. File metadata:"
ssh root@10.96.200.205 "sudo -u postgres psql busibox -c \"
SELECT 
    file_id,
    original_filename,
    chunk_count,
    ingestion_status,
    error_message
FROM files
WHERE file_id = '$FILE_ID';
\""

echo ""
echo "4. Milvus vectors (via ingest API):"
ssh root@10.96.200.206 "curl -s 'http://localhost:8000/files/$FILE_ID/chunks?limit=10' | python3 -m json.tool"

echo ""
echo "=== Diagnosis Complete ==="

