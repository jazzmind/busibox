# Full Pipeline Integration Tests

## Overview

The `test_full_pipeline.py` file contains comprehensive integration tests for the complete document ingestion pipeline, from upload through search and retrieval.

## Test Structure

### Granular Stage Tests

Each pipeline stage has its own test:

1. **`test_stage_1_upload`** - Document upload and initial storage
2. **`test_stage_2_parsing`** - Text extraction and parsing
3. **`test_stage_3_classification`** - Document type and language detection
4. **`test_stage_4_chunking`** - Semantic text chunking
5. **`test_stage_5_embedding`** - Dense embedding generation
6. **`test_stage_6_indexing`** - Vector storage in Milvus

### End-to-End Test

**`test_full_pipeline_with_search`** - Complete lifecycle test:
- Uploads a test document
- Waits for processing to complete
- Verifies data integrity across all storage layers
- Tests semantic search (dense vectors)
- Tests keyword search (BM25 sparse vectors)
- Validates content retrieval
- Tests duplicate detection
- Cleans up test data

## Prerequisites

### 1. Services Running

All integration services must be running:

```bash
# Check service status
docker ps  # or systemctl status for LXC services

# Required services:
# - PostgreSQL (port 5432)
# - Redis (port 6379)
# - MinIO (port 9000)
# - Milvus (port 19530)
# - liteLLM (port 4000)
```

### 2. Database Schema

The `files` database must exist with the ingestion schema:

```bash
# Check if schema exists
psql -h 10.96.201.203 -U busibox_user -d files -c "\dt"

# If not, run Ansible to create it:
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --tags postgres
```

### 3. Milvus Collection

The `documents` collection must exist with the hybrid schema:

```bash
# Check if collection exists
# SSH to milvus container and run:
python /root/hybrid_schema.py

# Or run via Ansible:
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --tags milvus
```

### 4. Worker Running

The ingestion worker must be running to process jobs:

```bash
# Terminal 1: Start the worker
cd srv/ingest
source test_venv/bin/activate  # or your venv
python src/worker.py

# The worker will:
# - Connect to Redis for job queue
# - Process uploaded files
# - Update status in PostgreSQL
# - Store vectors in Milvus
```

### 5. API Server Running

The FastAPI server must be running for upload tests:

```bash
# Terminal 2: Start the API
cd srv/ingest
source test_venv/bin/activate
uvicorn api.main:app --reload --port 8000
```

### 6. Environment Variables

Ensure `.env` file has correct test environment IPs:

```bash
# PostgreSQL
POSTGRES_HOST=10.96.201.203
POSTGRES_PORT=5432
POSTGRES_DB=files
POSTGRES_USER=busibox_user
POSTGRES_PASSWORD=<from vault>

# Redis
REDIS_HOST=10.96.201.206
REDIS_PORT=6379

# MinIO
MINIO_ENDPOINT=10.96.201.205:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadminchange
MINIO_SECURE=false
MINIO_BUCKET=documents

# Milvus
MILVUS_HOST=10.96.201.204
MILVUS_PORT=19530
MILVUS_COLLECTION=documents

# liteLLM
LITELLM_BASE_URL=http://10.96.201.207:4000
LITELLM_API_KEY=<from vault>
```

## Running Tests

### Run All Pipeline Tests

```bash
cd srv/ingest
pytest tests/integration/test_full_pipeline.py -v
```

### Run Specific Stage Test

```bash
# Test only upload stage
pytest tests/integration/test_full_pipeline.py::test_stage_1_upload -v

# Test only chunking stage
pytest tests/integration/test_full_pipeline.py::test_stage_4_chunking -v
```

### Run Full End-to-End Test

```bash
pytest tests/integration/test_full_pipeline.py::test_full_pipeline_with_search -v -s
```

The `-s` flag shows detailed logging output.

### Run with Coverage

```bash
pytest tests/integration/test_full_pipeline.py --cov=src --cov-report=html
```

## Test Output

### Successful Test Output

```
tests/integration/test_full_pipeline.py::test_stage_1_upload PASSED
tests/integration/test_full_pipeline.py::test_stage_2_parsing PASSED
tests/integration/test_full_pipeline.py::test_stage_3_classification PASSED
tests/integration/test_full_pipeline.py::test_stage_4_chunking PASSED
tests/integration/test_full_pipeline.py::test_stage_5_embedding PASSED
tests/integration/test_full_pipeline.py::test_stage_6_indexing PASSED
tests/integration/test_full_pipeline.py::test_full_pipeline_with_search PASSED
```

