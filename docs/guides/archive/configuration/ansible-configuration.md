# Busibox Ansible Configuration Guide

**Created**: 2025-10-23  
**Purpose**: Comprehensive guide to the Busibox Ansible configuration structure

## Overview

The Busibox configuration follows a strict separation of concerns:
- **Secrets** (encrypted) are stored in `vault.yml`
- **Environment-specific configuration** is stored in `inventory/{env}/group_vars/all.yml`
- **Group-specific overrides** are stored in `inventory/{env}/group_vars/{group}.yml`

## Configuration Hierarchy

```
provision/ansible/
├── roles/secrets/vars/
│   └── vault.yml              # SECRETS ONLY (encrypted)
├── inventory/
│   ├── production/
│   │   ├── hosts.yml          # Production host definitions
│   │   └── group_vars/
│   │       ├── all.yml        # Production environment config
│   │       ├── apps.yml       # Apps group overrides
│   │       └── proxy.yml      # Proxy group overrides
│   └── test/
│       ├── hosts.yml          # Test host definitions
│       └── group_vars/
│           ├── all.yml        # Test environment config
│           ├── apps.yml       # Apps group overrides
│           └── proxy.yml      # Proxy group overrides
```

## IP Address Management

### Production Environment
- **Base IP**: `10.96.200.x`
- **Network**: `10.96.200.0/21`
- **Pattern**: `network_base_octets: "10.96.200"`

Infrastructure containers (`.200-.209`):
- Proxy: `.200`
- Apps: `.201`
- Agent: `.202`
- PostgreSQL: `.203`
- Milvus: `.204`
- MinIO: `.205`
- Ingest: `.206`

LLM Services (`.30-.39`):
- LiteLLM: `.30`
- Ollama: `.31`
- vLLM: `.32`

### Test Environment
- **Base IP**: `10.96.201.x` (Production + 1 in 3rd octet)
- **Network**: `10.96.201.0/21`
- **Pattern**: `network_base_octets: "10.96.201"`

Same offset pattern as production:
- Infrastructure: `.200-.209`
- LLM Services: `.207-.209` (sequential for simplicity in test)

## Domain Management

### Production
```yaml
base_domain: jaycashman.com
domain: "ai.{{ base_domain }}"              # ai.jaycashman.com
www_domain: "www.{{ domain }}"              # www.ai.jaycashman.com
```

### Test
```yaml
base_domain: jaycashman.com
subdomain: test
domain: "ai.{{ base_domain }}"              # ai.jaycashman.com
full_domain: "{{ subdomain }}.{{ domain }}" # test.ai.jaycashman.com
```

## Application Definitions

All applications are defined in `inventory/{env}/group_vars/all.yml` under the `applications` list.

### Configured Applications

1. **agent-server** (Internal API)
   - Port: 4111
   - Container: agent-lxc
   - Routes: Internal only
   - Secrets: database_url, minio_access_key, minio_secret_key, redis_url, jwt_secret

2. **ai-portal** (Public Web App)
   - Port: 3000
   - Container: apps-lxc
   - Routes: Domain (production: ai.jaycashman.com, test: test.ai.jaycashman.com)
   - Secrets: database_url, better_auth_secret, resend_api_key, sso_jwt_secret, litellm_api_key

3. **agent-manager** (Public Web App)
   - Port: 3001
   - Container: apps-lxc
   - Routes: Subdomain (agents.{domain}) + Path (/agents)
   - Secrets: database_url, agent_api_key, jwt_secret, session_secret

4. **doc-intel** (Public Web App)
   - Port: 3002
   - Container: apps-lxc
   - Routes: Subdomain (docs.{domain}) + Path (/docs)
   - Secrets: database_url, openai_api_key, better_auth_secret, jwt_secret

5. **innovation** (Public Web App)
   - Port: 3003
   - Container: apps-lxc
   - Routes: Subdomain (innovation.{domain}) + Path (/innovation)
   - Secrets: database_url, better_auth_secret, jwt_secret, openai_api_key

### Port Allocation Strategy

- **3000-3099**: Public web applications
- **4000-4099**: Internal API services
- **8000-8099**: LLM/AI services

## Secrets Management

### Structure
All secrets are stored in `roles/secrets/vars/vault.yml`:

```yaml
secrets:
  postgresql:
    password: "CHANGE_ME"
  
  {app-name}:
    {secret-key}: "CHANGE_ME"
```

### Application Secret References

Applications reference secrets by name in their `secrets` list:
```yaml
applications:
  - name: ai-portal
    secrets:
      - database_url
      - better_auth_secret
```

The deployment system automatically injects these as environment variables:
- `database_url` → `DATABASE_URL`
- `better_auth_secret` → `BETTER_AUTH_SECRET`

### Cross-Application Secret Sharing

JWT secrets are shared between applications for cross-app authentication:
```yaml
secrets:
  agent-server:
    jwt_secret: "SHARED_SECRET"
  
  agent-manager:
    jwt_secret: "{{ secrets.agent-server.jwt_secret }}"
  
  doc-intel:
    jwt_secret: "{{ secrets.agent-server.jwt_secret }}"
```

