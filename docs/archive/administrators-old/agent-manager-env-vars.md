---
title: "Agent Manager Environment Variables"
category: "administrator"
order: 30
description: "Environment variables for Agent Manager in Ansible/Proxmox deployment"
published: true
---

# Agent-Manager Missing Environment Variables

## Overview

Several environment variables used in local Docker development were missing from the Ansible/Proxmox deployment configuration, causing potential runtime issues.

## Missing Variables Added

### 1. LiteLLM Configuration

**Added to `apps.yml` env section:**
```yaml
LITELLM_BASE_URL: "http://{{ litellm_ip }}:{{ litellm_port }}/v1"
```

**Added to secrets:**
```yaml
litellm_api_key: "{{ secrets.litellm_api_key }}"
```

### 2. AuthZ Service Configuration

**Added to `apps.yml` env section:**
```yaml
AUTHZ_BASE_URL: "http://{{ authz_ip }}:8010"
AUTHZ_CLIENT_ID: "agent-manager"
```

**Note:** `AUTHZ_CLIENT_SECRET` is mapped from the existing `admin_client_secret` secret.

### 3. SSO Token Expiry

**Added to `apps.yml` env section:**
```yaml
SSO_TOKEN_EXPIRY: "900"  # 15 minutes
```

### 4. Optional Email Service (Resend)

**Added to optional_secrets:**
```yaml
optional_secrets:
  - resend_api_key        # Email service (optional)
  - email_from            # Email sender address (optional)
```

These are optional because agent-manager may not need email functionality in all deployments.

### 5. Optional AuthZ Admin Token

**Added to optional_secrets:**
```yaml
optional_secrets:
  - authz_admin_token     # AuthZ admin token (optional, for admin operations)
```

## Secret Key Renaming

The vault secret key was renamed from `agent-manager` to `agent-manager` for consistency:

### Before:
```yaml
agent-manager:
  database_url: "..."
  admin_client_id: "agent-manager-app"
```

### After:
```yaml
agent-manager:
  database_url: "..."
  admin_client_id: "agent-manager"
```

## Variable Mapping

Some secret names in Ansible map to different environment variable names:

| Ansible Secret Key | Environment Variable | Purpose |
|--------------------|---------------------|---------|
| `admin_client_id` | `AUTHZ_CLIENT_ID` | AuthZ OAuth client ID |
| `admin_client_secret` | `AUTHZ_CLIENT_SECRET` | AuthZ OAuth client secret |
| `litellm_api_key` | `LITELLM_API_KEY` | LiteLLM authentication |
| `resend_api_key` | `RESEND_API_KEY` | Email service (optional) |
| `email_from` | `EMAIL_FROM` | Email sender address (optional) |
| `authz_admin_token` | `AUTHZ_ADMIN_TOKEN` | AuthZ admin operations (optional) |

This mapping is handled automatically by the app_deployer role.

## Development-Only Variables Not Added

The following variables from your local `.env` are development-specific and were **not** added to Ansible:

- `NODE_TLS_REJECT_UNAUTHORIZED=0` - Disabled TLS verification (insecure, dev only)

In production/staging, proper TLS certificates are used, so this is not needed.

## Migration Steps

### For Existing Deployments

If you have an existing vault file with `agent-manager`, you need to:

1. **Decrypt the vault:**
   ```bash
   cd provision/ansible
   ansible-vault decrypt roles/secrets/vars/vault.yml
   ```

2. **Rename the secret section:**
   ```yaml
   # Change this:
   agent-manager:
     # ... secrets ...
   
   # To this:
   agent-manager:
     # ... secrets ...
   ```

3. **Add new secrets:**
   ```yaml
   agent-manager:
     # ... existing secrets ...
     litellm_api_key: "{{ secrets.litellm_api_key }}"
     admin_client_id: "agent-manager"  # Update from "agent-manager-app"
     
     # Optional (only if using email):
     # resend_api_key: "your-resend-key"
     # email_from: "Portal <noreply@your-domain.com>"
     
     # Optional (only if needed):
     # authz_admin_token: "your-authz-admin-token"
   ```

4. **Re-encrypt the vault:**
   ```bash
   ansible-vault encrypt roles/secrets/vars/vault.yml
   ```

5. **Redeploy agent-manager:**
   ```bash
   ansible-playbook -i inventory/staging/hosts.yml site.yml --tags app_deployer -e "deploy_app=agent-manager"
   ```

### For New Deployments

The updated `vault.example.yml` includes all the new secrets with placeholder values. Copy it and fill in your actual values before encrypting.

## Verification

After deploying, check that all variables are present:

```bash
ssh root@{apps-lxc-ip} "grep -E 'LITELLM|AUTHZ|SSO_TOKEN_EXPIRY' /srv/apps/agent-manager/.env"
```

Expected output should include:
```
LITELLM_BASE_URL="http://10.96.201.207:4000/v1"
LITELLM_API_KEY="your-actual-key"
AUTHZ_BASE_URL="http://10.96.201.210:8010"
AUTHZ_CLIENT_ID="agent-manager"
AUTHZ_CLIENT_SECRET="your-actual-secret"
SSO_TOKEN_EXPIRY="900"
```

## Files Modified

1. **`provision/ansible/group_vars/all/apps.yml`**
   - Added `litellm_api_key` to secrets list
   - Added `optional_secrets` section with `resend_api_key`, `email_from`, `authz_admin_token`
   - Added env variables: `LITELLM_BASE_URL`, `AUTHZ_BASE_URL`, `AUTHZ_CLIENT_ID`, `SSO_TOKEN_EXPIRY`
   - Added comments to clarify OAuth credential mapping

2. **`provision/ansible/roles/secrets/vars/vault.example.yml`**
   - Renamed `agent-manager` → `agent-manager`
   - Updated `admin_client_id` from `"agent-manager-app"` to `"agent-manager"`
   - Added `litellm_api_key` secret
   - Added commented optional secrets for email and authz admin token
   - Added documentation comments

## Related Issues

- Agent-manager auth redirect issue (fixed by adding `NEXT_PUBLIC_AI_PORTAL_URL`)
- Service detection issue on Proxmox (fixed by checking Docker containers)
- Agent-client → agent-manager rename (completed)

## Testing

After deployment, test that:
1. Agent-manager can communicate with LiteLLM
2. Agent-manager can authenticate with AuthZ service
3. SSO token exchange works correctly
4. (If configured) Email functionality works

## Notes

- The `NEXT_PUBLIC_*` prefix is required for variables that need to be accessible in Next.js client-side code
- Variables without `NEXT_PUBLIC_` prefix are only available server-side
- Changes to `NEXT_PUBLIC_*` variables require a rebuild of the application
