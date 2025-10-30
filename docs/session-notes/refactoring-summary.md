# Ansible Configuration Refactoring Summary

**Date**: 2025-10-23  
**Branch**: 002-deploy-app-servers  
**Status**: Ready for Testing

## What Changed

### Configuration Structure Improvements

#### 1. **Separation of Concerns**
- **Before**: Secrets, IPs, and domains mixed together in group_vars
- **After**: 
  - Secrets ONLY in `vault.yml`
  - Environment config in `inventory/{env}/group_vars/all.yml`
  - Group overrides in `inventory/{env}/group_vars/{group}.yml`

#### 2. **Dynamic IP Calculation**
- **Before**: Hardcoded IPs like `10.96.200.200`, `10.96.201.200`
- **After**: 
  ```yaml
  network_base_octets: "10.96.200"  # or "10.96.201" for test
  proxy_ip: "{{ network_base_octets }}.200"
  ```
  - Change one variable to change entire network
  - Easy to create new environments

#### 3. **Dynamic Domain Configuration**
- **Before**: Hardcoded domains like `ai.jaycashman.com`
- **After**:
  ```yaml
  base_domain: jaycashman.com
  domain: "ai.{{ base_domain }}"
  ```
  - Change base domain to rebrand entire system

#### 4. **Complete Application Definitions**
- **Before**: Only partial app configurations
- **After**: All 5 Node.js applications fully defined:
  1. `agent-server` (internal API)
  2. `ai-portal` (main web app)
  3. `agent-client` (agent UI)
  4. `doc-intel` (document intelligence)
  5. `innovation` (innovation portal)

## Files Modified

### Created
- `CONFIGURATION_GUIDE.md` - Comprehensive configuration documentation
- `MIGRATION_CHECKLIST.md` - Step-by-step migration guide
- `REFACTORING_SUMMARY.md` - This file
- `inventory/production/group_vars/proxy.yml` - Proxy-specific overrides

### Updated
- `roles/secrets/vars/vault.example.yml` - All 5 apps, secrets only
- `inventory/production/group_vars/all.yml` - Calculated IPs, all apps
- `inventory/production/group_vars/apps.yml` - Minimal overrides
- `inventory/production/hosts.yml` - Variable references
- `inventory/test/group_vars/all.yml` - Calculated IPs, all apps
- `inventory/test/group_vars/apps.yml` - Minimal overrides
- `inventory/test/group_vars/proxy.yml` - Minimal overrides
- `inventory/test/hosts.yml` - Variable references

## Key Benefits

### 1. **Maintainability**
- Single point of change for network configuration
- Single point of change for domain configuration
- Clear separation between secrets and configuration

### 2. **Scalability**
- Easy to add new environments (staging, qa, etc.)
- Easy to add new applications
- Easy to change IP schemes

### 3. **Security**
- Secrets isolated in encrypted vault
- No secrets in version control
- Clear audit trail for secret changes

### 4. **Consistency**
- Same structure for test and production
- Same patterns for all applications
- Predictable configuration

## Application Routing

### Production (ai.jaycashman.com)

| Application | Domain | Path | Port |
|------------|--------|------|------|
| ai-portal | ai.jaycashman.com | / | 3000 |
| ai-portal | www.ai.jaycashman.com | / | 3000 |
| agent-client | agents.ai.jaycashman.com | / | 3001 |
| agent-client | ai.jaycashman.com | /agents | 3001 |
| doc-intel | docs.ai.jaycashman.com | / | 3002 |
| doc-intel | ai.jaycashman.com | /docs | 3002 |
| innovation | innovation.ai.jaycashman.com | / | 3003 |
| innovation | ai.jaycashman.com | /innovation | 3003 |
| agent-server | (internal only) | - | 4111 |

### Test (test.ai.jaycashman.com)

| Application | Domain | Port |
|------------|--------|------|
| ai-portal | test.ai.jaycashman.com | 3000 |
| agent-client | agents.test.ai.jaycashman.com | 3001 |
| doc-intel | docs.test.ai.jaycashman.com | 3002 |
| innovation | innovation.test.ai.jaycashman.com | 3003 |
| agent-server | (internal only) | 4111 |

## IP Address Allocation

### Production (10.96.200.x)

