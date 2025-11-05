# Integration Tests

Integration tests for the ingestion service that connect to real services.

## Prerequisites

These tests require:
- Access to PostgreSQL, Milvus, Redis, MinIO, and liteLLM services
- Valid credentials in `busibox/.env` file
- Network access to the service IP addresses

## Configuration

Tests automatically load environment variables from `busibox/.env` file using `python-dotenv`.

Required environment variables:
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `MILVUS_HOST`, `MILVUS_PORT`, `MILVUS_COLLECTION`
- `REDIS_HOST`, `REDIS_PORT`
- `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`
- `LITELLM_BASE_URL`, `LITELLM_API_KEY`

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

