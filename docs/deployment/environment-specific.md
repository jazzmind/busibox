# Multi-Deployment Configuration

**Important**: This repository is a **generic, reusable infrastructure template** designed to be deployed across multiple customers/environments.

## Architecture

### Generic (Version Controlled)
- Infrastructure patterns
- Application definitions structure
- Deployment playbooks
- Role definitions
- Calculated IP patterns
- `vault.example.yml` - Template only

### Deployment-Specific (NOT Version Controlled)
- `vault.yml` - Contains:
  - Network base octets (production and test)
  - Domain names
  - Secrets (passwords, API keys)
  - SSL email

## File Structure

```
busibox/
├── provision/ansible/
│   ├── inventory/
│   │   ├── production/
│   │   │   ├── hosts.yml           ✅ Generic (uses vault vars)
│   │   │   └── group_vars/
│   │   │       ├── all.yml         ✅ Generic (references vault vars)
│   │   │       ├── apps.yml        ✅ Generic
│   │   │       └── proxy.yml       ✅ Generic
│   │   └── test/
│   │       ├── hosts.yml           ✅ Generic (uses vault vars)
│   │       └── group_vars/
│   │           ├── all.yml         ✅ Generic (references vault vars)
│   │           ├── apps.yml        ✅ Generic
│   │           └── proxy.yml       ✅ Generic
│   └── roles/secrets/vars/
│       ├── vault.example.yml       ✅ Generic (template)
│       └── vault.yml               ❌ Deployment-specific (gitignored)
```

## Per-Deployment Setup

### 1. Clone Repository
```bash
git clone https://github.com/yourorg/busibox.git deployment-customer1
cd deployment-customer1
```

### 2. Create Deployment Vault
```bash
cd provision/ansible

# Copy template
cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml

# Edit with deployment-specific values
ansible-vault edit roles/secrets/vars/vault.yml
```

### 3. Configure Deployment Values

Edit `vault.yml` with deployment-specific values:

```yaml
# Network Configuration
network_base_octets_production: "10.96.200"  # Customer's production network
network_base_octets_test: "10.96.201"        # Customer's test network

# Domain Configuration
base_domain: "customer1.com"                  # Customer's domain
ssl_email: "admin@customer1.com"              # Customer's SSL email

# Secrets
secrets:
  postgresql:
    password: "customer1_db_password"
  ai-portal:
    better_auth_secret: "customer1_auth_secret"
    resend_api_key: "customer1_resend_key"
  # ... etc
```

### 4. Deploy
```bash
# Deploy to production
ansible-playbook -i inventory/production site.yml --ask-vault-pass

# Deploy to test
ansible-playbook -i inventory/test site.yml --ask-vault-pass
```

## Multiple Deployments Example

### Customer 1 (Cashman)
```bash
/deployments/cashman/
└── provision/ansible/roles/secrets/vars/vault.yml
    network_base_octets_production: "10.96.200"
    base_domain: "jaycashman.com"
```

### Customer 2 (ACME Corp)
```bash
/deployments/acme/
└── provision/ansible/roles/secrets/vars/vault.yml
    network_base_octets_production: "192.168.100"
    base_domain: "acme.com"
```

### Customer 3 (Widgets Inc)
```bash
/deployments/widgets/
└── provision/ansible/roles/secrets/vars/vault.yml
    network_base_octets_production: "172.16.50"
    base_domain: "widgets.io"
```

## Variable Flow

```
vault.yml (deployment-specific)
    ↓
network_base_octets_production: "10.96.200"
base_domain: "jaycashman.com"
    ↓
inventory/production/group_vars/all.yml
    ↓
network_base_octets: "{{ network_base_octets_production }}"
domain: "ai.{{ base_domain }}"
    ↓
proxy_ip: "{{ network_base_octets }}.200"
    ↓
RESULT: 10.96.200.200, ai.jaycashman.com
```

## What Goes Where?

### ✅ Version Control (Generic)
- All playbooks
- All roles
- All inventory structure
- `vault.example.yml` (template)
- Application definitions structure
- IP offset patterns (`.200`, `.201`, etc.)

### ❌ NOT Version Controlled (Deployment-Specific)
- `vault.yml` (encrypted, deployment-specific)
- Actual IP base octets
- Actual domain names
- All secrets

## Benefits

1. **Single Source**: One repo, multiple deployments
2. **Updates**: Update generic repo, pull into all deployments
3. **Security**: Each deployment has isolated secrets
4. **Flexibility**: Each customer can have different networks/domains
5. **Maintenance**: Fix once, deploy everywhere

## Deployment Workflow

### Initial Setup (Per Customer)
1. Clone repo to customer-specific directory
2. Create `vault.yml` from template
3. Configure customer-specific values
4. Encrypt vault
5. Deploy

### Updates (Generic Infrastructure)
1. Update generic repo
2. For each deployment:
   ```bash
   cd deployment-customer1
   git pull
   # vault.yml is preserved (gitignored)
   ansible-playbook -i inventory/production site.yml --ask-vault-pass
   ```

### Updates (Deployment-Specific)
```bash
cd deployment-customer1
ansible-vault edit provision/ansible/roles/secrets/vars/vault.yml
# Make changes
ansible-playbook -i inventory/production site.yml --ask-vault-pass
```

## Security Best Practices

1. **Never commit** `vault.yml` (gitignored)
2. **Always encrypt** vault with strong password
3. **Unique passwords** for each deployment
4. **Document** vault password location (1Password, etc.)
5. **Backup** vault.yml separately (encrypted)

## Migration from Hardcoded Values

If you currently have hardcoded values in `all.yml`:

1. **Extract** IPs and domains to `vault.yml`
2. **Replace** with variable references in `all.yml`
3. **Add** `vault.yml` to `.gitignore`
4. **Verify** nothing deployment-specific is in version control

## Troubleshooting

### Variables Not Resolving
```bash
# Check vault variables are loaded
ansible-inventory -i inventory/production --list | grep network_base_octets
```

### Need to Change Deployment Network
```bash
# Edit vault
ansible-vault edit roles/secrets/vars/vault.yml

# Update network_base_octets_production
# Redeploy
ansible-playbook -i inventory/production site.yml --ask-vault-pass
```

### Need to Change Deployment Domain
```bash
# Edit vault
ansible-vault edit roles/secrets/vars/vault.yml

# Update base_domain
# Redeploy (will update NGINX, SSL, etc.)
ansible-playbook -i inventory/production site.yml --ask-vault-pass
```

## Summary

- **This repo**: Generic infrastructure template
- **vault.yml**: Deployment-specific configuration (NOT in git)
- **vault.example.yml**: Template (IS in git)
- **Result**: One repo → Many deployments, each with unique networks/domains/secrets

