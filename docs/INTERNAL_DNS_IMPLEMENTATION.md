---
created: 2026-01-25
status: in-progress
---

# Internal DNS Routing Implementation

## Summary

This document tracks the implementation of internal DNS routing for Busibox services across Docker and Proxmox deployments. The goal is to enable all services to reference each other via canonical hostnames (e.g., `postgres`, `authz-api`) instead of IP addresses or environment-specific container names.

## Benefits

1. **Consistency**: Same hostnames across dev, staging, and production
2. **Simplicity**: No need to track IP addresses in configuration
3. **Flexibility**: Easy to move services between containers
4. **Portability**: Configuration works across all environments
5. **Debugging**: Clear service dependencies

## Implementation Status

### ✅ Completed

1. **Documentation**
   - [x] Architecture overview: `docs/architecture/internal-dns-routing.md`
   - [x] Implementation guide: `docs/guides/implementing-internal-dns.md`
   - [x] Ansible role README: `provision/ansible/roles/internal_dns/README.md`

2. **Ansible Role (Proxmox)**
   - [x] Created `provision/ansible/roles/internal_dns/`
   - [x] Task definitions in `tasks/main.yml`
   - [x] `/etc/hosts` template in `templates/hosts.j2`
   - [x] Default variables in `defaults/main.yml`
   - [x] Integrated into `site.yml` (runs on all hosts with `always` tag)

3. **Docker Compose (Partial)**
   - [x] Added network aliases for infrastructure services:
     - postgres (aliases: postgres, pg)
     - redis (aliases: redis)
     - minio (aliases: minio, files)
     - milvus (aliases: milvus)
     - litellm (aliases: litellm)
     - authz-api (aliases: authz-api, authz)

### 🔄 In Progress

1. **Docker Compose - Remaining Services**
   - [ ] embedding-api (aliases: embedding-api, embedding)
   - [ ] ingest-api (aliases: ingest-api, ingest)
   - [ ] ingest-worker (aliases: ingest-worker)
   - [ ] search-api (aliases: search-api, search)
   - [ ] agent-api (aliases: agent-api, agent)
   - [ ] docs-api (aliases: docs-api, docs)
   - [ ] deploy-api (aliases: deploy-api, deploy)
   - [ ] nginx (aliases: nginx, proxy)
   - [ ] ollama (aliases: ollama)
   - [ ] vllm (aliases: vllm)
   - [ ] user-apps (aliases: user-apps)

2. **Docker Compose Overlay Files**
   - [ ] docker-compose.dev.yml:
     - ai-portal (aliases: ai-portal)
     - agent-manager (aliases: agent-manager)
   - [ ] docker-compose.prod.yml:
     - ai-portal (aliases: ai-portal)
     - agent-manager (aliases: agent-manager)

### 📋 Pending

1. **Testing**
   - [ ] Test DNS resolution in Docker (all services)
   - [ ] Test DNS resolution in Proxmox production
   - [ ] Test DNS resolution in Proxmox staging
   - [ ] Run integration tests to verify service connectivity
   - [ ] Document test results

2. **Configuration Updates**
   - [ ] Simplify group_vars to use canonical hostnames
   - [ ] Verify all service templates use hostname variables
   - [ ] Update any hardcoded IPs in service configurations

3. **Deployment**
   - [ ] Deploy internal_dns role to production Proxmox
   - [ ] Deploy internal_dns role to staging Proxmox
   - [ ] Verify all services can resolve each other
   - [ ] Monitor for any DNS-related issues

## Quick Reference

### Canonical Service Names

| Service | Hostname | Aliases |
|---------|----------|---------|
| PostgreSQL | `postgres` | `pg` |
| Redis | `redis` | - |
| MinIO | `minio` | `files` |
| Milvus | `milvus` | - |
| Nginx | `nginx` | `proxy` |
| AuthZ API | `authz-api` | `authz` |
| Ingest API | `ingest-api` | `ingest` |
| Search API | `search-api` | `search` |
| Agent API | `agent-api` | `agent` |
| LiteLLM | `litellm` | - |
| vLLM | `vllm` | - |
| Ollama | `ollama` | - |
| Docs API | `docs-api` | `docs` |
| Deploy API | `deploy-api` | `deploy` |
| Embedding API | `embedding-api` | `embedding` |
| AI Portal | `ai-portal` | - |
| Agent Manager | `agent-manager` | - |

### Commands

**Apply DNS configuration to Proxmox**:
```bash
cd provision/ansible

# All containers (production)
ansible-playbook -i inventory/production/hosts.yml site.yml --tags internal_dns

# All containers (staging)
ansible-playbook -i inventory/staging/hosts.yml site.yml --tags internal_dns

# Specific container
ansible-playbook -i inventory/production/hosts.yml site.yml --limit ingest-lxc --tags internal_dns
```

**Test DNS resolution**:
```bash
# Docker
docker exec local-ingest-api getent hosts postgres
docker exec local-ingest-api ping -c 1 authz-api

# Proxmox
ssh root@ingest-lxc "getent hosts postgres"
ssh root@ingest-lxc "ping -c 1 authz-api"
```

**Verify /etc/hosts**:
```bash
# Proxmox
ssh root@ingest-lxc "cat /etc/hosts | grep 'Busibox Service DNS'"
```

## Next Steps

1. **Complete Docker Compose aliases** (see "In Progress" section above)
2. **Test in Docker**: Start services and verify DNS resolution
3. **Deploy to Proxmox staging**: Apply internal_dns role and test
4. **Deploy to Proxmox production**: Apply internal_dns role and test
5. **Update configurations**: Simplify group_vars to use canonical hostnames
6. **Run integration tests**: Verify all services can communicate
7. **Document lessons learned**: Update troubleshooting guide

## Related Files

- `docs/architecture/internal-dns-routing.md` - Architecture overview
- `docs/guides/implementing-internal-dns.md` - Step-by-step implementation guide
- `provision/ansible/roles/internal_dns/` - Ansible role for Proxmox
- `provision/ansible/site.yml` - Playbook integration (internal_dns runs first)
- `docker-compose.local.yml` - Docker Compose with network aliases (partial)
- `docker-compose.dev.yml` - Development overlay (needs aliases)
- `docker-compose.prod.yml` - Production overlay (needs aliases)

## Notes

- The `internal_dns` role uses the `always` tag in site.yml, so it runs on every deployment
- Docker's built-in DNS automatically resolves service names to container IPs
- Proxmox uses `/etc/hosts` for DNS resolution (no external DNS server needed)
- IP addresses are still tracked in group_vars for `/etc/hosts` generation and documentation
- Services can use either the primary hostname or aliases (e.g., `postgres` or `pg`)
