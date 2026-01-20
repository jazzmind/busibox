# AI Portal Deployment Guide

This guide covers deploying the ai-portal Next.js application to the Busibox infrastructure with SSL certificates and LiteLLM integration.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Internet / Users                         │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS (443)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Proxy Container (300) - test.ai.localhost            │
│  - Nginx reverse proxy                                      │
│  - SSL termination                                          │
│  - Rate limiting                                            │
│  - Security headers                                         │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP (3000)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Apps Container (301) - ai-portal                           │
│  - Next.js 15 application                                   │
│  - PM2 cluster mode (2 instances)                           │
│  - Better Auth (magic link)                                 │
│  - Chat interface                                           │
└──────┬──────────────┬───────────────────────────────────────┘
       │              │
       │              │ HTTP (4000)
       │              ▼
       │   ┌──────────────────────────────────────────────────┐
       │   │  LiteLLM Container (307)                         │
       │   │  - OpenAI-compatible API                         │
       │   │  - Model routing                                 │
       │   └──────┬───────────────────────────────────────────┘
       │          │
       │          ├─────────▶ Ollama (308) - GPU 0
       │          └─────────▶ vLLM (309) - GPU 1
       │
       │ PostgreSQL (5432)
       ▼
┌─────────────────────────────────────────────────────────────┐
│  PostgreSQL Container (303)                                 │
│  - User accounts & sessions                                 │
│  - Audit logs                                               │
│  - App metadata                                             │
└─────────────────────────────────────────────────────────────┘
```

## Prerequisites

### 1. Infrastructure Setup

Ensure the following containers are created and running:

```bash
# Check container status
pct status 300  # Proxy
pct status 301  # Apps
pct status 303  # PostgreSQL
pct status 307  # LiteLLM
pct status 308  # Ollama
pct status 309  # vLLM
```

If containers don't exist, create them:

```bash
cd /root/busibox
bash provision/pct/create_lxc_base.sh test
```

### 2. LLM Stack Deployment

Deploy the LLM services first:

```bash
cd /root/busibox/provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --tags llm
```

Verify LLM services:

```bash
# Check LiteLLM
curl http://10.96.201.207:4000/health

# Check Ollama
curl http://10.96.201.208:11434/api/tags

# Check vLLM
curl http://10.96.201.209:8000/health
```

### 3. SSL Certificate

You have two options for SSL certificates:

#### Option A: Upload Production Certificate (Recommended)

```bash
cd /root/busibox
bash scripts/upload-ssl-cert.sh test.ai.localhost \
  /path/to/certificate.crt \
  /path/to/private.key \
  /path/to/chain.crt
```

This script will:
- Validate certificate and key match
- Display certificate information
- Store in Ansible vault (encrypted)
- Prompt for confirmation

#### Option B: Use Self-Signed Certificate (Testing Only)

Edit `provision/ansible/inventory/test/group_vars/proxy.yml`:

```yaml
ssl_mode: "selfsigned"  # Change from "provisioned"
```

⚠️ **Warning**: Self-signed certificates will show browser warnings.

### 4. Configure Environment Variables

Edit `provision/ansible/inventory/test/group_vars/all.yml` to set secrets:

```yaml
vault_better_auth_secret: "your-32-byte-secret-here"
vault_sso_jwt_secret: "your-32-byte-secret-here"
vault_resend_api_key: "your-resend-api-key"
```

Generate secrets:

```bash
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

For production, encrypt the vault:

```bash
ansible-vault encrypt provision/ansible/roles/secrets/vars/vault.yml
```

## Deployment

### Quick Deployment (All Steps)

```bash
cd /root/busibox
bash deploy-ai-portal.sh
```

This will:
1. Deploy PostgreSQL database
2. Deploy ai-portal application
3. Configure Nginx reverse proxy with SSL

### Step-by-Step Deployment

#### Step 1: Deploy PostgreSQL

```bash
cd /root/busibox/provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --limit pg --tags postgres
```

Verify:

```bash
pct exec 303 -- systemctl status postgresql
pct exec 303 -- su - postgres -c "psql -l"
```

#### Step 2: Deploy ai-portal Application

```bash
cd /root/busibox/provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --limit apps --tags nextjs
```

This will:
- Install Node.js 20
- Clone ai-portal repository
- Install npm dependencies
- Build Next.js application
- Start with PM2 (cluster mode, 2 instances)

Verify:

```bash
# Check PM2 status
pct exec 301 -- su - appuser -c "pm2 status"

# Check application logs
pct exec 301 -- su - appuser -c "pm2 logs ai-portal --lines 50"

# Test health endpoint
curl http://10.96.201.201:3000/api/health
```

#### Step 3: Deploy Nginx Reverse Proxy

```bash
cd /root/busibox/provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --limit proxy --tags nginx
```

Verify:

```bash
# Check Nginx status
pct exec 300 -- systemctl status nginx

# Test SSL configuration
pct exec 300 -- nginx -t

# Check certificate
pct exec 300 -- openssl x509 -in /etc/ssl/busibox/ai.localhost.crt -noout -dates
```

