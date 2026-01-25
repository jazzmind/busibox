---
created: 2026-01-25
updated: 2026-01-25
status: active
category: guides
---

# Implementing Internal DNS Routing

## Overview

This guide walks through implementing internal DNS routing for Busibox services in both Docker and Proxmox deployments.

## Goals

1. **Canonical Hostnames**: All services accessible via consistent names (e.g., `postgres`, `authz-api`)
2. **Environment Agnostic**: Same configuration works in dev, staging, and production
3. **No IP Management**: Services reference each other by name, not IP address

## Implementation Steps

### Phase 1: Docker Compose (Local Development)

#### Step 1.1: Add Network Aliases

For each service in `docker-compose.local.yml`, change from:

```yaml
services:
  ingest-api:
    # ... configuration ...
    networks:
      - busibox-net
```

To:

```yaml
services:
  ingest-api:
    # ... configuration ...
    networks:
      busibox-net:
        aliases:
          - ingest-api
          - ingest
```

**Services to update**:
- `embedding-api` → aliases: `embedding-api`, `embedding`
- `ingest-api` → aliases: `ingest-api`, `ingest`
- `ingest-worker` → aliases: `ingest-worker`
- `search-api` → aliases: `search-api`, `search`
- `agent-api` → aliases: `agent-api`, `agent`
- `docs-api` → aliases: `docs-api`, `docs`
- `deploy-api` → aliases: `deploy-api`, `deploy`
- `nginx` → aliases: `nginx`, `proxy`
- `ollama` → aliases: `ollama`
- `vllm` → aliases: `vllm`
- `user-apps` → aliases: `user-apps`
- `ai-portal` → aliases: `ai-portal` (in docker-compose.dev.yml and docker-compose.prod.yml)
- `agent-manager` → aliases: `agent-manager` (in docker-compose.dev.yml and docker-compose.prod.yml)

**Already completed**:
- ✅ `postgres` → aliases: `postgres`, `pg`
- ✅ `redis` → aliases: `redis`
- ✅ `minio` → aliases: `minio`, `files`
- ✅ `milvus` → aliases: `milvus`
- ✅ `litellm` → aliases: `litellm`
- ✅ `authz-api` → aliases: `authz-api`, `authz`

#### Step 1.2: Test DNS Resolution

```bash
# Start services
cd /path/to/busibox
docker compose -f docker-compose.local.yml -f docker-compose.dev.yml up -d

# Test DNS resolution
docker exec local-ingest-api ping -c 1 postgres
docker exec local-ingest-api getent hosts authz-api
docker exec local-ingest-api getent hosts milvus
docker exec local-ingest-api getent hosts redis

# Test service connectivity
docker exec local-ingest-api curl -f http://postgres:5432 || echo "Connection refused (expected)"
docker exec local-ingest-api curl -f http://authz-api:8010/health/live
docker exec local-ingest-api curl -f http://milvus:9091/healthz
```

### Phase 2: Proxmox Deployment

#### Step 2.1: Apply Internal DNS Role

The `internal_dns` Ansible role has been created at `provision/ansible/roles/internal_dns/`.

**Apply to all containers**:

```bash
cd provision/ansible

# Production
ansible-playbook -i inventory/production/hosts.yml site.yml --tags internal_dns

# Staging
ansible-playbook -i inventory/staging/hosts.yml site.yml --tags internal_dns
```

**Apply to specific container**:

```bash
# Example: Update only ingest-lxc
ansible-playbook -i inventory/production/hosts.yml site.yml --limit ingest-lxc --tags internal_dns
```

#### Step 2.2: Verify DNS Resolution

```bash
# SSH into any container
ssh root@10.96.200.206  # ingest-lxc

# Test DNS resolution
getent hosts postgres
getent hosts authz-api
getent hosts milvus
ping -c 1 redis

# Check /etc/hosts
cat /etc/hosts | grep "Busibox Service DNS"
```

#### Step 2.3: Integrate into Service Roles

Add the `internal_dns` role as a dependency for all service roles:

```yaml
# provision/ansible/roles/ingest/meta/main.yml
dependencies:
  - role: internal_dns
```

Or add it to the main playbook before service roles:

```yaml
# provision/ansible/site.yml
- hosts: all
  roles:
    - internal_dns
    - { role: ingest, tags: [ingest] }
    - { role: agent, tags: [agent] }
    # ... other roles ...
```

### Phase 3: Update Service Configurations

#### Step 3.1: Simplify group_vars

**Before** (`inventory/production/group_vars/all/00-main.yml`):
```yaml
postgres_host: "{{ postgres_ip }}"
postgres_ip: "{{ network_base_octets }}.203"
milvus_host: "{{ milvus_ip }}"
milvus_ip: "{{ network_base_octets }}.204"
```

**After**:
```yaml
# IP addresses still needed for /etc/hosts generation
postgres_ip: "{{ network_base_octets }}.203"
milvus_ip: "{{ network_base_octets }}.204"

# Service hostnames (canonical)
postgres_host: postgres
milvus_host: milvus
redis_host: redis
minio_host: minio
authz_host: authz-api
ingest_host: ingest-api
search_api_host: search-api
agent_api_host: agent-api
litellm_host: litellm
```

