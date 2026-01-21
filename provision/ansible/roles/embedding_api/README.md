# Embedding API Role

Deploys the Busibox Embedding API service to the ingest-lxc container.

## Description

This role installs and configures the Embedding API, a dedicated service that:
- Loads the FastEmbed model once at startup
- Provides HTTP API for embedding generation
- Serves all embedding consumers (ingest-api, ingest-worker, search-api)

## Requirements

- Target: ingest-lxc container
- Python 3.11+
- Sufficient RAM for model loading (~2GB for bge-large)

## Role Variables

### Service Configuration

- `embedding_api_port`: API port (default: 8005)
- `embedding_api_user`: Service user (default: embedding)
- `embedding_api_venv_path`: Python venv path
- `embedding_api_src_path`: Source code path
- `embedding_api_log_path`: Log directory

### Model Configuration

- `embedding_model`: FastEmbed model name (default: BAAI/bge-large-en-v1.5)
- `embedding_dimension`: Model dimension (default: 1024)
- `embedding_batch_size`: Batch size for embedding (default: 32)

## Model Options

| Model | Dimension | Size | Notes |
|-------|-----------|------|-------|
| BAAI/bge-large-en-v1.5 | 1024 | ~1.3GB | Production (default) |
| BAAI/bge-base-en-v1.5 | 768 | ~440MB | Balanced |
| BAAI/bge-small-en-v1.5 | 384 | ~134MB | Fast/dev |

## Example Playbook

```yaml
- hosts: ingest
  roles:
    - role: embedding_api
      vars:
        embedding_batch_size: 32
```

## Service Management

```bash
# Check status
systemctl status embedding-api

# View logs
journalctl -u embedding-api -f

# Restart service
systemctl restart embedding-api
```

## API Endpoints

```bash
# Health check
curl http://localhost:8005/health

# Get model info
curl http://localhost:8005/info

# Generate embeddings
curl -X POST http://localhost:8005/embed \
  -H "Content-Type: application/json" \
  -d '{"input": "Hello, world!"}'

# Batch embeddings
curl -X POST http://localhost:8005/embed \
  -H "Content-Type: application/json" \
  -d '{"input": ["Text 1", "Text 2", "Text 3"]}'
```

## Architecture

The embedding service runs on ingest-lxc and serves multiple consumers:

```
ingest-lxc:
├── Ingest API    - Port 8002 (proxies to embedding-api)
├── Ingest Worker - Uses FastEmbed directly (deprecated)
└── Embedding API - Port 8005
    ├── FastAPI server
    └── FastEmbed model (loaded once at startup)

milvus-lxc:
└── Search API - Port 8003 (calls embedding-api:8005)
```

## Performance

- Model load time: ~30-60 seconds (first run downloads model)
- Embedding latency: ~50-100ms per text
- Batch throughput: ~100-200 texts/second

## Consumers

This service is used by:
- `search-api`: Query embedding generation
- `ingest-api`: Document embedding (proxied through /api/embeddings)
- `ingest-worker`: Document embedding (direct FastEmbed call)

## Author

Busibox Team
