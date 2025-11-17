---
title: GitHub OAuth Setup for AI Portal Deployment Management
category: guides
created: 2025-01-13
updated: 2025-01-13
status: active
---

# GitHub OAuth Setup for AI Portal Deployment Management

This guide walks through setting up GitHub OAuth for the AI Portal's deployment management features, which allow you to connect private repositories, list releases, and deploy applications.

## Overview

The AI Portal uses GitHub OAuth to:
- Connect admin users to their GitHub accounts
- Access private repositories for deployment
- List and sync GitHub releases
- Trigger deployments from specific releases

## Prerequisites

- Admin access to your GitHub account
- Access to edit Ansible vault
- Your production/test domain configured

## Step 1: Create GitHub OAuth App

### 1.1 Navigate to GitHub Developer Settings

Go to: https://github.com/settings/developers

### 1.2 Create New OAuth App

1. Click **"New OAuth App"** (or "OAuth Apps" → "New OAuth App")
2. Fill in the application details:

**For Production:**
```
Application name: AI Portal Deployment (Production)
Homepage URL: https://your-production-domain.com
Authorization callback URL: https://your-production-domain.com/api/admin/github/callback
Application description: (optional) Deployment management for AI Portal
```

**For Test Environment:**
```
Application name: AI Portal Deployment (Test)
Homepage URL: https://your-test-domain.com
Authorization callback URL: https://your-test-domain.com/api/admin/github/callback
Application description: (optional) Test environment for AI Portal
```

3. Click **"Register application"**

### 1.3 Get OAuth Credentials

