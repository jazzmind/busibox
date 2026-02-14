---
title: GitHub OAuth and Environment Variables Configuration Fix
category: session-notes
created: 2025-01-13
updated: 2025-01-13
status: completed
---

# GitHub OAuth and Environment Variables Configuration Fix

## Issue

The AI Portal's "Connect GitHub Account" button was not working because required environment variables were missing from the deployment configuration.

**Symptoms:**
- Clicking "Connect GitHub Account" redirected back to apps list page
- No errors in logs
- GitHub OAuth flow not initiating

**Root Cause:**
- GitHub OAuth credentials (`GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`) not configured in vault
- Additional deployment-related environment variables missing from Ansible configuration

## Solution

### 1. Updated Vault Example Template

**File:** `provision/ansible/roles/secrets/vars/vault.example.yml`

**Added:**
```yaml
secrets:
  # Encryption key for secrets storage
  encryption_key: "CHANGE_ME_ENCRYPTION_KEY_FOR_SECRETS_STORAGE_32_BYTES"
  
  # GitHub OAuth for deployment management
  github:
    client_id: "CHANGE_ME_GITHUB_OAUTH_CLIENT_ID"
    client_secret: "CHANGE_ME_GITHUB_OAUTH_CLIENT_SECRET"
  
  # Container SSH key for remote deployments (optional)
  container_ssh_private_key: ""

  # AI Portal secrets section
  ai-portal:
    # ... existing secrets ...
    
    # GitHub OAuth
    github_client_id: "{{ secrets.github.client_id }}"
    github_client_secret: "{{ secrets.github.client_secret }}"
    github_redirect_uri: "https://{{ domain }}/api/admin/github/callback"
    
    # Encryption
    encryption_key: "{{ secrets.encryption_key | default(secrets.better_auth_secret) }}"
    
    # Container IPs
    current_container_ip: "{{ apps_container_ip }}"
    apps_container_ip: "{{ apps_container_ip }}"
    agent_container_ip: "{{ agent_container_ip }}"
    postgres_container_ip: "{{ postgres_container_ip }}"
    
    # SSH key (optional)
    container_ssh_private_key: "{{ secrets.container_ssh_private_key | default('') }}"
```

### 2. Updated Inventory Files

Updated all inventory files to include the new environment variables:

**Files:**
- `inventory/production/group_vars/all/00-main.yml`
- `inventory/test/group_vars/all/00-main.yml`
- `inventory/local/group_vars/all.yml`

**Added Required Secrets:**
```yaml
secrets:
  # ... existing secrets ...
  - github_client_id
  - github_client_secret
  - github_redirect_uri
  - encryption_key
  - current_container_ip
  - apps_container_ip
  - agent_container_ip
  - postgres_container_ip
```

**Added Optional Secrets:**
```yaml
optional_secrets:
  # ... existing optional secrets ...
  - container_ssh_private_key
```

### 3. Improved Error Handling

**File:** `ai-portal/src/components/admin/DeploymentManager.tsx`

**Enhanced:**
- Check HTTP response status before processing
- Display specific error messages from API
- Log auth URL before redirect for debugging
- Better user feedback for missing configuration

```typescript
async function connectGitHub() {
  try {
    const res = await fetch('/api/admin/github/connect');
    
    if (!res.ok) {
      const errorData = await res.json();
      console.error('Failed to get GitHub auth URL:', errorData);
      alert(`Failed to connect: ${errorData.error || 'Unknown error'}`);
      return;
    }
    
    const { authUrl, state } = await res.json();
    console.log('GitHub auth URL:', authUrl);
    window.location.href = authUrl;
  } catch (error) {
    console.error('Failed to initiate GitHub connection:', error);
    alert('Failed to connect to GitHub');
  }
}
```

## Environment Variables Summary

