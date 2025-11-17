# Integration Tests

Integration tests for the ingestion service that connect to real services.

## Prerequisites

These tests require:
- Access to PostgreSQL, Milvus, Redis, MinIO, and liteLLM services
- Valid credentials in `busibox/.env` file (or environment variables)
- Network access to the service IP addresses
- Ingestion API and worker services running (for full pipeline tests)

## Configuration

Tests automatically load environment variables from `busibox/.env` file using `python-dotenv`, or from the environment if `.env` is not available.

### Environment Variables

Required environment variables (aligned with Ansible variable names):

**PostgreSQL:**
- `POSTGRES_HOST` - Database host (e.g., `10.96.201.203`)
- `POSTGRES_PORT` - Database port (default: `5432`)
- `POSTGRES_DB` - Database name (test: `busibox_test`, prod: `agent_server`)
- `POSTGRES_USER` - Database user (test: `busibox_test_user`)
- `POSTGRES_PASSWORD` - Database password (from vault)

**Milvus:**
- `MILVUS_HOST` - Milvus host (e.g., `10.96.201.204`)
- `MILVUS_PORT` - Milvus port (default: `19530`)
- `MILVUS_COLLECTION` - Collection name (default: `documents` or `document_embeddings`)

**Redis:**
- `REDIS_HOST` - Redis host (e.g., `10.96.201.206`)
- `REDIS_PORT` - Redis port (default: `6379`)

**MinIO:**
- `MINIO_ENDPOINT` - MinIO endpoint (e.g., `10.96.201.205:9000`)
- `MINIO_ACCESS_KEY` - Access key (default: `minioadmin`)
- `MINIO_SECRET_KEY` - Secret key (default: `minioadminchange`)
- `MINIO_BUCKET` - Bucket name (default: `documents`)

**liteLLM:**
- `LITELLM_BASE_URL` - liteLLM base URL (e.g., `http://10.96.201.207:4000`)
- `LITELLM_API_KEY` - API key (from vault)

See `CI_CD.md` for details on running tests in CI/CD pipelines with Ansible variables.

## Running Tests

Run all integration tests:
```bash
pytest -m integration
```

Run specific integration test:
```bash
pytest tests/integration/test_pipeline.py -v
```

Run with verbose output:
```bash
pytest -m integration -v -s
```

## Test Files

- `test_pipeline.py` - Full end-to-end pipeline test
- `test_duplicates.py` - Duplicate detection and vector reuse
- `test_sse.py` - SSE status streaming
- `test_errors.py` - Error scenario tests
- `test_concurrent.py` - Concurrent upload handling

## Notes

- Tests create real data in the services
- Tests clean up after themselves
- Some tests may skip if services are not accessible
- Tests may take several minutes to complete (waiting for processing)

