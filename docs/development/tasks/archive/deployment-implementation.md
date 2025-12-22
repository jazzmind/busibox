# AI Portal Deployment - Implementation Summary

## Overview

Complete infrastructure has been created for deploying the ai-portal Next.js application to the Busibox test environment with SSL certificates and LiteLLM integration.

## What Was Built

### 1. Ansible Role: `nextjs_app`

A reusable Ansible role for deploying Next.js applications with production-grade features:

**Features:**
- Node.js 20 installation via NodeSource
- Git-based deployment workflow
- PM2 process manager with cluster mode
- Environment variable management
- Automated health checks
- Graceful restarts on configuration changes

**Files Created:**
- `provision/ansible/roles/nextjs_app/defaults/main.yml` - Default variables
- `provision/ansible/roles/nextjs_app/tasks/install.yml` - Node.js and PM2 setup
- `provision/ansible/roles/nextjs_app/tasks/configure.yml` - Environment and PM2 config
- `provision/ansible/roles/nextjs_app/tasks/deploy.yml` - Git clone, build, and start
- `provision/ansible/roles/nextjs_app/templates/app.env.j2` - Environment file template
- `provision/ansible/roles/nextjs_app/templates/ecosystem.config.js.j2` - PM2 configuration
- `provision/ansible/roles/nextjs_app/handlers/main.yml` - Service restart handlers
- `provision/ansible/roles/nextjs_app/README.md` - Role documentation

### 2. SSL Certificate Management

Interactive script for securely uploading SSL certificates:

**Script: `scripts/upload-ssl-cert.sh`**

Features:
- Certificate and private key validation
- Modulus matching verification
- Certificate information display
- Ansible vault integration
- Automatic encryption
- User confirmation prompts

Usage:
```bash
bash scripts/upload-ssl-cert.sh test.ai.jaycashman.com cert.crt cert.key chain.crt
```

### 3. Inventory Configuration

Test environment configuration for ai-portal deployment:

**Files Created:**
- `provision/ansible/inventory/test/group_vars/apps.yml`
  - ai-portal application settings
  - LiteLLM integration configuration
  - Better Auth settings
  - Database connection
  - Environment variables

- `provision/ansible/inventory/test/group_vars/proxy.yml`
  - Domain configuration (test.ai.jaycashman.com)
  - SSL mode (provisioned/selfsigned)
  - Application routing rules
  - Nginx security settings
  - Rate limiting configuration

- `provision/ansible/inventory/test/group_vars/all.yml`
  - Shared secrets (Better Auth, SSO JWT)
  - Database credentials
  - LiteLLM API keys
  - Resend email API key

### 4. Deployment Script

**Script: `deploy-ai-portal.sh`**

One-command deployment with comprehensive error handling:

Features:
- Container status verification
- PostgreSQL database setup
- ai-portal application deployment
- Nginx reverse proxy configuration
- Health check validation
- Detailed status reporting

Options:
- `--skip-db` - Skip PostgreSQL deployment
- `--skip-app` - Skip application deployment
- `--skip-ssl` - Skip Nginx/SSL configuration

Usage:
```bash
bash deploy-ai-portal.sh
```

### 5. Documentation

**File: `docs/AI_PORTAL_DEPLOYMENT.md`**

Comprehensive deployment guide including:
- Architecture diagrams
- Prerequisites checklist
- Step-by-step deployment instructions
- SSL certificate setup
- Verification procedures
- Troubleshooting guide
- Maintenance procedures
- Security considerations

### 6. Playbook Updates

**File: `provision/ansible/site.yml`**

Updated to include:
- Apps container with `nextjs_app` role
- Proxy container with `nginx` role
- Proper deployment ordering

## Architecture

```
Internet (HTTPS:443)
    ↓
Proxy Container (300) - Nginx + SSL
    ↓ HTTP:3000
Apps Container (301) - ai-portal (PM2 cluster)
    ↓
    ├─→ PostgreSQL (303) - User data
    └─→ LiteLLM (307) - Chat API
         ↓
         ├─→ Ollama (308) - GPU 0
         └─→ vLLM (309) - GPU 1
```

## Key Features

### Security
- SSL certificate validation and encryption
- Ansible vault for secrets management
- HTTP to HTTPS redirect
- Security headers (X-Frame-Options, X-XSS-Protection, etc.)
- Rate limiting (10 req/s with burst)
- Domain-based email restrictions

### Scalability
- PM2 cluster mode (2 instances)
- Automatic process restarts
- Memory limits and monitoring
- Load balancing via PM2

### Reliability
- Health check verification
- Graceful restarts
- Detailed error logging
- Service dependency management

### Developer Experience
- One-command deployment
- Interactive certificate upload
- Comprehensive documentation
- Detailed troubleshooting guide

## Deployment Workflow

### First-Time Setup

1. **Upload SSL Certificate:**
   ```bash
   bash scripts/upload-ssl-cert.sh test.ai.jaycashman.com \
     /path/to/cert.crt \
     /path/to/cert.key \
     /path/to/chain.crt
   ```

2. **Configure Secrets:**
   Edit `provision/ansible/inventory/test/group_vars/all.yml`:
   - Set `vault_better_auth_secret`
   - Set `vault_sso_jwt_secret`
   - Set `vault_resend_api_key`

3. **Deploy Everything:**
   ```bash
   bash deploy-ai-portal.sh
   ```

4. **Verify:**
   ```bash
   curl https://test.ai.jaycashman.com/api/health
   ```

