# Deploy AI Portal to Test Environment - Quick Guide

Based on the new refactored Ansible configuration (2025-10-23).

## Overview

The configuration has been centralized and simplified:
- **Network/Domain config**: Stored in vault (deployment-specific)
- **Application definitions**: In `inventory/test/group_vars/all.yml`
- **Secrets**: In `roles/secrets/vars/vault.yml` (encrypted)

## Prerequisites

1. **Proxmox Host**: LXC containers created (300-309)
2. **LLM Stack**: Deployed and running (litellm, ollama, vllm)
3. **Vault File**: Created with your deployment-specific config

## Step 1: Create Vault File

Copy the example vault and customize it:

```bash
cd /root/busibox/provision/ansible
cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml
```

Edit `roles/secrets/vars/vault.yml`:

```yaml
# Network Configuration (YOUR network)
network_base_octets_production: "10.96.200"
network_base_octets_staging: "10.96.201"

# Domain Configuration (YOUR domain)
base_domain: "maigentpartners.com"  # Change to your domain
ssl_email: "admin@maigentpartners.com"

# Secrets - Generate with: node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
secrets:
  postgresql:
    password: "YOUR_SECURE_PASSWORD"
  
  ai-portal:
    database_url: "postgresql://{{ postgres_user }}:{{ secrets.postgresql.password }}@{{ postgres_host }}:{{ postgres_port }}/ai_portal"
    better_auth_secret: "YOUR_32_BYTE_SECRET"
    resend_api_key: "YOUR_RESEND_API_KEY"
    sso_jwt_secret: "YOUR_32_BYTE_SECRET"
    litellm_api_key: "sk-litellm-master-key"  # Match LiteLLM config
  
  litellm:
    master_key: "sk-litellm-master-key"  # Match ai-portal
    database_url: "postgresql://{{ postgres_user }}:{{ secrets.postgresql.password }}@{{ postgres_host }}:{{ postgres_port }}/litellm"
```

**Optional**: Encrypt the vault file:
```bash
ansible-vault encrypt roles/secrets/vars/vault.yml
# You'll be prompted to create a password
```

## Step 2: Verify Configuration

Check that your configuration is correct:

```bash
# View the computed variables (with vault)
ansible-inventory -i inventory/test/hosts.yml --list

# If vault is encrypted, add --ask-vault-pass
ansible-inventory -i inventory/test/hosts.yml --list --ask-vault-pass
```

Key things to verify:
- `network_base_octets`: Should be "10.96.201" (or your test network)
- `base_domain`: Should be your domain
- `full_domain`: Should be "test.ai.YOUR_DOMAIN"
- Application `secrets` are defined

## Step 3: Deploy PostgreSQL

```bash
cd /root/busibox/provision/ansible

# If vault is NOT encrypted:
ansible-playbook -i inventory/test/hosts.yml site.yml --limit pg --tags postgres

# If vault IS encrypted:
ansible-playbook -i inventory/test/hosts.yml site.yml --limit pg --tags postgres --ask-vault-pass
```

Verify:
```bash
pct exec 303 -- systemctl status postgresql
pct exec 303 -- su - postgres -c "psql -l"
```

## Step 4: Deploy AI Portal Application

```bash
cd /root/busibox/provision/ansible

# Deploy (add --ask-vault-pass if encrypted)
ansible-playbook -i inventory/test/hosts.yml site.yml --limit apps --tags nextjs
```

This will:
1. Install Node.js 20
2. Create `appuser` user/group
3. Clone ai-portal from GitHub
4. Install dependencies
5. Build Next.js app
6. Start with PM2

Verify:
```bash
# Check PM2 status
pct exec 301 -- su - appuser -c "pm2 status"

# View logs
pct exec 301 -- su - appuser -c "pm2 logs ai-portal --lines 50"

# Test health endpoint
curl http://10.96.201.201:3000/api/health
```

## Step 5: Deploy Nginx Proxy (Optional)

For now, we're using self-signed SSL (`ssl_mode: selfsigned` in `all.yml`).

```bash
cd /root/busibox/provision/ansible

# Deploy nginx
ansible-playbook -i inventory/test/hosts.yml site.yml --limit proxy --tags nginx
```

Verify:
```bash
# Check Nginx status
pct exec 300 -- systemctl status nginx

# Test SSL
pct exec 300 -- nginx -t

# Check cert
pct exec 300 -- openssl x509 -in /etc/ssl/certs/ssl-cert-snakeoil.pem -noout -dates
```