| Service | IP | Container ID |
|---------|-----|--------------|
| Proxy | 10.96.200.200 | 200 |
| Apps | 10.96.200.201 | 201 |
| Agent | 10.96.200.202 | 202 |
| PostgreSQL | 10.96.200.203 | 203 |
| Milvus | 10.96.200.204 | 204 |
| MinIO | 10.96.200.205 | 205 |
| Ingest | 10.96.200.206 | 206 |
| LiteLLM | 10.96.200.30 | 230 |
| Ollama | 10.96.200.31 | 231 |
| vLLM | 10.96.200.32 | 232 |

### Test (10.96.201.x)

| Service | IP | Container ID |
|---------|-----|--------------|
| Proxy | 10.96.201.200 | 300 |
| Apps | 10.96.201.201 | 301 |
| Agent | 10.96.201.202 | 302 |
| PostgreSQL | 10.96.201.203 | 303 |
| Milvus | 10.96.201.204 | 304 |
| MinIO | 10.96.201.205 | 305 |
| Ingest | 10.96.201.206 | 306 |
| LiteLLM | 10.96.201.207 | 307 |
| Ollama | 10.96.201.208 | 308 |
| vLLM | 10.96.201.209 | 309 |

## Secret Structure

All secrets follow this pattern in `vault.yml`:

```yaml
secrets:
  postgresql:
    password: "..."
  
  app-name:
    secret-key-1: "..."
    secret-key-2: "..."
```

Applications reference secrets by adding to their `secrets` list:

```yaml
applications:
  - name: app-name
    secrets:
      - secret-key-1
      - secret-key-2
```

The deployment system automatically injects as uppercase environment variables:
- `secret-key-1` → `SECRET_KEY_1`
- `database_url` → `DATABASE_URL`

## Environment Variables

### Shared Across All Apps
- `NODE_ENV`: production (prod) / development (test)
- `LOG_LEVEL`: info (prod) / debug (test)
- `PORT`: Application-specific

### LiteLLM Integration
All web apps that need AI:
```yaml
LITELLM_BASE_URL: "http://{{ litellm_ip }}:{{ litellm_port }}/v1"
```

### Database URLs
Automatically constructed in vault.yml:
```yaml
database_url: "postgresql://{{ postgres_user }}:{{ secrets.postgresql.password }}@{{ postgres_host }}:{{ postgres_port }}/{{ db_name }}"
```

### Cross-App Authentication
JWT secrets are shared for SSO:
```yaml
secrets:
  agent-server:
    jwt_secret: "MASTER_SECRET"
  
  ai-portal:
    jwt_secret: "{{ secrets.agent-server.jwt_secret }}"  # Shared
  
  agent-client:
    jwt_secret: "{{ secrets.agent-server.jwt_secret }}"  # Shared
```

## Next Steps

1. **Review Documentation**
   - Read `CONFIGURATION_GUIDE.md`
   - Review `MIGRATION_CHECKLIST.md`

2. **Update Vault File**
   - Copy `vault.example.yml` to `vault.yml`
   - Add real secrets
   - Encrypt with `ansible-vault encrypt`

3. **Test Deployment**
   - Deploy to test environment
   - Verify all 5 applications work
   - Check routing and secrets

4. **Production Deployment**
   - Deploy to production
   - Verify all applications
   - Monitor for issues

## Testing

### Variable Resolution Test
```bash
# Should show calculated IPs
ansible-inventory -i inventory/production --list | grep "_ip"
ansible-inventory -i inventory/test --list | grep "_ip"
```

### Application Configuration Test
```bash
# Should show all 5 applications
ansible-inventory -i inventory/production --list | grep -A 5 "applications"
```

### Syntax Validation
```bash
# Should pass without errors
ansible-playbook --syntax-check -i inventory/production site.yml
ansible-playbook --syntax-check -i inventory/test site.yml
```

## Rollback Plan

If needed, rollback is simple:
```bash
git reset --hard <previous-commit>
ansible-playbook -i inventory/production site.yml --ask-vault-pass
```

## Questions?

- Configuration: See `CONFIGURATION_GUIDE.md`
- Migration: See `MIGRATION_CHECKLIST.md`
- Schema: See `specs/002-deploy-app-servers/data-model.md`
- Deployment: See `DEPLOYMENT_SUMMARY.md`

## Rules Applied

- `innovation/002-project`: Architecture and planning requirements
- `innovation/400-md`: Documentation standards

## Credits

**Architect**: Claude (Cursor AI)  
**Date**: 2025-10-23  
**Approach**: Clean separation of concerns, calculated values, DRY principle

