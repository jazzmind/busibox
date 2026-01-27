---
created: 2026-01-25
updated: 2026-01-25
status: active
category: reference
---

# Service Hostnames Reference

## Overview

All Busibox services are accessible via canonical hostnames that work consistently across Docker and Proxmox deployments. Use these hostnames in your service configurations instead of IP addresses or environment-specific container names.

## Service Hostname Table

### Infrastructure Services

| Service | Hostname | Port | Aliases | Description |
|---------|----------|------|---------|-------------|
| PostgreSQL | `postgres` | 5432 | `pg` | Primary database |
| Redis | `redis` | 6379 | - | Cache and job queue |
| MinIO | `minio` | 9000, 9001 | `files` | S3-compatible object storage |
| Milvus | `milvus` | 19530, 9091 | - | Vector database |
| Nginx | `nginx` | 80, 443 | `proxy` | Reverse proxy |

### API Services

| Service | Hostname | Port | Aliases | Description |
|---------|----------|------|---------|-------------|
| AuthZ API | `authz-api` | 8010 | `authz` | Authentication & authorization |
| Ingest API | `ingest-api` | 8002 | `ingest` | Document ingestion |
| Search API | `search-api` | 8003 | `search` | Semantic search |
| Agent API | `agent-api` | 8000 | `agent` | AI agents |
| Docs API | `docs-api` | 8004 | `docs` | Documentation & OpenAPI specs |
| Deploy API | `deploy-api` | 8011 | `deploy` | App deployment management |
| Embedding API | `embedding-api` | 8005 | `embedding` | Text embeddings |

### LLM Services

| Service | Hostname | Port | Aliases | Description |
|---------|----------|------|---------|-------------|
| LiteLLM | `litellm` | 4000 | - | LLM gateway/proxy |
| vLLM | `vllm` | 8000-8005 | - | GPU inference |
| Ollama | `ollama` | 11434 | - | Local LLM inference |

### Application Services

| Service | Hostname | Port | Aliases | Description |
|---------|----------|------|---------|-------------|
| AI Portal | `ai-portal` | 3000 | - | Main web interface |
| Agent Manager | `agent-manager` | 3001 | - | Agent management UI |
| User Apps | `user-apps` | varies | - | External/user applications |

### Host Services (Apple Silicon Only)

These services run on the host machine (not in containers) and are accessible from Docker via `host.docker.internal`:

| Service | Hostname | Port | Description |
|---------|----------|------|-------------|
| Host Agent | `host.docker.internal` | 8089 | MLX control bridge for Docker containers |
| MLX-LM | `host.docker.internal` | 8080 | Local LLM inference for Apple Silicon |

## Usage Examples

### Python (FastAPI/Flask)

```python
# Database connection
DATABASE_URL = "postgresql://user:pass@postgres:5432/dbname"

# Redis connection
REDIS_URL = "redis://redis:6379/0"

# MinIO client
minio_client = Minio(
    "minio:9000",
    access_key="...",
    secret_key="...",
    secure=False
)

# API calls
authz_response = requests.get("http://authz-api:8010/health")
search_response = requests.post("http://search-api:8003/search", json={...})
```

### JavaScript/TypeScript (Next.js)

```typescript
// Environment variables (server-side)
const DATABASE_URL = "postgresql://user:pass@postgres:5432/dbname";
const AUTHZ_BASE_URL = "http://authz-api:8010";
const AGENT_API_URL = "http://agent-api:8000";

// Fetch from API
const response = await fetch("http://ingest-api:8002/files");
```

### Docker Compose

```yaml
services:
  myservice:
    environment:
      POSTGRES_HOST: postgres
      POSTGRES_PORT: 5432
      REDIS_HOST: redis
      REDIS_PORT: 6379
      MILVUS_HOST: milvus
      MILVUS_PORT: 19530
      AUTHZ_URL: http://authz-api:8010
      SEARCH_API_URL: http://search-api:8003
```

### Ansible Templates

```jinja2
# service.env.j2
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
REDIS_HOST=redis
MILVUS_HOST=milvus
AUTHZ_BASE_URL=http://authz-api:8010
INGEST_API_URL=http://ingest-api:8002
SEARCH_API_URL=http://search-api:8003
AGENT_API_URL=http://agent-api:8000
LITELLM_BASE_URL=http://litellm:4000
```

### Nginx Configuration

```nginx
# Upstream definitions
upstream authz_api {
    server authz-api:8010;
}

upstream ingest_api {
    server ingest-api:8002;
}

upstream search_api {
    server search-api:8003;
}

# Proxy pass
location /api/authz/ {
    proxy_pass http://authz-api:8010/;
}
```

## Environment-Specific Notes

### Docker (Local Development)

- Hostnames resolve automatically via Docker's built-in DNS
- All services must be on the same Docker network (`busibox-net`)
- Container names can be different (e.g., `local-postgres`, `dev-postgres`)
- Hostnames remain consistent (always `postgres`)

### Proxmox (Production/Staging)

- Hostnames resolve via `/etc/hosts` managed by Ansible
- The `internal_dns` role configures `/etc/hosts` on all containers
- Container names vary by environment (e.g., `pg-lxc`, `TEST-pg-lxc`)
- Hostnames remain consistent (always `postgres`)

## Testing DNS Resolution

### Docker

```bash
# Test from any container
docker exec local-ingest-api ping -c 1 postgres
docker exec local-ingest-api getent hosts authz-api
docker exec local-ingest-api curl http://milvus:9091/healthz
```

### Proxmox

```bash
# SSH into any container
ssh root@ingest-lxc

# Test DNS resolution
ping -c 1 postgres
getent hosts authz-api
curl http://milvus:9091/healthz
```

## Troubleshooting

### DNS Not Resolving

**Docker**:
1. Verify service is on `busibox-net` network
2. Check network aliases: `docker inspect <container> | grep -A 10 Networks`
3. Restart services: `docker compose restart`

**Proxmox**:
1. Check `/etc/hosts`: `cat /etc/hosts | grep postgres`
2. Re-run Ansible role: `ansible-playbook site.yml --tags internal_dns`
3. Verify IP addresses match: `getent hosts postgres`

### Service Not Responding

1. Verify service is running:
   - Docker: `docker ps | grep postgres`
   - Proxmox: `systemctl status postgresql`
2. Check port is listening:
   - `netstat -tlnp | grep 5432`
3. Test connectivity:
   - `curl http://authz-api:8010/health`

## Best Practices

1. **Always use hostnames**, never hardcode IP addresses
2. **Use primary hostname** (e.g., `postgres`) for clarity
3. **Use aliases** (e.g., `pg`) only for brevity in scripts
4. **Include port numbers** explicitly in URLs (e.g., `postgres:5432`)
5. **Test DNS resolution** after deployment changes

## Related Documentation

- `docs/architecture/internal-dns-routing.md` - Architecture overview
- `docs/guides/implementing-internal-dns.md` - Implementation guide
- `provision/ansible/roles/internal_dns/README.md` - Ansible role docs
- `INTERNAL_DNS_IMPLEMENTATION.md` - Implementation status

## Updates

To update this reference when adding new services:

1. Add service to the appropriate table above
2. Update the Ansible template: `provision/ansible/roles/internal_dns/templates/hosts.j2`
3. Add network aliases to Docker Compose files
4. Test DNS resolution in all environments
