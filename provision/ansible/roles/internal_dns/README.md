# Internal DNS Role

## Purpose

Configures `/etc/hosts` on all Busibox containers to enable service discovery via canonical hostnames instead of IP addresses.

## What It Does

1. Backs up existing `/etc/hosts`
2. Deploys templated `/etc/hosts` with all service mappings
3. Verifies DNS resolution for core services

## Service Mappings

All services are accessible via canonical hostnames:

- `postgres` → PostgreSQL database
- `redis` → Redis (embedded in ingest-lxc)
- `minio` → MinIO object storage
- `milvus` → Milvus vector database
- `nginx` → Nginx reverse proxy
- `authz-api` → Authentication/Authorization API
- `ingest-api` → Document ingestion API
- `search-api` → Semantic search API (embedded in milvus-lxc)
- `agent-api` → AI agent API
- `litellm` → LiteLLM gateway
- `vllm` → vLLM inference
- `ollama` → Ollama inference
- `docs-api` → Documentation API (embedded in agent-lxc)
- `deploy-api` → Deployment API (embedded in authz-lxc)
- `embedding-api` → Embedding API (embedded in ingest-lxc)
- `ai-portal` → AI Portal frontend
- `agent-manager` → Agent Manager frontend

## Usage

### Apply to All Containers

```bash
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml --tags internal_dns
```

### Apply to Specific Container

```bash
ansible-playbook -i inventory/production/hosts.yml site.yml --limit pg-lxc --tags internal_dns
```

### Verify DNS Resolution

```bash
ansible-playbook -i inventory/production/hosts.yml site.yml --tags internal_dns,verify
```

## Integration

This role should be included in all service roles to ensure DNS is configured before services start:

```yaml
# roles/myservice/meta/main.yml
dependencies:
  - role: internal_dns
```

Or explicitly in playbooks:

```yaml
# site.yml
- hosts: all
  roles:
    - internal_dns
    - myservice
```

## Environment Support

- **Production**: Uses production IPs (10.96.200.x)
- **Staging**: Uses staging IPs (10.96.201.x) with TEST- aliases
- **Cross-Environment**: Supports staging → production vLLM access

## Testing

```bash
# On any container
getent hosts postgres
getent hosts authz-api
ping -c 1 milvus

# Check /etc/hosts
cat /etc/hosts | grep postgres
```

## Troubleshooting

### DNS Not Resolving

1. Check if role was applied:
   ```bash
   ssh root@container-ip "grep 'Managed by Ansible' /etc/hosts"
   ```

2. Re-run the role:
   ```bash
   ansible-playbook -i inventory/production/hosts.yml site.yml --limit container-name --tags internal_dns
   ```

3. Verify IP addresses match group_vars:
   ```bash
   ansible-playbook -i inventory/production/hosts.yml debug.yml -e "var=postgres_ip"
   ```

### Manual Override

If you need to manually edit `/etc/hosts`, the backup is at `/etc/hosts.bak`.

To prevent Ansible from overwriting:
```yaml
# In your playbook or role
- name: Skip internal_dns role
  tags: [never, internal_dns]
```

## Related Documentation

- `docs/architecture/internal-dns-routing.md` - Architecture overview
- `docs/configuration/service-discovery.md` - Service discovery patterns