## Step 6: Test Deployment

### Internal Access (from Proxmox)

```bash
# Health check
curl http://10.96.201.201:3000/api/health

# Should return: {"status":"ok"}
```

### External Access (if Nginx deployed)

```bash
# HTTPS (will show self-signed cert warning)
curl -k https://test.ai.maigentpartners.com/api/health
```

### Test Chat Integration

1. Access the portal: `https://test.ai.maigentpartners.com`
2. Accept self-signed cert warning
3. Log in with magic link
4. Navigate to Chat
5. Send a test message
6. Verify LLM responds

## Troubleshooting

### Application Won't Start

```bash
# Check PM2 logs
pct exec 301 -- su - appuser -c "pm2 logs ai-portal --lines 100"

# Check environment
pct exec 301 -- cat /opt/ai-portal/.env

# Restart app
pct exec 301 -- su - appuser -c "pm2 restart ai-portal"
```

### Database Connection Issues

```bash
# Check PostgreSQL
pct exec 303 -- systemctl status postgresql

# Test connection from apps container
pct exec 301 -- psql "postgresql://busibox_test_user:PASSWORD@10.96.201.203:5432/ai_portal" -c "SELECT 1"

# Check PostgreSQL logs
pct exec 303 -- tail -f /var/log/postgresql/postgresql-15-main.log
```

### LiteLLM Connection Issues

```bash
# Check LiteLLM status
pct exec 307 -- systemctl status litellm

# Test from apps container
pct exec 301 -- curl http://10.96.201.207:4000/health

# Check logs
pct exec 307 -- journalctl -u litellm -f
```

### Missing Secrets Error

If you get errors about missing secrets:

```bash
# List variables
ansible -i inventory/test/hosts.yml TEST-apps-lxc -m debug -a "var=secrets"

# Check if vault is loaded
ansible -i inventory/test/hosts.yml TEST-apps-lxc -m debug -a "var=base_domain"
```

If vault isn't loading, make sure:
1. `vault.yml` exists in `roles/secrets/vars/`
2. The `secrets` role is applied before `nextjs_app` in `site.yml`

## Configuration Files Reference

**Key Configuration Files:**
- `inventory/test/hosts.yml` - Container IPs and groups
- `inventory/test/group_vars/all.yml` - **Main config** (apps, network, services)
- `inventory/test/group_vars/apps.yml` - Apps-specific overrides
- `inventory/test/group_vars/proxy.yml` - Proxy-specific overrides
- `roles/secrets/vars/vault.yml` - **Deployment-specific** (network, domain, secrets)

**Application Deployment:**
The `applications[]` array in `all.yml` defines all apps:
```yaml
applications:
  - name: ai-portal
    github_repo: jazzmind/ai-portal
    container: apps-lxc
    port: 3000
    routes:
      - type: domain
        domains:
          - "{{ full_domain }}"
    secrets:
      - database_url
      - better_auth_secret
      - resend_api_key
      # ...
```

## Next Steps

1. **Upload Production SSL Certificate** (when ready):
   ```bash
   bash scripts/upload-ssl-cert.sh test.ai.maigentpartners.com cert.crt cert.key chain.crt
   ```
   
   Then update `all.yml`:
   ```yaml
   ssl_mode: provisioned  # Change from selfsigned
   ```

2. **Deploy Additional Apps** (agent-client, doc-intel, innovation):
   - They're already defined in `all.yml`
   - Just run the deployment playbook

3. **Production Deployment**:
   - Copy `inventory/test/` to `inventory/production/`
   - Update vault with production network/domain
   - Deploy with production inventory

## Quick Commands

```bash
# Full deployment (PostgreSQL + Apps + Nginx)
cd /root/busibox/provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --limit pg,apps,proxy

# Redeploy just ai-portal
ansible-playbook -i inventory/test/hosts.yml site.yml --limit apps --tags nextjs

# Check application status
pct exec 301 -- su - appuser -c "pm2 status"

# View logs
pct exec 301 -- su - appuser -c "pm2 logs ai-portal"

# Restart application
pct exec 301 -- su - appuser -c "pm2 restart ai-portal"
```

---

**Last Updated**: 2025-10-23
**Configuration Version**: 2.0 (Refactored)

