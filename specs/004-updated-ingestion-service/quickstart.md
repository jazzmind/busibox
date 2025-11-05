# Quickstart: Production-Grade Document Ingestion Service

**Feature**: Updated ingestion service with multi-vector hybrid search  
**Date**: 2025-11-05

This guide provides step-by-step instructions for deploying and testing the enhanced ingestion service.

---

## Prerequisites

- Proxmox host with Busibox LXC containers deployed
- Ansible control machine with access to inventory
- PostgreSQL, MinIO, Milvus, Redis, and liteLLM services running
- Python 3.11+ on development machine for local testing

---

## Phase 1: Prepare Infrastructure

### 1.1 Update Milvus Collection Schema

The new hybrid search requires updating the Milvus collection to support BM25 and multi-vector embeddings.

**On ansible control machine**:

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Deploy updated Milvus schema
ansible-playbook -i inventory/test/hosts.yml site.yml --tags milvus
```

This will:
- Create new `documents` collection with hybrid schema
- Add BM25 function for automatic sparse embedding generation
- Create indexes for dense, sparse, and multi-vector fields
- Migrate existing data (if any) to new schema

**Verify**:

```bash
# SSH into milvus-lxc
ssh root@10.96.200.24

# Check collection exists
python3 << 'EOF'
from pymilvus import connections, Collection
connections.connect(host="localhost", port="19530")
collection = Collection("documents")
print(f"Collection loaded: {collection.name}")
print(f"Num entities: {collection.num_entities}")
print(f"Schema: {collection.schema}")
EOF
```

Expected output:
```
Collection loaded: documents
Num entities: 0
Schema: <fields: id, file_id, text_dense, text_sparse, page_vectors, ...>
```

---

### 1.2 Configure liteLLM Embedding Models

Add text-embedding-3-small to liteLLM configuration.

**Update variables**:

Edit `provision/ansible/inventory/test/group_vars/litellm.yml`:

```yaml
litellm_models:
  # Existing models...
  
  # Add embedding model
  - model_name: text-embedding-3-small
    litellm_params:
      model: openai/text-embedding-3-small
      api_base: "{{ lookup('env', 'OPENAI_API_BASE') | default('https://api.openai.com/v1') }}"
      api_key: "{{ litellm_openai_api_key }}"
    model_info:
      mode: embedding
      supports_vision: false
      max_tokens: 8191
```

**Deploy**:

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

ansible-playbook -i inventory/test/hosts.yml site.yml --tags litellm
```

**Verify**:

```bash
# Test embedding endpoint
curl -X POST http://litellm-lxc:4000/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "text-embedding-3-small",
    "input": "Hello world"
  }'
```

Expected response:
```json
{
  "data": [{"embedding": [0.123, -0.456, ...], "index": 0}],
  "model": "text-embedding-3-small",
  "usage": {"prompt_tokens": 2, "total_tokens": 2}
}
```

---

### 1.3 (Optional) Deploy ColPali Model for PDF Visual Search

If you want visual PDF search, deploy ColPali to vLLM.

**Update vLLM configuration**:

Edit `provision/ansible/inventory/test/group_vars/vllm.yml`:

```yaml
vllm_models:
  # Existing models...
  
  # Add ColPali
  - name: colpali-v1.2
    repo_id: vidore/colpali-v1.2
    enable: true
    gpu_memory_utilization: 0.5
    max_model_len: 512
```

**Deploy**:

```bash
ansible-playbook -i inventory/test/hosts.yml site.yml --tags vllm
```

**Note**: ColPali requires GPU. Skip this step if you don't have GPU available or want text-only search.

---

## Phase 2: Deploy Ingestion Service

### 2.1 Deploy FastAPI Service (ingest-api)

**Deploy new API role**:

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

ansible-playbook -i inventory/test/hosts.yml site.yml --tags ingest_api
```

This will:
- Install FastAPI and dependencies on ingest-lxc
- Create systemd service for API (`ingest-api.service`)
- Configure environment variables
- Start service on port 8002

**Verify**:

```bash
# Check service status
ssh root@10.96.200.30 "systemctl status ingest-api"

