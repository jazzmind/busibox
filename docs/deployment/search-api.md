# Search API Deployment Guide

**Created**: 2025-11-17  
**Updated**: 2025-11-17  
**Status**: Active  
**Category**: Deployment

## Overview

This guide covers deploying the sophisticated Search API to the **milvus-lxc** container. The Search API provides keyword, semantic, and hybrid search capabilities with reranking, highlighting, and semantic alignment visualization.

## Prerequisites

Before deploying the Search API, ensure the following are operational:

1. **milvus-lxc** container created and running
2. **Milvus** service running on milvus-lxc (port 19530)
3. **PostgreSQL** accessible from milvus-lxc
4. **liteLLM** or embedding service accessible
5. **Documents ingested** into Milvus with embeddings

## Architecture

The Search API is deployed to milvus-lxc for optimal performance:

```
milvus-lxc (CTID 204, IP 10.96.200.27)
├── Milvus (Docker)
│   └── Port 19530 (vector database)
├── Search API (systemd)
│   └── Port 8003 (FastAPI service)
└── Dependencies
    ├── PostgreSQL (pg-lxc) - File metadata
    ├── liteLLM (litellm-lxc) - Query embeddings
    └── Redis (ingest-lxc) - Optional caching
```

## Deployment Steps

### 1. Update Ansible Inventory

Ensure milvus-lxc is in the `milvus` group in your inventory:

**`provision/ansible/inventory/production/hosts.yml`:**
```yaml
all:
  children:
    milvus:
      hosts:
        milvus-lxc:
          ansible_host: 10.96.200.27
```

### 2. Configure Variables (Optional)

Create group vars if you need to customize settings:

**`provision/ansible/inventory/production/group_vars/milvus.yml`:**
```yaml
# Search API Configuration
search_api_port: 8003
enable_reranking: true
reranker_device: cpu  # or cuda if GPU available
enable_caching: false

# Reranker model (default is bge-reranker-v2-m3)
# reranker_model: "BAAI/bge-reranker-v2-m3"
```

### 3. Deploy with Ansible

Deploy search services (Milvus + Search API):

```bash
cd provision/ansible

# Deploy both Milvus and Search API (recommended)
make search

# Or deploy just Search API (if Milvus already deployed)
make search-api

# With test environment
make search INV=inventory/test
make search-api INV=inventory/test
```

Alternative: using ansible-playbook directly:

```bash
# Deploy to production
ansible-playbook -i inventory/production/hosts.yml site.yml --tags search_api

# Deploy to test
ansible-playbook -i inventory/test/hosts.yml site.yml --tags search_api

# Deploy everything to milvus-lxc
ansible-playbook -i inventory/production/hosts.yml site.yml --limit milvus
```

### 4. Verify Deployment

Check that the service is running:

```bash
# SSH to milvus-lxc
ssh root@10.96.200.27

# Check service status
systemctl status search-api

# Check logs
journalctl -u search-api -f

# Test health endpoint
curl http://localhost:8003/health
```

Expected health response:
```json
{
  "status": "healthy",
  "milvus": "connected",
  "postgres": "connected",
  "reranker": "loaded",
  "embedder": "available",
  "cache": null
}
```

### 5. Test Search Functionality

Test the search endpoints:

```bash
# Test hybrid search
curl -X POST http://10.96.200.27:8003/search \
  -H "Content-Type: application/json" \
  -H "X-User-Id: your-user-id" \
  -d '{
    "query": "machine learning best practices",
    "mode": "hybrid",
    "limit": 10,
    "rerank": true,
    "dense_weight": 0.7,
    "sparse_weight": 0.3,
    "highlight": {
      "enabled": true,
      "fragment_size": 200,
      "num_fragments": 3
    }
  }'

# Test keyword search
curl -X POST http://10.96.200.27:8003/search/keyword \
  -H "Content-Type: application/json" \
  -H "X-User-Id: your-user-id" \
  -d '{
    "query": "specific term or phrase",
    "limit": 10
  }'

# Test semantic search
curl -X POST http://10.96.200.27:8003/search/semantic \
  -H "Content-Type: application/json" \
  -H "X-User-Id: your-user-id" \
  -d '{
    "query": "conceptual question about topic",
    "limit": 10
  }'
```