## Verification

### 1. Internal Access

```bash
# From Proxmox host
curl -v http://10.96.201.201:3000/api/health
```

Expected: `200 OK`

### 2. External Access (HTTPS)

```bash
# From Proxmox host or external machine
curl -v https://test.ai.localhost/api/health
```

Expected: `200 OK` with valid SSL certificate

### 3. Chat Functionality

1. Open browser: `https://test.ai.localhost`
2. Log in with magic link (email to allowed domain)
3. Navigate to Chat
4. Send a test message
5. Verify streaming response from LLM

### 4. Database Connection

```bash
# Check database exists
pct exec 303 -- su - postgres -c "psql -l | grep ai_portal_test"

# Check tables created
pct exec 303 -- su - postgres -c "psql ai_portal_test -c '\dt'"
```

## Troubleshooting

### Application Won't Start

```bash
# Check PM2 logs
pct exec 301 -- su - appuser -c "pm2 logs ai-portal --lines 100"

# Check environment variables
pct exec 301 -- cat /opt/ai-portal/.env

# Restart application
pct exec 301 -- su - appuser -c "pm2 restart ai-portal"
```

### SSL Certificate Issues

```bash
# Check certificate details
pct exec 300 -- openssl x509 -in /etc/ssl/busibox/ai.localhost.crt -noout -text

# Check Nginx error logs
pct exec 300 -- tail -f /var/log/nginx/error.log

# Test Nginx configuration
pct exec 300 -- nginx -t
```

### Database Connection Errors

```bash
# Check PostgreSQL is running
pct exec 303 -- systemctl status postgresql

# Test connection from apps container
pct exec 301 -- psql "postgresql://busibox_test_user:test_password_change_me@10.96.201.203:5432/ai_portal_test" -c "SELECT 1"

# Check PostgreSQL logs
pct exec 303 -- tail -f /var/log/postgresql/postgresql-15-main.log
```

### LiteLLM Connection Issues

```bash
# Check LiteLLM is running
pct exec 307 -- systemctl status litellm

# Test LiteLLM from apps container
pct exec 301 -- curl http://10.96.201.207:4000/health

# Check LiteLLM logs
pct exec 307 -- journalctl -u litellm -f
```

## Maintenance

### Update Application Code

```bash
# SSH into apps container
pct enter 301

# As appuser
su - appuser
cd /opt/ai-portal/current
git pull origin main
npm install
npm run build
pm2 restart ai-portal
```

Or use Ansible:

```bash
cd /root/busibox/provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --limit apps --tags nextjs
```

### Renew SSL Certificate

1. Upload new certificate:

```bash
bash scripts/upload-ssl-cert.sh test.ai.localhost \
  /path/to/new-certificate.crt \
  /path/to/new-private.key \
  /path/to/new-chain.crt
```

2. Redeploy Nginx:

```bash
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --limit proxy --tags nginx
```

### View Logs

```bash
# Application logs
pct exec 301 -- su - appuser -c "pm2 logs ai-portal"

# Nginx access logs
pct exec 300 -- tail -f /var/log/nginx/access.log

# Nginx error logs
pct exec 300 -- tail -f /var/log/nginx/error.log

# PostgreSQL logs
pct exec 303 -- tail -f /var/log/postgresql/postgresql-15-main.log
```

### Backup Database

```bash
# Backup ai-portal database
pct exec 303 -- su - postgres -c "pg_dump ai_portal_test" > ai_portal_test_backup.sql

# Restore database
cat ai_portal_test_backup.sql | pct exec 303 -- su - postgres -c "psql ai_portal_test"
```

## Security Considerations

1. **SSL Certificates**: Use valid certificates from a trusted CA in production
2. **Secrets Management**: Encrypt vault.yml with `ansible-vault encrypt`
3. **Database Passwords**: Use strong, unique passwords (not the test defaults)
4. **Firewall**: Ensure only necessary ports are exposed
5. **Email Domain Restrictions**: Configure `ALLOWED_EMAIL_DOMAINS` appropriately
6. **Rate Limiting**: Nginx is configured with rate limiting (10 req/s)
7. **Security Headers**: X-Frame-Options, X-Content-Type-Options, etc. are set

## Configuration Files

- **Inventory**: `provision/ansible/inventory/test/hosts.yml`
- **Apps Config**: `provision/ansible/inventory/test/group_vars/apps.yml`
- **Proxy Config**: `provision/ansible/inventory/test/group_vars/proxy.yml`
- **Global Config**: `provision/ansible/inventory/test/group_vars/all.yml`
- **Secrets**: `provision/ansible/roles/secrets/vars/vault.yml` (encrypted)

## Support

For issues or questions:

1. Check logs (see Troubleshooting section)
2. Review Ansible playbook output
3. Verify all prerequisites are met
4. Check container networking: `pct exec <CTID> -- ip addr`
5. Test connectivity between containers: `pct exec 301 -- ping 10.96.201.207`

---

**Last Updated**: 2025-10-22
**Version**: 1.0.0