#### Step 3.2: Update Service Templates

Service environment templates already use variables like `{{ postgres_host }}`, so they'll automatically use the new canonical hostnames once group_vars are updated.

**Example** (`roles/ingest/templates/ingest.env.j2`):
```bash
# Already uses variable - no change needed
POSTGRES_HOST={{ postgres_host }}
REDIS_HOST={{ redis_host }}
MILVUS_HOST={{ milvus_host }}
```

#### Step 3.3: Update Docker Environment Variables

Docker Compose files use hardcoded hostnames in many places. Update them to use canonical names:

**Before**:
```yaml
environment:
  POSTGRES_HOST: postgres  # Already correct!
  REDIS_HOST: redis        # Already correct!
  MINIO_ENDPOINT: minio:9000  # Already correct!
```

Most Docker services already use canonical hostnames, so minimal changes needed.

### Phase 4: Testing

#### Test Matrix

| Environment | Test | Command |
|-------------|------|---------|
| Docker Dev | DNS Resolution | `docker exec local-ingest-api getent hosts postgres` |
| Docker Dev | Service Connectivity | `docker exec local-ingest-api curl http://authz-api:8010/health/live` |
| Proxmox Prod | DNS Resolution | `ssh root@ingest-lxc "getent hosts postgres"` |
| Proxmox Prod | Service Connectivity | `ssh root@ingest-lxc "curl http://authz-api:8010/health/live"` |
| Proxmox Staging | DNS Resolution | `ssh root@TEST-ingest-lxc "getent hosts postgres"` |
| Proxmox Staging | Service Connectivity | `ssh root@TEST-ingest-lxc "curl http://authz-api:8010/health/live"` |

#### Integration Tests

```bash
# Run integration tests to verify services can communicate
cd provision/ansible

# Test ingest service (uses postgres, redis, milvus, authz-api)
make test-ingest

# Test search service (uses milvus, postgres, authz-api)
make test-search

# Test agent service (uses postgres, redis, authz-api, search-api, ingest-api)
make test-agent
```

### Phase 5: Cleanup (Optional)

Once all services are using canonical hostnames, you can optionally remove the `*_ip` variables from group_vars. However, keeping them is fine as they're still used for:

1. `/etc/hosts` generation in Proxmox
2. External access documentation
3. Nginx upstream configuration

## Rollback Plan

If issues arise:

### Docker
```bash
# Restore original docker-compose.local.yml
cd /path/to/busibox
git checkout docker-compose.local.yml

# Restart services
docker compose down
docker compose -f docker-compose.local.yml -f docker-compose.dev.yml up -d
```

### Proxmox
```bash
# Restore original /etc/hosts on affected container
ssh root@container-ip
cp /etc/hosts.bak /etc/hosts

# Or re-run Ansible without internal_dns role
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml --skip-tags internal_dns
```

## Troubleshooting

### Issue: DNS not resolving in Docker

**Symptom**: `ping: postgres: Name or service not known`

**Solution**:
1. Check service is on the same network:
   ```bash
   docker network inspect local-busibox-net
   ```
2. Verify network aliases:
   ```bash
   docker inspect local-postgres | grep -A 10 Networks
   ```
3. Restart services:
   ```bash
   docker compose down && docker compose up -d
   ```

### Issue: DNS not resolving in Proxmox

**Symptom**: `getent hosts postgres` returns nothing

**Solution**:
1. Check `/etc/hosts`:
   ```bash
   ssh root@container-ip "cat /etc/hosts | grep postgres"
   ```
2. Re-run internal_dns role:
   ```bash
   cd provision/ansible
   ansible-playbook -i inventory/production/hosts.yml site.yml --limit container-name --tags internal_dns
   ```

### Issue: Service can't connect despite DNS resolving

**Symptom**: DNS resolves but `curl http://postgres:5432` fails

**Solution**:
1. Check service is running:
   ```bash
   # Docker
   docker ps | grep postgres
   
   # Proxmox
   ssh root@postgres-ip "systemctl status postgresql"
   ```
2. Check firewall rules (Proxmox only):
   ```bash
   ssh root@container-ip "iptables -L"
   ```
3. Verify port is correct:
   ```bash
   # Docker
   docker port local-postgres
   
   # Proxmox
   ssh root@postgres-ip "netstat -tlnp | grep 5432"
   ```

## Benefits

After implementation:

1. **Simplified Configuration**: No more tracking IP addresses
2. **Portable**: Same config works across environments
3. **Flexible**: Easy to move services between containers
4. **Debuggable**: Clear service dependencies
5. **Consistent**: Same experience in dev, staging, and production

## Related Documentation

- `docs/architecture/internal-dns-routing.md` - Architecture overview
- `provision/ansible/roles/internal_dns/README.md` - Ansible role documentation
- `docs/troubleshooting/dns-issues.md` - DNS troubleshooting guide