### Required for GitHub OAuth
- `GITHUB_CLIENT_ID` - GitHub OAuth App client ID
- `GITHUB_CLIENT_SECRET` - GitHub OAuth App client secret  
- `GITHUB_REDIRECT_URI` - Callback URL (auto-generated: `https://domain/api/admin/github/callback`)

### Required for Deployment Management
- `ENCRYPTION_KEY` - For encrypting secrets in database (can default to `BETTER_AUTH_SECRET`)
- `CURRENT_CONTAINER_IP` - IP of container running AI Portal
- `APPS_CONTAINER_IP` - IP of apps container
- `AGENT_CONTAINER_IP` - IP of agent container
- `POSTGRES_CONTAINER_IP` - IP of PostgreSQL container

### Optional
- `CONTAINER_SSH_PRIVATE_KEY` - Only needed for remote deployments to different containers

## Setup Instructions

### 1. Create GitHub OAuth App

1. Go to: https://github.com/settings/developers
2. Click "New OAuth App"
3. Set callback URL: `https://your-domain.com/api/admin/github/callback`
4. Copy Client ID and Client Secret

### 2. Update Vault

```bash
cd provision/ansible
ansible-vault edit roles/secrets/vars/vault.yml
```

Add GitHub OAuth credentials:
```yaml
secrets:
  github:
    client_id: "Ov23li..."
    client_secret: "abc123..."
  
  encryption_key: "your-32-byte-key"  # or reuse better_auth_secret
```

### 3. Deploy

```bash
cd provision/ansible

# Production
make apps

# Test
INV=test make apps
```

### 4. Verify

1. Check PM2 logs: `pm2 logs ai-portal --lines 20`
2. Verify env vars: `pm2 show ai-portal`
3. Test GitHub connection in AI Portal admin UI

## Documentation Created

### Setup Guide
**File:** `docs/guides/github-oauth-setup.md`

Comprehensive guide covering:
- Creating GitHub OAuth App
- Adding credentials to vault
- Deploying configuration
- Testing connection
- Troubleshooting

### Environment Variables Reference
**File:** `docs/reference/ai-portal-environment-variables.md`

Complete reference for all AI Portal environment variables:
- Type definitions and formats
- Required vs optional
- Ansible mappings
- Security best practices
- Troubleshooting

## Commits

### Busibox Repository

1. **2913b0c** - Add GitHub OAuth secrets to ai-portal configuration
2. **859caeb** - Add complete GitHub OAuth and deployment environment variables
3. **d220f3e** - Add comprehensive GitHub OAuth setup guide
4. **b320045** - Add comprehensive AI Portal environment variables reference
5. **[pending]** - GitHub OAuth and environment variables configuration fix summary

### AI Portal Repository

1. **c6c0033** - Add better error handling for GitHub OAuth connection

## Testing Checklist

After deploying these changes:

- [ ] Create GitHub OAuth App
- [ ] Add credentials to vault.yml
- [ ] Deploy to test environment
- [ ] Log into AI Portal admin
- [ ] Click "Connect GitHub Account"
- [ ] Verify redirect to GitHub authorization page
- [ ] Authorize application
- [ ] Verify redirect back to AI Portal
- [ ] Confirm "GitHub Connected: username" appears
- [ ] Test listing releases for a repository
- [ ] Test deployment trigger

## Related Issues

This fix resolves:
- GitHub OAuth not working
- Missing environment variables for deployment management
- Silent failures in GitHub connection flow

## Next Steps

1. **User Action Required:** 
   - Create GitHub OAuth App
   - Add credentials to vault
   - Deploy updated configuration

2. **Future Enhancements:**
   - Add health check for required env vars at startup
   - Display configuration status in admin panel
   - Add OAuth app setup wizard in UI

## References

- GitHub OAuth Documentation: https://docs.github.com/en/apps/oauth-apps
- Better Auth: https://better-auth.com/
- Ansible Vault: https://docs.ansible.com/ansible/latest/vault_guide/