# Test health endpoint
curl http://10.96.200.30:8002/health
```

Expected response:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "dependencies": {
    "postgres": {"status": "up", "responseTime": 15},
    "minio": {"status": "up", "responseTime": 20},
    "redis": {"status": "up", "responseTime": 5},
    "milvus": {"status": "up", "responseTime": 25},
    "litellm": {"status": "up", "responseTime": 30}
  }
}
```

---

### 2.2 Update Ingestion Worker

**Deploy enhanced worker**:

```bash
ansible-playbook -i inventory/test/hosts.yml site.yml --tags ingest_worker
```

This will:
- Install new Python dependencies (Marker, TATR, ColPali, etc.)
- Update worker configuration
- Restart worker service (`ingest-worker.service`)

**Verify**:

```bash
# Check worker status
ssh root@10.96.200.30 "systemctl status ingest-worker"

# Check worker logs
ssh root@10.96.200.30 "journalctl -u ingest-worker -n 50 --no-pager"
```

Expected log output:
```
Nov 05 10:00:00 ingest-lxc python[12345]: [INFO] Worker started (PID: 12345)
Nov 05 10:00:00 ingest-lxc python[12345]: [INFO] Connected to Redis at redis://10.96.200.30:6379
Nov 05 10:00:00 ingest-lxc python[12345]: [INFO] Connected to PostgreSQL at postgresql://10.96.200.13:5432
Nov 05 10:00:00 ingest-lxc python[12345]: [INFO] Waiting for jobs...
```

---

## Phase 3: Test End-to-End

### 3.1 Upload a Test Document

**Prepare test file**:

```bash
# Create sample PDF
echo "Test document for ingestion" > /tmp/test.txt
# Or use an actual PDF: cp ~/Documents/sample.pdf /tmp/test.pdf
```

**Upload via API**:

```bash
curl -X POST http://10.96.200.30:8002/upload \
  -H "X-User-Id: user-test-123" \
  -F "file=@/tmp/test.txt"
```

Expected response:
```json
{
  "fileId": "file-abc123...",
  "filename": "test.txt",
  "sizeBytes": 28,
  "mimeType": "text/plain",
  "status": "queued",
  "createdAt": "2025-11-05T10:05:00Z"
}
```

**Save fileId for next steps**:

```bash
FILE_ID="file-abc123..."
```

---

### 3.2 Monitor Processing Status (SSE)

**Stream status updates**:

```bash
curl -N -H "X-User-Id: user-test-123" \
  http://10.96.200.30:8002/status/$FILE_ID
```

Expected output (streamed):
```
event: status
data: {"fileId":"file-abc123...","stage":"queued","progress":0}

event: status
data: {"fileId":"file-abc123...","stage":"parsing","progress":10}

event: status
data: {"fileId":"file-abc123...","stage":"chunking","progress":40,"totalChunks":1}

event: status
data: {"fileId":"file-abc123...","stage":"embedding","progress":60,"chunksProcessed":1,"totalChunks":1}

event: status
data: {"fileId":"file-abc123...","stage":"indexing","progress":80}

event: status
data: {"fileId":"file-abc123...","stage":"completed","progress":100,"chunksProcessed":1,"totalChunks":1}

event: close
data: {"message":"Processing complete"}
```

---

### 3.3 Verify Data Stored

**Check PostgreSQL**:

```bash
ssh root@10.96.200.13 "psql -U busibox -d busibox -c \"
  SELECT file_id, filename, document_type, chunk_count 
  FROM ingestion_files 
  WHERE file_id = '$FILE_ID';
\""
```

Expected output:
```
     file_id      | filename  | document_type | chunk_count
------------------+-----------+---------------+-------------
 file-abc123...   | test.txt  | document      | 1
```

**Check Milvus**:

```bash
ssh root@10.96.200.24 "python3 << 'EOF'
from pymilvus import connections, Collection
connections.connect(host='localhost', port='19530')
collection = Collection('documents')
results = collection.query(expr='file_id == \"$FILE_ID\"', output_fields=['*'])
print(f'Found {len(results)} vectors for file')
for r in results[:1]:
    print(f'  Chunk {r[\"chunk_index\"]}: {r[\"text\"][:50]}...')
EOF"
```

