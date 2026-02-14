---
title: "Internal DNS Routing"
category: "developer"
order: 22
description: "Internal DNS routing strategy for service discovery across Docker and Proxmox"
published: true
---

# Internal DNS Routing

## Overview

This document describes the internal DNS routing strategy for Busibox services across both Docker and Proxmox deployments. The goal is to provide consistent service discovery using canonical hostnames regardless of the deployment environment.

## Canonical Service Names

All services are accessible via consistent hostnames across all environments:

| Service | Canonical Hostname | Docker Container | Proxmox Container |
|---------|-------------------|------------------|-------------------|
| PostgreSQL | `postgres` | `local-postgres` | `pg-lxc` / `TEST-pg-lxc` |
| Redis | `redis` | `local-redis` | `ingest-lxc` (embedded) |
| MinIO | `minio` | `local-minio` | `files-lxc` |
| Milvus | `milvus` | `local-milvus` | `milvus-lxc` |
| Nginx | `nginx` | `local-nginx` | `proxy-lxc` |
| AuthZ API | `authz-api` | `local-authz-api` | `authz-lxc` |
| Ingest API | `ingest-api` | `local-ingest-api` | `ingest-lxc` |
| Search API | `search-api` | `local-search-api` | `milvus-lxc` (embedded) |
| Agent API | `agent-api` | `local-agent-api` | `agent-lxc` |
| LiteLLM | `litellm` | `local-litellm` | `litellm-lxc` |
| vLLM | `vllm` | `local-vllm` | `vllm-lxc` |
| Ollama | `ollama` | `local-ollama` | `ollama-lxc` |
| Docs API | `docs-api` | `local-docs-api` | `milvus-lxc` (embedded) |
| Deploy API | `deploy-api` | `local-deploy-api` | `authz-lxc` (embedded) |
| Embedding API | `embedding-api` | `local-embedding-api` | `ingest-lxc` (embedded) |
| AI Portal | `ai-portal` | `local-ai-portal` | `core-apps-lxc` |
| Agent Manager | `agent-manager` | `local-agent-manager` | `core-apps-lxc` |

## Implementation

### Docker Deployment

Docker uses its built-in DNS server which automatically resolves service names to container IPs. We configure this via:

1. **Service Hostname**: Set `hostname:` in docker-compose.yml
2. **Network Aliases**: Add aliases to the `busibox-net` network
3. **Container Name**: Use descriptive names with environment prefix

**Example**:
```yaml
services:
  postgres:
    container_name: ${CONTAINER_PREFIX:-local}-postgres
    hostname: postgres
    networks:
      busibox-net:
        aliases:
          - postgres
          - pg
```

**Benefits**:
- Automatic DNS resolution within Docker network
- No manual configuration needed
- Works across all Docker Compose modes (dev/prod)

### Proxmox Deployment

Proxmox LXC containers don't have built-in DNS, so we use `/etc/hosts` entries managed by Ansible.

**Implementation**:
1. Create an Ansible role: `roles/internal_dns/`
2. Template `/etc/hosts` with all service mappings
3. Apply to all containers during provisioning

**Example `/etc/hosts`**:
```
# Busibox Internal DNS - Production
10.96.200.203 postgres pg pg-lxc
10.96.200.206 redis ingest-lxc
10.96.200.205 minio files files-lxc
10.96.200.204 milvus milvus-lxc
10.96.200.200 nginx proxy proxy-lxc
10.96.200.210 authz-api authz authz-lxc
10.96.200.206 ingest-api ingest ingest-lxc
10.96.200.204 search-api search milvus-lxc
10.96.200.202 agent-api agent agent-lxc
10.96.200.207 litellm litellm-lxc
10.96.200.208 vllm vllm-lxc
10.96.200.209 ollama ollama-lxc
10.96.200.204 docs-api docs milvus-lxc
10.96.200.210 deploy-api deploy authz-lxc
10.96.200.206 embedding-api embedding ingest-lxc
10.96.200.201 ai-portal core-apps-lxc
10.96.200.201 agent-manager core-apps-lxc
```

**Benefits**:
- Works with any network configuration
- No external DNS server needed
- Easy to debug and verify

## Migration Strategy

### Phase 1: Add Aliases (Non-Breaking)
1. Add canonical hostnames as aliases in Docker
2. Add `/etc/hosts` entries in Proxmox
3. Services can use either old (IP-based) or new (hostname-based) references

### Phase 2: Update Service Configurations
1. Update Ansible templates to use canonical hostnames
2. Update Docker environment variables
3. Test each service individually

### Phase 3: Remove IP Variables (Cleanup)
1. Remove `*_ip` variables from group_vars
2. Remove `*_host` variables (keep only for external access if needed)
3. Simplify configuration files

## Configuration Examples

### Before (IP-based)
```yaml
# group_vars/all.yml
postgres_host: "{{ postgres_ip }}"
postgres_ip: "{{ network_base_octets }}.203"

# service.env.j2
POSTGRES_HOST={{ postgres_host }}
```

### After (DNS-based)
```yaml
# group_vars/all.yml
postgres_host: postgres

# service.env.j2
POSTGRES_HOST={{ postgres_host }}
```

## Testing

### Docker
```bash
# Test DNS resolution
docker exec local-ingest-api ping -c 1 postgres
docker exec local-ingest-api getent hosts postgres

# Test service connectivity
docker exec local-ingest-api curl http://postgres:5432
```

### Proxmox
```bash
# Test DNS resolution
ssh root@10.96.200.206 "ping -c 1 postgres"
ssh root@10.96.200.206 "getent hosts postgres"

# Test service connectivity
ssh root@10.96.200.206 "curl http://postgres:5432"
```

## Troubleshooting

### Docker Issues

**Problem**: Service name not resolving
```bash
# Check if service is on the same network
docker network inspect local-busibox-net

# Check container hostname
docker inspect local-postgres | grep Hostname

# Check network aliases
docker inspect local-postgres | grep -A 10 Networks
```

**Solution**: Ensure service is connected to `busibox-net` with proper aliases

### Proxmox Issues

**Problem**: Hostname not resolving
```bash
# Check /etc/hosts
ssh root@container-ip "cat /etc/hosts"

# Check if entry exists
ssh root@container-ip "grep postgres /etc/hosts"
```

**Solution**: Re-run Ansible role to update `/etc/hosts`

## Benefits

1. **Consistency**: Same hostnames across all environments
2. **Simplicity**: No need to track IP addresses
3. **Flexibility**: Easy to move services between containers
4. **Portability**: Configuration works in dev, staging, and production
5. **Debugging**: Easier to understand service dependencies

## Future Enhancements

1. **mDNS/Avahi**: Consider using mDNS for automatic service discovery
2. **Consul**: For more complex service mesh scenarios
3. **CoreDNS**: For advanced DNS features (SRV records, etc.)