## Configuration

### Environment Variables

The search API is configured via `/opt/search/.env`:

```bash
# Service
SERVICE_PORT=8003
LOG_LEVEL=INFO

# Milvus (local)
MILVUS_HOST=localhost
MILVUS_PORT=19530

# PostgreSQL
POSTGRES_HOST=10.96.200.26
POSTGRES_PORT=5432
POSTGRES_DB=busibox
POSTGRES_USER=app_user
POSTGRES_PASSWORD=<from_vault>

# Embedding Service (local FastEmbed on ingest-lxc)
EMBEDDING_SERVICE_URL=http://10.96.200.30:8002
EMBEDDING_MODEL=bge-large-en-v1.5

# Reranking
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_DEVICE=cpu
ENABLE_RERANKING=true

# Performance
DEFAULT_SEARCH_LIMIT=10
MAX_SEARCH_LIMIT=100
```

### Service Configuration

The systemd service is configured at `/etc/systemd/system/search-api.service`:

- **User**: `search` (dedicated service user)
- **Working Directory**: `/opt/search/src`
- **Virtual Environment**: `/opt/search/venv`
- **Restart Policy**: Always restart on failure
- **Resource Limits**: 4GB RAM, 200% CPU

## Integration with Applications

### From ai-portal

Update the ai-portal to use the new search endpoint:

```typescript
// src/config/services.ts
const SEARCH_SERVICE_URL = `http://${MILVUS_CONTAINER_IP}:8003`;

// API route
export async function POST(request: NextRequest) {
  const { query, limit = 10 } = await request.json();
  
  const response = await fetch(`${SEARCH_SERVICE_URL}/search`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Id': user.id,
    },
    body: JSON.stringify({
      query,
      mode: 'hybrid',
      limit,
      rerank: true,
      highlight: { enabled: true },
    }),
  });
  
  return response.json();
}
```

### From agent-lxc

The agent API can call the search service for RAG:

```python
import httpx

SEARCH_SERVICE_URL = "http://10.96.200.27:8003"

async def search_documents(query: str, user_id: str, limit: int = 10):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{SEARCH_SERVICE_URL}/search",
            headers={"X-User-Id": user_id},
            json={
                "query": query,
                "mode": "hybrid",
                "limit": limit,
                "rerank": True,
            },
        )
        return response.json()
```

## Monitoring

### Service Logs

View logs with journalctl:

```bash
# Real-time logs
journalctl -u search-api -f

# Last 100 lines
journalctl -u search-api -n 100

# Logs since yesterday
journalctl -u search-api --since yesterday

# JSON format for parsing
journalctl -u search-api -o json
```

### Metrics

Monitor key metrics:

```bash
# Service status
systemctl status search-api

# Resource usage
ps aux | grep search-api

# Memory usage
systemctl show search-api --property=MemoryCurrent

# Request logs
journalctl -u search-api | grep "Search completed"
```

### Health Checks

The `/health` endpoint provides detailed status:

```bash
curl http://10.96.200.27:8003/health | jq
```

Monitor for:
- `status`: "healthy", "degraded", or "unhealthy"
- `milvus`: Milvus connection status
- `postgres`: PostgreSQL connection status
- `reranker`: Reranker model status
- `embedder`: Embedding service status

## Troubleshooting

### Service Won't Start

Check logs for errors:

```bash
journalctl -u search-api -n 50 --no-pager
```

Common issues:

1. **Milvus not running**:
   ```bash
   cd /srv/milvus
   docker-compose ps
   docker-compose up -d
   ```

2. **Python dependencies missing**:
   ```bash
   su - search
   cd /opt/search
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Permission issues**:
   ```bash
   chown -R search:search /opt/search
   chmod 600 /opt/search/.env
   ```