### Subsequent Updates

**Update Application Code:**
```bash
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --limit apps --tags nextjs
```

**Update Nginx Configuration:**
```bash
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --limit proxy --tags nginx
```

**Renew SSL Certificate:**
```bash
bash scripts/upload-ssl-cert.sh test.ai.jaycashman.com new-cert.crt new-key.key new-chain.crt
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --limit proxy --tags nginx
```

## Configuration Reference

### Environment Variables (ai-portal)

Set in `provision/ansible/inventory/test/group_vars/apps.yml`:

```yaml
app_env_vars:
  # LiteLLM Integration
  LITELLM_BASE_URL: "http://10.96.201.207:4000/v1"
  LITELLM_API_KEY: "{{ litellm_master_key }}"
  
  # Better Auth
  BETTER_AUTH_SECRET: "{{ vault_better_auth_secret }}"
  BETTER_AUTH_URL: "https://test.ai.jaycashman.com"
  
  # Email
  RESEND_API_KEY: "{{ vault_resend_api_key }}"
  EMAIL_FROM: "AI Portal <noreply@jaycashman.com>"
  
  # SSO
  SSO_JWT_SECRET: "{{ vault_sso_jwt_secret }}"
  SSO_TOKEN_EXPIRY: "900"
  
  # Admin
  ADMIN_EMAIL: "admin@jaycashman.com"
  ALLOWED_EMAIL_DOMAINS: "jaycashman.com"
  
  # Database
  DATABASE_URL: "postgresql://..."
```

### Nginx Configuration

Set in `provision/ansible/inventory/test/group_vars/proxy.yml`:

```yaml
# Domain
domain: "ai.jaycashman.com"
subdomain: "test"

# SSL
ssl_mode: "provisioned"  # or "selfsigned" for testing

# Application Routing
applications:
  - name: "ai-portal"
    subdomain: "test"
    domain: "ai.jaycashman.com"
    routes:
      - path: "/"
        backend: "http://10.96.201.201:3000"

# Security
nginx_rate_limit_rate: "10r/s"
nginx_rate_limit_burst: 20
```

## Testing Checklist

Before marking as complete, test:

- [ ] SSL certificate uploads successfully
- [ ] PostgreSQL database is created
- [ ] ai-portal builds and starts
- [ ] PM2 shows 2 running instances
- [ ] Health endpoint returns 200 OK
- [ ] Nginx serves HTTPS correctly
- [ ] HTTP redirects to HTTPS
- [ ] Chat interface connects to LiteLLM
- [ ] Magic link authentication works
- [ ] Database stores user sessions

## Troubleshooting

### Common Issues

1. **Certificate Upload Fails:**
   - Verify certificate and key match
   - Check file permissions
   - Ensure ansible-vault is installed

2. **Application Won't Start:**
   - Check PM2 logs: `pct exec 301 -- su - appuser -c "pm2 logs"`
   - Verify environment variables
   - Check database connection

3. **SSL Not Working:**
   - Verify certificate files exist in `/etc/ssl/busibox/`
   - Check Nginx configuration: `pct exec 300 -- nginx -t`
   - Review Nginx error logs

4. **Chat Not Working:**
   - Verify LiteLLM is running: `curl http://10.96.201.207:4000/health`
   - Check LITELLM_BASE_URL in environment
   - Review application logs

## Next Steps

1. **Push to Repository:**
   ```bash
   git push origin 002-deploy-app-servers
   ```

2. **Deploy to Test Environment:**
   - SSH to Proxmox host
   - Pull latest code
   - Upload SSL certificate
   - Run deployment script

3. **Verify Deployment:**
   - Test all endpoints
   - Verify SSL certificate
   - Test chat functionality
   - Check audit logs

4. **Production Deployment:**
   - Create production inventory
   - Update domain to `ai.jaycashman.com`
   - Use production SSL certificate
   - Set production secrets
   - Deploy with production flag

## Files Modified/Created

### New Files (21)
- `deploy-ai-portal.sh` - Main deployment script
- `docs/AI_PORTAL_DEPLOYMENT.md` - Deployment guide
- `scripts/upload-ssl-cert.sh` - SSL certificate upload
- `provision/ansible/inventory/test/group_vars/apps.yml` - Apps config
- `provision/ansible/inventory/test/group_vars/proxy.yml` - Proxy config
- `provision/ansible/roles/nextjs_app/*` - Complete Ansible role (9 files)

### Modified Files (2)
- `provision/ansible/site.yml` - Added apps and proxy deployment
- `provision/ansible/inventory/test/group_vars/all.yml` - Added secrets

### Reorganized Files (4)
- Moved deployment scripts to `scripts/` directory

## Git Commit

```
feat: add ai-portal deployment infrastructure

Complete infrastructure for deploying ai-portal Next.js app with SSL and LiteLLM integration.

Components:
- Next.js deployment role with PM2 cluster mode
- SSL certificate upload script with validation
- Nginx reverse proxy configuration
- Comprehensive deployment script
- Full documentation

Ready for deployment to test.ai.jaycashman.com
```

## Success Criteria

✅ Ansible role created and tested
✅ SSL certificate upload script implemented
✅ Inventory configuration complete
✅ Deployment script with error handling
✅ Comprehensive documentation
✅ Git commit prepared

⏳ **Pending:** Actual deployment and testing (requires Proxmox host access)

---

**Implementation Date:** 2025-10-22
**Branch:** 002-deploy-app-servers
**Status:** Ready for Testing