1. After registration, you'll see your **Client ID** - copy this
2. Click **"Generate a new client secret"**
3. Copy the **Client Secret** immediately (you won't see it again)

**Important:** Keep these credentials secure! They grant access to private repositories.

## Step 2: Add Credentials to Vault

### 2.1 Edit the Vault

```bash
cd /path/to/busibox/provision/ansible
ansible-vault edit roles/secrets/vars/vault.yml
```

### 2.2 Add GitHub OAuth Section

Find the `secrets:` section and add (if not already present):

```yaml
secrets:
  # ... existing secrets ...
  
  # GitHub OAuth for deployment management
  # Create OAuth App at: https://github.com/settings/developers
  # Callback URL: https://your-domain.com/api/admin/github/callback
  github:
    client_id: "Ov23li..."  # Your GitHub OAuth Client ID
    client_secret: "a1b2c3..."  # Your GitHub OAuth Client Secret
  
  # Encryption key for secrets storage
  # Can be same as better_auth_secret or generate a new one
  encryption_key: "your-encryption-key-32-bytes-minimum"
```

### 2.3 Generate Encryption Key (Optional)

If you want a separate encryption key (recommended for production):

```bash
# Generate a secure 32-byte key
openssl rand -base64 32
```

Or you can reuse `better_auth_secret`:

```yaml
encryption_key: "{{ secrets.better_auth_secret }}"
```

### 2.4 Container IPs

The vault.example.yml already includes these in the `ai-portal` secrets section, which reference Ansible variables:

```yaml
secrets:
  ai-portal:
    # ... existing secrets ...
    
    # GitHub OAuth
    github_client_id: "{{ secrets.github.client_id }}"
    github_client_secret: "{{ secrets.github.client_secret }}"
    github_redirect_uri: "https://{{ domain }}/api/admin/github/callback"
    
    # Encryption key
    encryption_key: "{{ secrets.encryption_key | default(secrets.better_auth_secret) }}"
    
    # Container IPs (already defined via Ansible vars)
    current_container_ip: "{{ apps_container_ip }}"
    apps_container_ip: "{{ apps_container_ip }}"
    agent_container_ip: "{{ agent_container_ip }}"
    postgres_container_ip: "{{ postgres_container_ip }}"
```

**Note:** You don't need to add these manually - they're already in the vault template and use Ansible inventory variables.

## Step 3: Deploy Updated Configuration

### 3.1 Production Deployment

```bash
cd provision/ansible
make apps  # This will deploy ai-portal with new .env
```

Or manually:

```bash
ansible-playbook -i inventory/production/hosts.yml site.yml --tags ai-portal
```

### 3.2 Test Environment

```bash
cd provision/ansible
INV=test make apps
```

### 3.3 Verify Deployment

Check that the environment variables are set:

```bash
# From host:
ssh root@10.96.200.31  # apps container IP

# In container:
pm2 logs ai-portal --lines 20

# Check for any OAuth-related errors
# Should NOT see "Missing GitHub OAuth configuration"
```

## Step 4: Test GitHub Connection

### 4.1 Access AI Portal Admin

1. Navigate to your AI Portal: `https://your-domain.com/home`
2. Log in as admin
3. Go to **Admin** → **Apps**
4. Click on any **INTERNAL** app
5. Scroll to **Deployment Management**

### 4.2 Connect GitHub Account

1. Click **"Connect GitHub Account"**
2. You should be redirected to GitHub's authorization page
3. Review the permissions:
   - **Read access to code** (to list releases)
   - **Read access to metadata** (repository info)
4. Click **"Authorize"**
5. You'll be redirected back to the AI Portal
6. Should see: **"GitHub Connected: your-username"**

### 4.3 Troubleshooting Connection Issues

**If clicking "Connect GitHub Account" does nothing:**

Check browser console (F12) for errors:
```javascript
// Should see:
GitHub auth URL: https://github.com/login/oauth/authorize?client_id=...
```

**If you see "Failed to connect: GitHub OAuth not configured":**
- GitHub credentials not in vault
- Vault not deployed to container
- Check PM2 logs for missing env vars

**If redirect fails after authorization:**
- Check callback URL matches exactly in GitHub OAuth App settings
- Verify `GITHUB_REDIRECT_URI` env var is correct

## Environment Variables Summary

The following environment variables are now set for AI Portal:

### Required:
- `GITHUB_CLIENT_ID` - OAuth app client ID
- `GITHUB_CLIENT_SECRET` - OAuth app client secret
- `GITHUB_REDIRECT_URI` - Callback URL (auto-generated from domain)
- `ENCRYPTION_KEY` - Key for encrypting secrets in database
- `CURRENT_CONTAINER_IP` - IP of container running AI Portal
- `APPS_CONTAINER_IP` - IP of apps container (for deployments)
- `AGENT_CONTAINER_IP` - IP of agent container
- `POSTGRES_CONTAINER_IP` - IP of PostgreSQL container

### Optional:
- `CONTAINER_SSH_PRIVATE_KEY` - SSH key for remote deployments (only needed if deploying to different containers)

## Security Considerations

### OAuth Scopes

The AI Portal requests minimal GitHub scopes:
- `repo` - Access to private repositories (for deployment)
- `user:email` - User email (for account linking)

### Secret Storage

- GitHub tokens are encrypted with AES-256-GCM before database storage
- Uses `ENCRYPTION_KEY` environment variable
- Falls back to `BETTER_AUTH_SECRET` if `ENCRYPTION_KEY` not set

### Token Refresh

- GitHub OAuth tokens don't expire by default
- Tokens are stored per-user in `GitHubConnection` table
- Users can disconnect/reconnect to refresh

## Next Steps

After GitHub OAuth is configured:

1. **Configure Deployment Settings** for each app
2. **Set up App Secrets** (database URLs, API keys, etc.)
3. **Sync GitHub Releases** for your apps
4. **Test Deployment** to staging environment
5. **Deploy to Production** when ready

## Related Documentation

- **Deployment System**: `/docs/deployment/manual-deployment-system.md`
- **App Secrets Management**: `/docs/guides/managing-app-secrets.md`
- **Troubleshooting**: `/docs/troubleshooting/deployment-issues.md`

## Troubleshooting

### Common Issues

**"Missing API key" error:**
- Check that all secrets are in vault.yml
- Verify deployment completed successfully
- Check PM2 logs: `pm2 logs ai-portal`

**"ECONNREFUSED" when connecting to GitHub:**
- Check network connectivity from container
- Verify firewall rules allow outbound HTTPS
- Test: `curl -v https://github.com`

**"Invalid redirect_uri":**
- Callback URL in GitHub OAuth App must match exactly
- Check for trailing slashes, http vs https
- Verify `GITHUB_REDIRECT_URI` env var

**Database errors after deployment:**
- Run Prisma migrations: `npm run db:migrate`
- Or push schema: `npm run db:push`
- Check: `npm run db:status`

## Support

For issues:
1. Check PM2 logs: `pm2 logs ai-portal --lines 50`
2. Check browser console (F12) for frontend errors
3. Verify GitHub OAuth App settings
4. Review Ansible deployment logs

