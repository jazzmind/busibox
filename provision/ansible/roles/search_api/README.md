# Search API Role

Deploys the Busibox Search API service to the milvus-lxc container.

## Description

This role installs and configures the Search API, a sophisticated search service that provides:
- Keyword search (BM25)
- Semantic search (dense vectors)
- Hybrid search (BM25 + dense with RRF fusion)
- Cross-encoder reranking
- Search term highlighting
- Semantic alignment visualization
- MMR diversity

## Requirements

- Target: milvus-lxc container
- Milvus 2.5+ running on localhost
- PostgreSQL accessible
- Python 3.11+

## Role Variables

### Service Configuration

- `search_api_port`: API port (default: 8003)
- `search_api_user`: Service user (default: search)
- `search_api_venv_path`: Python venv path
- `search_api_src_path`: Source code path
- `search_api_log_path`: Log directory

### Dependencies

- `milvus_host`: Milvus host (default: localhost)
- `milvus_port`: Milvus port (default: 19530)
- `postgres_host`: PostgreSQL host
- `postgres_port`: PostgreSQL port
- `postgres_db`: Database name
- `postgres_user`: Database user
- `db_app_password`: Database password (from vault)

### Embedding Service

- `embedding_service_url`: URL of embedding service (local FastEmbed on ingest-lxc)
- `embedding_model`: Model name (default: bge-large-en-v1.5)

### Reranking

- `reranker_model`: Cross-encoder model (default: BAAI/bge-reranker-v2-m3)
- `reranker_device`: Device (cpu or cuda)
- `enable_reranking`: Enable reranking (default: true)

### Caching

- `redis_host`: Redis host (optional)
- `redis_port`: Redis port
- `enable_caching`: Enable query caching

## Dependencies

This role depends on:
- Milvus running (milvus role)
- PostgreSQL accessible (postgres role)
- Embedding API service (embedding_api role on ingest-lxc)

## Example Playbook

```yaml
- hosts: milvus
  roles:
    - role: search_api
      vars:
        enable_reranking: true
        reranker_device: cpu
```

## Service Management

```bash
# Check status
systemctl status search-api

# View logs
journalctl -u search-api -f

# Restart service
systemctl restart search-api
```

## Testing

```bash
# Health check
curl http://localhost:8003/health

# Test search
curl -X POST http://localhost:8003/search \
  -H "Content-Type: application/json" \
  -H "X-User-Id: test-user-id" \
  -d '{
    "query": "machine learning",
    "mode": "hybrid",
    "limit": 10,
    "rerank": true
  }'
```

## Architecture

The search service runs in milvus-lxc, colocated with Milvus for low-latency vector operations:

```
milvus-lxc:
├── Milvus (Docker) - Port 19530
└── Search API      - Port 8003
    ├── FastAPI web server
    ├── Milvus client
    ├── Reranker model
    └── Highlighting engine
```

## Performance

- Target latency: P95 < 300ms
- Concurrent requests: 50
- Resource limits: 4GB RAM, 200% CPU

## Author

Busibox Team

