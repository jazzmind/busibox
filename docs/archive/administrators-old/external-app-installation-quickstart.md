---
title: "External App Installation Quickstart"
category: "administrator"
order: 10
description: "Quick start guide for installing external apps via AI Portal"
published: true
---

# External App Installation - Quick Start Guide

## Overview

This guide walks through installing external apps into Busibox via AI Portal, with automatic database provisioning and nginx configuration.

## Prerequisites

- App built from app-template with `busibox.json` manifest
- GitHub repository (public or private with token)
- Admin access to AI Portal
- Deployment service running in authz container

## Architecture

```
AI Portal (UI) → Authz Container (Deployment Service) → apps-lxc + pg-lxc + nginx
```

## Step 1: Prepare Your App

### Create busibox.json

In your app repository root:

```json
{
  "name": "My App",
  "id": "my-app",
  "version": "1.0.0",
  "description": "My awesome app",
  "icon": "Calculator",
  "defaultPath": "/myapp",
  "defaultPort": 3010,
  "healthEndpoint": "/api/health",
  "buildCommand": "npm run build",
  "startCommand": "npm start -p 3010",
  "appMode": "prisma",
  "database": {
    "required": true,
    "preferredName": "myapp",
    "schemaManagement": "prisma"
  },
  "requiredEnvVars": ["LITELLM_API_KEY"],
  "busiboxAppVersion": "^2.1.17"
}
```

### Validate Manifest

```bash
npx ts-node scripts/validate-manifest.ts
```

### Push to GitHub

```bash
git add busibox.json
git commit -m "Add Busibox manifest"
git push origin main
```

## Step 2: Install via AI Portal

### Open AI Portal Admin

1. Navigate to `https://your-domain.com/admin`
2. Log in as admin user

### Install Custom App

1. Click "Apps" in admin menu
2. Click "Install from Library"
3. Click "Install Custom App from GitHub"
4. Enter repository URL: `owner/repo` or `https://github.com/owner/repo`
5. (Optional) Enter GitHub token for private repos
6. Click "Fetch Manifest"

### Review Manifest Preview

The UI will show:
- App name, description, icon
- Database requirements
- Required environment variables
- Port and path configuration

### Confirm Installation

1. Review the details
2. Click "Install App"
3. Watch deployment progress in real-time

## Step 3: Configure Environment Variables

After installation:

1. Navigate to the app's configuration page
2. Click "Manage Secrets"
3. Add required environment variables:
   - `LITELLM_API_KEY` - Your LiteLLM API key
   - Others as needed
4. Save secrets

Note: `DATABASE_URL` is automatically created if database is required.

## Step 4: Deploy the App

1. In app configuration, click "Deploy"
2. Select environment (production or staging)
3. Click "Confirm Deployment"
4. Monitor deployment logs in real-time via WebSocket

**Deployment Steps:**
1. Provision Database (if required)
2. Run Ansible Deployment
3. Configure Nginx
4. Complete

## Step 5: Verify Installation

### Check App Health

```bash
curl https://your-domain.com/myapp/api/health
```

Expected response:
```json
{"status": "ok"}
```

### Access App

Navigate to: `https://your-domain.com/myapp`

### Check Logs

```bash
# SSH to apps container
ssh root@apps-ip

# View logs
journalctl -u my-app -f
```

## Local Development Testing

### 1. Start Busibox Services

```bash
cd busibox
docker-compose up -d
```

### 2. Run Authz with Deployment Service

```bash
cd srv/authz
python -m uvicorn src.main:app --reload --port 8010
```

### 3. Test Deployment API

```bash
# Get admin token
TOKEN="your-admin-jwt"

# Deploy app
curl -X POST http://localhost:8010/api/v1/deployment/deploy \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @deployment-request.json
```

### 4. Monitor via WebSocket

