# Environment Setup for Integration Tests

Integration tests require a `.env` file at `/busibox/.env` (project root) with the following variables:

## Required Environment Variables

```bash
# PostgreSQL (files database) - NOTE: Must be 'files', not 'busibox_test'
POSTGRES_HOST=10.96.201.203
POSTGRES_PORT=5432
POSTGRES_DB=files
POSTGRES_USER=busibox_test_user
POSTGRES_PASSWORD=your-postgres-password

# Redis - NOTE: Must be set, not REDIS_IP
REDIS_HOST=10.96.201.206
REDIS_PORT=6379

# MinIO
MINIO_ENDPOINT=10.96.201.205:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadminchange
MINIO_SECURE=false
MINIO_BUCKET=documents

# Milvus - REQUIRED for Milvus tests
MILVUS_HOST=10.96.201.204
MILVUS_PORT=19530
MILVUS_COLLECTION=documents

# NOTE: The Milvus collection must be created with the correct hybrid schema.
# Run: provision/ansible/roles/milvus/files/hybrid_schema.py
# Or manually create a collection named 'documents' with the required fields:
# id, file_id, chunk_index, page_number, text, text_dense, text_sparse, modality, etc.

# liteLLM
LITELLM_BASE_URL=http://10.96.201.207:4000
LITELLM_API_KEY=your-litellm-api-key
```

## Critical Notes

1. **POSTGRES_DB must be `files`** - The ingestion service uses a dedicated `files` database, not `busibox_test`
2. **REDIS_HOST must be set** - The config looks for `REDIS_HOST`, not `REDIS_IP`
3. The tests load environment from `/busibox/.env` (project root), not `srv/ingest/.env`

## Current Issues

If tests are failing, check your `.env`:

```bash
# In /busibox directory
cat .env | grep -E '(POSTGRES_DB|REDIS_HOST)'
```

Expected output:
```
POSTGRES_DB=files
REDIS_HOST=10.96.201.206
```

If you see `POSTGRES_DB=busibox_test` or `REDIS_HOST` is missing, update your `.env` file.