### Full Pipeline Test Stages

The end-to-end test logs each step:

```
STEP 1: Uploading document
  → Document uploaded (file_id: xxx)

STEP 2: Waiting for processing to complete
  → Processing status: parsing (10%)
  → Processing status: chunking (50%)
  → Processing status: embedding (80%)
  → Processing completed successfully

STEP 3: Verifying data integrity
  → Metadata verified (chunks: 5, vectors: 5)
  → Chunks verified (chunk_count: 5)

STEP 4: Testing semantic search
  → Semantic search successful (results: 5)

STEP 5: Testing keyword search
  → Keyword search successful (results: 2)

STEP 6: Testing content retrieval
  → Content retrieval and verification successful

STEP 7: Testing duplicate detection
  → Duplicate handling tested

STEP 8: Cleaning up test data
  → Cleanup successful

FULL PIPELINE TEST COMPLETED SUCCESSFULLY
```

## Troubleshooting

### Worker Not Processing

**Symptom**: Tests timeout waiting for processing

**Solution**:
```bash
# Check worker is running
ps aux | grep worker.py

# Check worker logs
tail -f /path/to/worker.log

# Check Redis queue
redis-cli -h 10.96.201.206
> XLEN ingestion:jobs
```

### Database Connection Errors

**Symptom**: `connection refused` or `authentication failed`

**Solution**:
```bash
# Test PostgreSQL connection
psql -h 10.96.201.203 -U busibox_user -d files

# Check pg_hba.conf allows connections
# Check password in vault matches .env
```

### Milvus Collection Not Found

**Symptom**: `Collection 'documents' does not exist`

**Solution**:
```bash
# Run schema creation
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --tags milvus

# Or manually:
ssh root@10.96.201.204
python /root/hybrid_schema.py
```

### liteLLM API Errors

**Symptom**: `401 Unauthorized` or `Connection refused`

**Solution**:
```bash
# Check liteLLM is running
curl http://10.96.201.207:4000/health

# Check API key in .env
echo $LITELLM_API_KEY

# Verify key in vault
ansible-vault view provision/ansible/roles/secrets/vars/vault.yml
```

### Tests Pass But Search Fails

**Symptom**: Document indexed but not searchable

**Solution**:
```bash
# Check Milvus collection is loaded
# SSH to Milvus container
python3 << EOF
from pymilvus import connections, Collection
connections.connect(host="localhost", port="19530")
collection = Collection("documents")
print(f"Loaded: {collection.num_entities}")
EOF

# If not loaded:
collection.load()
```

## CI/CD Integration

These tests can be integrated into CI/CD pipelines:

```yaml
# .github/workflows/integration-tests.yml
name: Integration Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_DB: files
          POSTGRES_USER: busibox_user
          POSTGRES_PASSWORD: test_password
      redis:
        image: redis:7
      minio:
        image: minio/minio
      milvus:
        image: milvusdb/milvus:v2.5.4
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          cd srv/ingest
          pip install -r requirements.txt
      
      - name: Run integration tests
        env:
          POSTGRES_HOST: postgres
          REDIS_HOST: redis
          MINIO_ENDPOINT: minio:9000
          MILVUS_HOST: milvus
        run: |
          cd srv/ingest
          pytest tests/integration/test_full_pipeline.py -v
```

## Test Data Cleanup

Tests automatically clean up after themselves, but if interrupted:

```bash
# Clean test files from PostgreSQL
psql -h 10.96.201.203 -U busibox_user -d files << EOF
DELETE FROM ingestion_files WHERE filename LIKE '%test%';
EOF

# Clean test vectors from Milvus
# (Requires manual deletion or TTL policy)

# Clean test objects from MinIO
mc rm --recursive myminio/documents/test-user-*
```

## Performance Benchmarks

Expected test durations (approximate):

- `test_stage_1_upload`: < 1 second
- `test_stage_2_parsing`: 5-10 seconds
- `test_stage_3_classification`: 10-15 seconds
- `test_stage_4_chunking`: 10-15 seconds
- `test_stage_5_embedding`: 30-60 seconds (depends on liteLLM)
- `test_stage_6_indexing`: 5-10 seconds
- `test_full_pipeline_with_search`: 60-120 seconds

Total suite runtime: ~2-4 minutes with all services healthy.

