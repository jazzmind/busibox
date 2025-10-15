# Quick Start: Application Services Deployment

**Feature**: 002-deploy-app-servers  
**Created**: 2025-10-15  
**Prerequisites**: Spec 001 infrastructure fully provisioned and tested

## Overview

This guide covers:
1. Adding a new application to the system
2. Managing application secrets
3. Configuring NGINX routing
4. Deploying applications
5. Troubleshooting common issues

---

## Prerequisites

Ensure the following from spec 001 are complete:

✅ All LXC containers provisioned (apps-lxc, openwebui-lxc, agent-lxc, etc.)  
✅ Node.js and PM2 installed (node_common role)  
✅ Deploywatch systemd timer running  
✅ PostgreSQL operational with busibox database  
✅ MinIO, Milvus, Redis operational

**New requirements for spec 002:**

- DNS records configured:
  - `ai.jaycashman.com` → NGINX IP (10.96.200.24)
  - `*.ai.jaycashman.com` → NGINX IP (10.96.200.24)
- DNS provider API credentials (for Let's Encrypt wildcard cert)
- Ansible vault password file

---

## 1. Adding a New Application

### Step 1: Add Application to Config

Edit `provision/ansible/group_vars/apps.yml`:

```yaml
applications:
  # ... existing applications ...
  
  # Your new application
  - name: my-new-app
    github_repo: myorg/my-app
    container: apps-lxc
    container_ip: 10.96.200.25
    port: 3002
    deploy_path: /srv/apps/my-new-app
    health_endpoint: /health
    build_command: "npm run build"  # Optional
    routes:
      - type: subdomain
        subdomain: myapp
      - type: path
        domain: ai.jaycashman.com
        path: /myapp
    env:
      NODE_ENV: "production"
      API_URL: "http://10.96.200.30:8000"
    secrets:
      - database_url
      - api_key
```

### Step 2: Add Secrets (if needed)

If your application needs secrets, edit the vault file:

```bash
# Edit encrypted vault
ansible-vault edit provision/ansible/roles/secrets/vars/vault.yml

# Add your secrets under your app name
```

Add to vault.yml:
```yaml
secrets:
  my_new_app:  # Must match 'name' in apps.yml
    database_url: "postgresql://busibox_user:password@10.96.200.26/busibox"
    api_key: "your-secret-api-key-here"
```

Save and exit (vault will re-encrypt automatically).

### Step 3: Deploy

```bash
cd provision/ansible

# Run deployment (includes NGINX config, deploywatch setup)
make deploy-apps

# Trigger immediate deployment (don't wait for deploywatch timer)
ssh root@10.96.200.25 "bash /srv/deploywatch/apps/my-new-app.sh"
```

### Step 4: Verify

```bash
# Check application health
curl https://myapp.ai.jaycashman.com/health

# Check deployment log
ssh root@10.96.200.25 "journalctl -u deploywatch.service -n 50"

# Check application process
ssh root@10.96.200.25 "pm2 list"
```

---

## 2. Managing Secrets

### Adding a New Secret

```bash
# Edit vault
ansible-vault edit provision/ansible/roles/secrets/vars/vault.yml

# Add secret under appropriate app
secrets:
  agent_server:
    new_secret_key: "new-secret-value"
```

### Rotating a Secret

1. Update value in vault.yml:
   ```bash
   ansible-vault edit provision/ansible/roles/secrets/vars/vault.yml
   ```

2. Redeploy to update .env files:
   ```bash
   make deploy-apps
   ```

3. Applications will restart automatically with new secrets

### Viewing Secrets (Decrypted)

```bash
# View entire vault
ansible-vault view provision/ansible/roles/secrets/vars/vault.yml

# Or decrypt temporarily
ansible-vault decrypt provision/ansible/roles/secrets/vars/vault.yml
# ... make changes ...
ansible-vault encrypt provision/ansible/roles/secrets/vars/vault.yml
```

### Vault Password Management

```bash
# Store password in file (add to .gitignore!)
echo "your-vault-password" > .vault_password
chmod 0600 .vault_password

# Configure Ansible to use it
export ANSIBLE_VAULT_PASSWORD_FILE=.vault_password

# Or use in Makefile
ansible-playbook --vault-password-file=.vault_password ...
```

---

## 3. Configuring NGINX Routing

### Subdomain Routing

Application accessible at: `https://subdomain.ai.jaycashman.com`

```yaml
routes:
  - type: subdomain
    subdomain: myapp
    websocket: false  # Set true if app needs WebSocket
```

Result: NGINX creates server block for `myapp.ai.jaycashman.com`

### Path Routing

Application accessible at: `https://ai.jaycashman.com/myapp`

```yaml
routes:
  - type: path
    domain: ai.jaycashman.com
    path: /myapp
    strip_path: false  # Set true to remove /myapp before proxying
```

**Important**: Application must handle base path correctly!

If `strip_path: false`, application receives `/myapp/some/route`  
If `strip_path: true`, application receives `/some/route`

### Domain Routing (Root)

Application accessible at: `https://ai.jaycashman.com` (root)

```yaml
routes:
  - type: domain
    domains:
      - ai.jaycashman.com
      - www.ai.jaycashman.com
```

### Multiple Routes (Hybrid)

Same application accessible multiple ways:

```yaml
routes:
  - type: subdomain
    subdomain: agents
  - type: path
    domain: ai.jaycashman.com
    path: /agents
```

Result: Both `https://agents.ai.jaycashman.com` and `https://ai.jaycashman.com/agents` work

---

## 4. SSL Certificate Setup

### Initial Setup (One-time)

1. Install certbot with DNS plugin:
   ```bash
   ssh root@10.96.200.24  # NGINX container
   apt-get install certbot python3-certbot-nginx python3-certbot-dns-cloudflare
   ```

2. Add DNS credentials to vault:
   ```bash
   ansible-vault edit provision/ansible/roles/secrets/vars/vault.yml
   ```
   
   ```yaml
   secrets:
     letsencrypt:
       dns_provider: "cloudflare"
       cloudflare_api_token: "your-api-token"
       email: "admin@jaycashman.com"
   ```

3. Run NGINX role to deploy credentials and obtain cert:
   ```bash
   cd provision/ansible
   ansible-playbook -i inventory/hosts.yml site.yml --tags nginx
   ```

### Certificate Renewal

Automatic via certbot systemd timer (runs twice daily):

```bash
# Check renewal timer status
ssh root@10.96.200.24 "systemctl status certbot.timer"

# Manually test renewal (dry run)
ssh root@10.96.200.24 "certbot renew --dry-run"

# Force renewal
ssh root@10.96.200.24 "certbot renew --force-renewal"
```

Certbot automatically reloads NGINX after renewal.

---

## 5. Deployment Workflow

### Automated Deployment (Recommended)

Deploywatch checks for new releases every 15 minutes:

1. Developer tags new release on GitHub
2. Deploywatch detects new tag
3. Downloads and deploys release
4. Runs health check
5. Logs success/failure

**No manual intervention required!**

### Manual Deployment

Force immediate deployment:

```bash
# SSH to target container
ssh root@10.96.200.25  # apps-lxc

# Run deploywatch script for specific app
bash /srv/deploywatch/apps/cashman-portal.sh

# Or restart from PM2
cd /srv/apps/cashman
git pull  # If using git instead of releases
npm install
pm2 restart cashman-portal
```

### Deployment Verification

```bash
# Check deploywatch logs
ssh root@10.96.200.25 "journalctl -u deploywatch.service -n 100"

# Check application logs
ssh root@10.96.200.25 "pm2 logs cashman-portal --lines 50"

# Check current version
ssh root@10.96.200.25 "cat /srv/apps/cashman/.version"

# Check health endpoint
curl -f https://ai.jaycashman.com/api/health || echo "Health check failed"
```

---

## 6. Troubleshooting

### Application Not Accessible

**Symptom**: 502 Bad Gateway or connection refused

**Diagnosis**:
```bash
# Check application is running
ssh root@10.96.200.25 "pm2 list"

# Check application logs
ssh root@10.96.200.25 "pm2 logs cashman-portal --lines 50"

# Check if port is listening
ssh root@10.96.200.25 "netstat -tlnp | grep 3000"

# Check NGINX error log
ssh root@10.96.200.24 "tail -f /var/log/nginx/error.log"
```

**Solutions**:
- Restart application: `pm2 restart cashman-portal`
- Check .env file exists: `ls -la /srv/apps/cashman/.env`
- Verify port matches apps.yml configuration

---

### SSL Certificate Issues

**Symptom**: Browser shows "Certificate not valid"

**Diagnosis**:
```bash
# Check certificate expiration
ssh root@10.96.200.24 "certbot certificates"

# Check NGINX is using correct cert
ssh root@10.96.200.24 "nginx -T | grep ssl_certificate"
```

**Solutions**:
- Renew certificate: `certbot renew --force-renewal`
- Check DNS is pointing to correct IP
- Verify wildcard cert includes subdomain

---

### Secrets Not Loading

**Symptom**: Application errors about missing environment variables

**Diagnosis**:
```bash
# Check .env file exists and has correct permissions
ssh root@10.96.200.25 "ls -la /srv/apps/cashman/.env"

# Check .env content (as root)
ssh root@10.96.200.25 "cat /srv/apps/cashman/.env"

# Check PM2 is loading environment file
ssh root@10.96.200.25 "pm2 show cashman-portal"
```

**Solutions**:
- Re-run deployment: `make deploy-apps`
- Verify secret exists in vault.yml
- Check secret name matches in apps.yml `secrets:` list

---

### Deploywatch Not Running

**Symptom**: New GitHub releases not deploying automatically

**Diagnosis**:
```bash
# Check deploywatch timer status
ssh root@10.96.200.25 "systemctl status deploywatch.timer"

# Check last run
ssh root@10.96.200.25 "journalctl -u deploywatch.service | tail -20"
```

**Solutions**:
```bash
# Start/enable timer
ssh root@10.96.200.25 "systemctl enable --now deploywatch.timer"

# Manually trigger run
ssh root@10.96.200.25 "systemctl start deploywatch.service"

# Check for errors in script
ssh root@10.96.200.25 "bash -x /srv/deploywatch/deploywatch.sh"
```

---

### NGINX Configuration Error

**Symptom**: NGINX fails to reload after deployment

**Diagnosis**:
```bash
# Test NGINX configuration
ssh root@10.96.200.24 "nginx -t"

# Check NGINX error log
ssh root@10.96.200.24 "tail -50 /var/log/nginx/error.log"
```

**Solutions**:
```bash
# Fix configuration error shown in nginx -t output
# Then reload
ssh root@10.96.200.24 "nginx -s reload"

# If broken, restore previous config
ssh root@10.96.200.24 "cp /etc/nginx/sites-available/myapp.conf.backup /etc/nginx/sites-available/myapp.conf"
ssh root@10.96.200.24 "nginx -t && nginx -s reload"
```

---

## 7. Common Tasks

### Add Route to Existing Application

1. Edit `apps.yml`, add route:
   ```yaml
   routes:
     - type: subdomain
       subdomain: newname
   ```

2. Redeploy:
   ```bash
   make deploy-apps
   ```

3. Test new route:
   ```bash
   curl https://newname.ai.jaycashman.com/health
   ```

### Change Application Port

1. Update `apps.yml`:
   ```yaml
   port: 3005  # New port
   ```

2. Update application config to listen on new port

3. Redeploy:
   ```bash
   make deploy-apps
   ```

4. Restart application:
   ```bash
   ssh root@10.96.200.25 "pm2 restart myapp"
   ```

### Remove an Application

1. Remove from `apps.yml`

2. Run deployment (removes NGINX config, deploywatch script):
   ```bash
   make deploy-apps
   ```

3. Manually stop and remove from container:
   ```bash
   ssh root@10.96.200.25 "pm2 delete myapp"
   ssh root@10.96.200.25 "rm -rf /srv/apps/myapp"
   ```

---

## 8. Testing

### Manual Testing Checklist

After deploying a new application:

- [ ] Health endpoint responds: `curl https://myapp.ai.jaycashman.com/health`
- [ ] SSL certificate valid (no browser warnings)
- [ ] Subdomain route works (if configured)
- [ ] Path route works (if configured)
- [ ] Authentication works (if required)
- [ ] Application can connect to agent-server (if needed)
- [ ] Application can access database (if needed)
- [ ] Logs show no errors: `pm2 logs myapp`
- [ ] Deploywatch deploys updates: Tag new release on GitHub, wait 15 min, verify

### Automated Testing

Extend `test-infrastructure.sh` with application tests:

```bash
# Add to test-infrastructure.sh
test_application_deployment() {
  log_info "Testing application deployment..."
  
  # Health check
  if curl -sf https://myapp.ai.jaycashman.com/health > /dev/null; then
    record_test "myapp health" "PASS"
  else
    record_test "myapp health" "FAIL" "Health endpoint not responding"
  fi
  
  # SSL check
  if curl -sf --head https://myapp.ai.jaycashman.com | grep -q "200 OK"; then
    record_test "myapp SSL" "PASS"
  else
    record_test "myapp SSL" "FAIL" "HTTPS not working"
  fi
}
```

---

## 9. Reference

### File Locations

| Purpose | Location | Description |
|---------|----------|-------------|
| App config | `provision/ansible/group_vars/apps.yml` | Application definitions |
| Secrets | `provision/ansible/roles/secrets/vars/vault.yml` | Encrypted secrets |
| NGINX configs | `/etc/nginx/sites-available/*.conf` | Generated vhost configs |
| App .env | `/srv/apps/<app-name>/.env` | Runtime environment variables |
| Deploywatch scripts | `/srv/deploywatch/apps/<app-name>.sh` | Per-app deployment scripts |
| Version files | `/srv/apps/<app-name>/.version` | Current deployed version |

### Useful Commands

```bash
# List all applications
pm2 list

# Application logs
pm2 logs <app-name> --lines 100

# Deploywatch status
systemctl status deploywatch.timer
journalctl -u deploywatch.service -n 50

# NGINX status
systemctl status nginx
nginx -t  # Test config
nginx -s reload  # Reload config

# SSL certificate status
certbot certificates

# Check DNS resolution
dig ai.jaycashman.com
dig agents.ai.jaycashman.com
```

---

## Next Steps

- Review [data-model.md](./data-model.md) for detailed configuration schema
- Review [contracts/agent-api.yaml](./contracts/agent-api.yaml) for API integration
- Implement Phase 2 tasks from `tasks.md` (created by `/speckit.tasks`)