### Slow Search Performance

Check reranker device:

```bash
grep RERANKER_DEVICE /opt/search/.env
```

If using CPU and GPU is available:

```bash
# Update config
sed -i 's/RERANKER_DEVICE=cpu/RERANKER_DEVICE=cuda/' /opt/search/.env

# Restart service
systemctl restart search-api
```

### Connection Errors

Verify connectivity to dependencies:

```bash
# Test Milvus
curl http://localhost:19530/healthz

# Test PostgreSQL
psql -h 10.96.200.26 -U app_user -d busibox -c "SELECT 1"

# Test embedding service
curl http://10.96.200.30:8000/health
```

### High Memory Usage

The reranker model uses ~500MB RAM. Check limits:

```bash
# Current memory usage
systemctl show search-api --property=MemoryCurrent

# Memory limit
systemctl show search-api --property=MemoryMax

# Adjust limit if needed
systemctl edit search-api
# Add: MemoryMax=8G
systemctl daemon-reload
systemctl restart search-api
```

## Performance Tuning

### Reranking

Disable reranking for faster searches:

```bash
# Edit config
sed -i 's/ENABLE_RERANKING=true/ENABLE_RERANKING=false/' /opt/search/.env

# Restart
systemctl restart search-api
```

### Caching

Enable Redis caching for repeat queries:

```yaml
# Ansible vars
enable_caching: true
redis_host: 10.96.200.29  # ingest-lxc
```

Redeploy:

```bash
ansible-playbook -i inventory/production/hosts.yml site.yml --tags search_api
```

### Concurrent Requests

Adjust worker processes in systemd service:

```bash
systemctl edit search-api
```

Add workers:

```ini
[Service]
ExecStart=
ExecStart=/opt/search/venv/bin/uvicorn \
    api.main:app \
    --host 0.0.0.0 \
    --port 8003 \
    --workers 4 \
    --log-level info
```

## Updating the Service

### Update Code

```bash
cd provision/ansible

# Pull latest code from Git
git pull

# Redeploy
ansible-playbook -i inventory/production/hosts.yml site.yml --tags search_api
```

The role will:
1. Copy new source code
2. Update dependencies
3. Restart the service

### Update Dependencies

```bash
# SSH to milvus-lxc
ssh root@10.96.200.27

# Update Python packages
su - search
cd /opt/search
source venv/bin/activate
pip install --upgrade -r requirements.txt

# Restart service
exit
systemctl restart search-api
```

### Rollback

If an update causes issues:

```bash
# Check logs
journalctl -u search-api -n 100

# Rollback code
cd /path/to/busibox
git checkout <previous_commit>

# Redeploy
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml --tags search_api
```

## Migration from ingest-lxc

If you have the old search endpoint in ingest-lxc:

### 1. Deploy New Service

Deploy search-api to milvus-lxc (as described above).

### 2. Update Applications

Update applications to point to new endpoint:
- Old: `http://10.96.200.29:8002/search`
- New: `http://10.96.200.27:8003/search`

### 3. Test Parallel

Run both services in parallel during migration:

```bash
# Test old endpoint
curl http://10.96.200.29:8002/search ...

# Test new endpoint
curl http://10.96.200.27:8003/search ...

# Compare results
```

### 4. Switch Traffic

Update applications to use new endpoint.

### 5. Remove Old Service

Once verified, remove old search route from ingest-lxc.

## Reference

- **Architecture**: `docs/architecture/search-service.md`
- **Ansible Role**: `provision/ansible/roles/search_api/`
- **Source Code**: `srv/search/`
- **API Documentation**: `http://10.96.200.27:8003/docs`

## Support

For issues or questions:
1. Check logs: `journalctl -u search-api -f`
2. Test health: `curl http://10.96.200.27:8003/health`
3. Review docs: `docs/architecture/search-service.md`
4. Check troubleshooting section above