## Environment-Specific Differences

| Setting | Production | Test |
|---------|-----------|------|
| Network Base | 10.96.200 | 10.96.201 |
| Domain | ai.jaycashman.com | test.ai.jaycashman.com |
| SSL Mode | provisioned | selfsigned |
| NODE_ENV | production | development |
| Log Level | info | debug |
| PM2 Instances | 2 | 1 |
| PM2 Memory | 1G | 512M |
| NGINX Connections | 1024 | 512 |
| Rate Limit | 10r/s | 20r/s |
| Ollama Models | Full set (llama3, phi3, gemma) | Lightweight (gemma only) |

## Adding a New Application

1. **Add to `vault.yml`** (encrypt after editing):
   ```yaml
   secrets:
     new-app:
       database_url: "postgresql://..."
       api_key: "CHANGE_ME"
   ```

2. **Add to `inventory/{env}/group_vars/all.yml`**:
   ```yaml
   applications:
     - name: new-app
       github_repo: jazzmind/new-app
       container: apps-lxc
       container_ip: "{{ apps_ip }}"
       port: 3004
       deploy_path: /srv/apps/new-app
       health_endpoint: /api/health
       build_command: "npm run build"
       routes:
         - type: subdomain
           subdomain: new
       secrets:
         - database_url
         - api_key
       env:
         NODE_ENV: production
         PORT: "3004"
   ```

3. **Deploy**:
   ```bash
   ansible-playbook -i inventory/production deploy-apps.yml
   ```

## Changing IP Addresses

To change the IP address scheme:

1. **Update base IP in `all.yml`**:
   ```yaml
   network_base_octets: "10.96.XXX"
   ```

2. **Update container offsets if needed**:
   ```yaml
   proxy_ip: "{{ network_base_octets }}.200"
   apps_ip: "{{ network_base_octets }}.201"
   # etc.
   ```

3. **Verify `hosts.yml`** uses variable references:
   ```yaml
   proxy-lxc:
     ansible_host: "{{ proxy_ip }}"
   ```

## Changing Domains

1. **Update in `all.yml`**:
   ```yaml
   base_domain: newdomain.com
   domain: "ai.{{ base_domain }}"
   ```

2. **Update SSL certificates** if using provisioned mode

3. **Redeploy proxy**:
   ```bash
   ansible-playbook -i inventory/production deploy-proxy.yml
   ```

## Best Practices

1. **Never hardcode IPs or domains** in configuration files
2. **Always use variable references** (e.g., `{{ apps_ip }}`)
3. **Keep secrets encrypted** with `ansible-vault encrypt`
4. **Test changes** in test environment first
5. **Document** any new configuration patterns
6. **Use incremental patterns** for IP allocation
7. **Share secrets** across apps when appropriate (JWT, API keys)
8. **Version control** all configuration changes
9. **Review** `group_vars/all.yml` before deploying
10. **Validate** YAML syntax before committing

## Troubleshooting

### Variables Not Resolving
Check the variable hierarchy:
1. `inventory/{env}/group_vars/all.yml` (environment-wide)
2. `inventory/{env}/group_vars/{group}.yml` (group-specific)
3. `inventory/{env}/hosts.yml` (host-specific)

### Secrets Not Available
1. Verify `vault.yml` is encrypted
2. Check vault password file location
3. Ensure secret keys match application names (use hyphens, not underscores)

### IP Address Conflicts
1. Verify `network_base_octets` is unique per environment
2. Check container ID assignments
3. Ensure no overlap between infrastructure and LLM services

## Migration from Old Structure

If migrating from the old hardcoded configuration:

1. ✅ Move all secrets to `vault.yml`
2. ✅ Replace hardcoded IPs with `{{ variable }}` references
3. ✅ Replace hardcoded domains with `{{ domain }}` references
4. ✅ Consolidate application definitions in `all.yml`
5. ✅ Remove duplicate configuration from group files
6. ✅ Test in test environment
7. ✅ Deploy to production

## Files Modified (2025-10-23)

- `roles/secrets/vars/vault.example.yml` - Secrets only, no env-specific config
- `inventory/production/group_vars/all.yml` - Calculated IPs, all 5 apps defined
- `inventory/production/group_vars/apps.yml` - Minimal overrides only
- `inventory/production/group_vars/proxy.yml` - Created, minimal overrides
- `inventory/production/hosts.yml` - Uses variable references
- `inventory/test/group_vars/all.yml` - Calculated IPs, all 5 apps defined
- `inventory/test/group_vars/apps.yml` - Minimal overrides only
- `inventory/test/group_vars/proxy.yml` - Minimal overrides only
- `inventory/test/hosts.yml` - Uses variable references

## Support

For questions or issues:
1. Review this guide
2. Check `specs/002-deploy-app-servers/data-model.md` for schema reference
3. Review `DEPLOYMENT_SUMMARY.md` for deployment procedures
4. Contact the infrastructure team