Expected output:
```
Found 1 vectors for file
  Chunk 0: Test document for ingestion...
```

**Check MinIO**:

```bash
ssh root@10.96.200.25 "mc ls minio/documents/user-test-123/$FILE_ID/"
```

Expected output:
```
[2025-11-05 10:05:00 UTC]    28B test.txt
```

---

### 3.4 Test Hybrid Search

**Search for the document**:

```bash
# This will be implemented in the agent-server, but you can test directly with Milvus
ssh root@10.96.200.24 "python3 << 'EOF'
from pymilvus import connections, Collection
import litellm

# Connect to Milvus
connections.connect(host='localhost', port='19530')
collection = Collection('documents')

# Generate query embedding
query = 'test document'
embedding = litellm.embedding(
    model='text-embedding-3-small',
    input=query,
    api_base='http://10.96.200.28:4000'
)
query_vector = embedding['data'][0]['embedding']

# Hybrid search (dense + BM25)
results = collection.search(
    data=[query_vector],
    anns_field='text_dense',
    param={'metric_type': 'COSINE', 'params': {'ef': 64}},
    limit=5,
    output_fields=['file_id', 'text', 'chunk_index']
)

print('Search results:')
for hits in results:
    for hit in hits:
        print(f'  Score: {hit.score:.4f} | {hit.entity.get(\"text\")[:50]}...')
EOF"
```

Expected output:
```
Search results:
  Score: 0.9850 | Test document for ingestion...
```

---

### 3.5 Test File Deletion

**Delete the test file**:

```bash
curl -X DELETE http://10.96.200.30:8002/files/$FILE_ID \
  -H "X-User-Id: user-test-123"
```

Expected response: `204 No Content`

**Verify deletion**:

```bash
# Check PostgreSQL (should be empty)
ssh root@10.96.200.13 "psql -U busibox -d busibox -c \"
  SELECT COUNT(*) FROM ingestion_files WHERE file_id = '$FILE_ID';
\""

# Check Milvus (should return 0)
ssh root@10.96.200.24 "python3 << 'EOF'
from pymilvus import connections, Collection
connections.connect(host='localhost', port='19530')
collection = Collection('documents')
results = collection.query(expr='file_id == \"$FILE_ID\"')
print(f'Vectors remaining: {len(results)}')
EOF"

# Check MinIO (should be gone)
ssh root@10.96.200.25 "mc ls minio/documents/user-test-123/$FILE_ID/ 2>&1"
```

Expected outputs:
```
 count
-------
     0

Vectors remaining: 0

mc: <ERROR> Object does not exist.
```

---

## Phase 4: Integration with Apps

### 4.1 Update App Environment

Apps (Next.js in apps-lxc) need to know about the ingestion API endpoint.

**Edit app environment**:

```yaml
# provision/ansible/inventory/test/group_vars/apps.yml
app_env:
  INGEST_API_URL: "http://10.96.200.30:8002"
```

**Redeploy apps**:

```bash
ansible-playbook -i inventory/test/hosts.yml site.yml --tags nextjs_app
```

---

### 4.2 Example: Upload from Next.js App

**Add upload API route** (`apps/[app]/app/api/documents/upload/route.ts`):

```typescript
import { NextRequest, NextResponse } from 'next/server';

export async function POST(req: NextRequest) {
  const formData = await req.formData();
  const file = formData.get('file') as File;
  
  // Get user from session
  const userId = req.headers.get('x-user-id');
  
  // Forward to ingestion service
  const ingestFormData = new FormData();
  ingestFormData.append('file', file);
  
  const response = await fetch(`${process.env.INGEST_API_URL}/upload`, {
    method: 'POST',
    headers: {
      'X-User-Id': userId,
    },
    body: ingestFormData,
  });
  
  const data = await response.json();
  return NextResponse.json(data);
}
```