```javascript
const ws = new WebSocket('ws://localhost:8010/api/v1/deployment/deploy/{id}/logs');
ws.onmessage = (event) => {
  const status = JSON.parse(event.data);
  console.log(`${status.progress}% - ${status.currentStep}`);
};
```

## Troubleshooting

### Manifest Not Found

**Error**: "Manifest file (busibox.json) not found"

**Solution**:
- Ensure `busibox.json` exists in repo root
- Check branch name (default is `main`)
- For private repos, provide GitHub token

### Database Provisioning Fails

**Error**: "Database provisioning failed"

**Solutions**:
- Check `POSTGRES_ADMIN_PASSWORD` is set in authz env
- Verify SSH access to pg-lxc: `ssh root@10.96.200.202`
- Check PostgreSQL is running: `systemctl status postgresql`
- Verify database name doesn't already exist

### Ansible Deployment Fails

**Error**: "Ansible deployment failed"

**Solutions**:
- Check Ansible inventory exists
- Verify GitHub token has repo access
- Check app is in `applications` list in inventory
- View detailed logs in deployment status

### Nginx Configuration Fails

**Error**: "Nginx configuration validation failed"

**Solutions**:
- Check nginx syntax: `nginx -t`
- Verify nginx config directories exist
- Check for port/path conflicts
- Review nginx error log: `/var/log/nginx/error.log`

### WebSocket Won't Connect

**Error**: Connection refused or timeout

**Solutions**:
- Verify authz service is running
- Check WebSocket URL is correct
- Ensure admin token is valid
- Check browser console for errors

## Environment Variables Required

### Authz Container

Add to Ansible vault (`provision/ansible/roles/secrets/vars/vault.yml`):

```yaml
authz:
  # Existing authz vars...
  
  # Deployment service vars
  ansible_dir: /root/busibox/provision/ansible
  postgres_host: 10.96.200.202
  postgres_port: 5432
  postgres_admin_user: postgres
  postgres_admin_password: "{{ secrets.postgres.admin_password }}"
  apps_container_ip: 10.96.200.201
  apps_container_ip_staging: 10.96.201.201
  ssh_key_path: /root/.ssh/id_rsa
  nginx_config_dir: /etc/nginx/sites-available/apps
  nginx_enabled_dir: /etc/nginx/sites-enabled
```

### AI Portal

Add to environment:

```bash
DEPLOYMENT_SERVICE_URL=http://10.96.200.210:8010/api/v1/deployment
```

## Example: Deploy Project Analysis

### 1. Verify Manifest

```bash
cd /path/to/project-analysis
cat busibox.json
```

Should show:
```json
{
  "name": "Data Analysis",
  "id": "project-analysis",
  "database": {
    "required": true,
    "preferredName": "project_analysis"
  }
}
```

### 2. Install via AI Portal

1. Go to AI Portal admin
2. Install Custom App
3. Enter: `jazzmind/project-analysis` (or your fork)
4. Fetch manifest
5. Install

### 3. Configure Secrets

Add:
- `LITELLM_API_KEY` - Your LiteLLM key
- Other optional vars

### 4. Deploy

Click "Deploy to Production"

### 5. Access

Navigate to: `https://your-domain.com/projects`

## Next Steps

- Test with multiple apps
- Monitor deployment logs
- Set up alerting for failures
- Add rollback UI
- Add deployment history

## Related Documentation

- Implementation: `busibox/DEPLOYMENT_SERVICE_IMPLEMENTATION.md`
- API Reference: `busibox/srv/authz/DEPLOYMENT_SERVICE.md`
- App Template: `app-template/CLAUDE.md`
- AI Portal Integration: `ai-portal/EXTERNAL_APP_INSTALLATION_IMPLEMENTATION.md`

## Support

For help:
1. Check authz logs: `journalctl -u authz-api -f`
2. Check deployment status API
3. Review manifest validation errors
4. Test SSH and PostgreSQL access
5. Verify Ansible inventory configuration
