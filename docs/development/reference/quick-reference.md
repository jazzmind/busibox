# Busibox Configuration Quick Reference

## IP Addresses

### Production (10.96.200.x)
```
Proxy:      10.96.200.200
Apps:       10.96.200.201
Agent:      10.96.200.202
PostgreSQL: 10.96.200.203
Milvus:     10.96.200.204
MinIO:      10.96.200.205
Ingest:     10.96.200.206
LiteLLM:    10.96.200.30
Ollama:     10.96.200.31
vLLM:       10.96.200.32
```

### Test (10.96.201.x)
Same pattern, just `.201` instead of `.200` in 3rd octet

## Domains

### Production
- Main: `ai.jaycashman.com`
- Agents: `agents.ai.jaycashman.com`
- Docs: `docs.ai.jaycashman.com`
- Innovation: `innovation.ai.jaycashman.com`

### Test
- Main: `test.ai.jaycashman.com`
- Agents: `agents.test.ai.jaycashman.com`
- Docs: `docs.test.ai.jaycashman.com`
- Innovation: `innovation.test.ai.jaycashman.com`

## Applications & Ports

```
agent-server:  4111  (internal only)
ai-portal:     3000  (public)
agent-manager:  3001  (public)
doc-intel:     3002  (public)
innovation:    3003  (public)
```

## Common Commands

### Vault Management
```bash
# View secrets
ansible-vault view roles/secrets/vars/vault.yml

# Edit secrets
ansible-vault edit roles/secrets/vars/vault.yml

# Encrypt new vault
ansible-vault encrypt roles/secrets/vars/vault.yml

# Decrypt vault
ansible-vault decrypt roles/secrets/vars/vault.yml
```

### Deployment
```bash
# Deploy to test
ansible-playbook -i inventory/test site.yml --ask-vault-pass

# Deploy to production
ansible-playbook -i inventory/production site.yml --ask-vault-pass

# Deploy only apps
ansible-playbook -i inventory/production deploy-apps.yml --ask-vault-pass

# Deploy only proxy
ansible-playbook -i inventory/production deploy-proxy.yml --ask-vault-pass
```

### Verification
```bash
# Check variable resolution
ansible-inventory -i inventory/production --list | grep "_ip"

# Test connectivity
ansible -i inventory/production all -m ping

# Check service status
ansible -i inventory/production apps -a "systemctl list-units --type=service --state=running | grep -E '(ai-portal|agent-manager|doc-intel|innovation)'"
ansible -i inventory/production proxy -a "systemctl status nginx"

# Syntax check
ansible-playbook --syntax-check -i inventory/production site.yml
```

## File Locations

```
Configuration:
├── inventory/{env}/group_vars/all.yml     # Main config
├── inventory/{env}/group_vars/apps.yml    # App overrides
├── inventory/{env}/group_vars/proxy.yml   # Proxy overrides
└── inventory/{env}/hosts.yml              # Host definitions

Secrets:
└── roles/secrets/vars/vault.yml           # Encrypted secrets

Documentation:
├── CONFIGURATION_GUIDE.md                 # Full guide
├── MIGRATION_CHECKLIST.md                 # Migration steps
├── REFACTORING_SUMMARY.md                 # What changed
└── QUICK_REFERENCE.md                     # This file
```

## Key Variables

### Change Network
```yaml
# inventory/{env}/group_vars/all.yml
network_base_octets: "10.96.200"  # Production
network_base_octets: "10.96.201"  # Test
```

### Change Domain
```yaml
# inventory/{env}/group_vars/all.yml
base_domain: jaycashman.com
domain: "ai.{{ base_domain }}"
```

### Add Application
```yaml
# inventory/{env}/group_vars/all.yml
applications:
  - name: new-app
    github_repo: jazzmind/new-app
    container: apps-lxc
    container_ip: "{{ apps_ip }}"
    port: 3004
    deploy_path: /srv/apps/new-app
    health_endpoint: /api/health
    routes:
      - type: subdomain
        subdomain: new
    secrets:
      - database_url
    env:
      NODE_ENV: production
```

### Add Secret
```yaml
# roles/secrets/vars/vault.yml
secrets:
  new-app:
    database_url: "postgresql://..."
```

## Troubleshooting

### Variables not resolving?
Check hierarchy: `all.yml` → `{group}.yml` → `hosts.yml`

### Secrets not working?
1. Is vault encrypted?
2. Do secret keys match app name?
3. Use hyphens not underscores

### Wrong IPs?
Check `network_base_octets` in `all.yml`

### Apps not building?
Check `build_command: "npm run build"` is set

### NGINX errors?
Check routes configuration, verify domain DNS

## Emergency Contacts

- Architecture: See `docs/architecture.md`
- Deployment: See `DEPLOYMENT_SUMMARY.md`
- Testing: See `TESTING.md`