**Add status streaming** (`apps/[app]/app/api/documents/[fileId]/status/route.ts`):

```typescript
export async function GET(
  req: NextRequest,
  { params }: { params: { fileId: string } }
) {
  const userId = req.headers.get('x-user-id');
  
  // Stream SSE from ingestion service
  const response = await fetch(
    `${process.env.INGEST_API_URL}/status/${params.fileId}`,
    {
      headers: { 'X-User-Id': userId },
    }
  );
  
  // Forward SSE stream to client
  return new Response(response.body, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    },
  });
}
```

---

## Troubleshooting

### Issue: Service won't start

**Check logs**:
```bash
ssh root@10.96.200.30 "journalctl -u ingest-api -n 100 --no-pager"
ssh root@10.96.200.30 "journalctl -u ingest-worker -n 100 --no-pager"
```

**Common causes**:
- Missing environment variables (check `/srv/ingest-api/.env`)
- Dependency service down (check health endpoint)
- Port already in use (check `netstat -tulpn | grep 8002`)
- Python dependencies not installed (check `/srv/ingest-api/venv/bin/python -m pip list`)
- Source code not copied correctly (check `/srv/ingest-api/src/api/main.py` exists)

---

### Issue: Worker not processing jobs

**Check Redis queue**:
```bash
ssh root@10.96.200.30 "redis-cli XLEN jobs:ingestion"
```

If queue is growing but worker isn't processing:
```bash
# Check worker is running
systemctl status ingest-worker

# Check worker logs for errors
journalctl -u ingest-worker -f
```

---

### Issue: Embedding generation fails

**Test liteLLM directly**:
```bash
curl -X POST http://10.96.200.28:4000/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "text-embedding-3-small", "input": "test"}'
```

If this fails:
- Check liteLLM logs: `ssh root@10.96.200.28 "journalctl -u litellm -n 100"`
- Verify API key in liteLLM environment
- Check network connectivity to OpenAI (if using cloud)

---

### Issue: Milvus insert fails

**Check Milvus connection**:
```bash
ssh root@10.96.200.24 "python3 << 'EOF'
from pymilvus import connections, utility
connections.connect(host='localhost', port='19530')
print('Server version:', utility.get_server_version())
print('Collections:', utility.list_collections())
EOF"
```

If connection fails:
- Check Milvus is running: `systemctl status milvus`
- Check collection exists and is loaded: `collection.load()`
- Verify schema matches code (re-run schema migration if needed)

---

## Performance Tuning

### Scale Workers Horizontally

To process more files concurrently, increase worker count:

**Edit systemd service**:
```bash
ssh root@10.96.200.30
nano /etc/systemd/system/ingest-worker@.service
```

**Start multiple workers**:
```bash
systemctl enable ingest-worker@{1..4}
systemctl start ingest-worker@{1..4}
```

Each worker will consume from the same Redis consumer group.

---

### Adjust Chunk Sizes

If search quality is poor, adjust chunking parameters:

**Edit worker config** (`srv/ingest/src/worker/processors/chunker.py`):
```python
CHUNK_MIN_TOKENS = 600  # Increase for more context
CHUNK_MAX_TOKENS = 1000
CHUNK_OVERLAP_PCT = 0.15  # Increase for better boundary handling
```

Redeploy worker and re-process documents.

---

## Next Steps

1. **Add more document types**: Extend `TextExtractor` with DOCX, HTML, etc.
2. **Enable ColPali**: Deploy ColPali model and enable PDF visual search
3. **Add reranking**: Integrate cross-encoder reranker for higher precision
4. **Implement batching**: Batch embedding requests for better throughput
5. **Add monitoring**: Deploy Prometheus/Grafana for metrics

---

## Reference

- **API Contract**: [contracts/ingest-api.openapi.yaml](./contracts/ingest-api.openapi.yaml)
- **Data Model**: [data-model.md](./data-model.md)
- **Research**: [research.md](./research.md)
- **Architecture**: [../../docs/architecture/ingest-service-specification.md](../../docs/architecture/ingest-service-specification.md)

