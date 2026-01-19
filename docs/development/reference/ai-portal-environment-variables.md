---
title: AI Portal Environment Variables Reference
category: reference
created: 2025-01-13
updated: 2026-01-15
status: active
---

# AI Portal Environment Variables Reference

Complete reference for all environment variables used by the AI Portal application.

## Core Application

### `NODE_ENV`
- **Type:** `string`
- **Values:** `development` | `production`
- **Required:** Yes
- **Description:** Node.js environment mode
- **Ansible:** Set in `env:` section of inventory

### `PORT`
- **Type:** `number`
- **Default:** `3000`
- **Required:** No
- **Description:** Port the application listens on
- **Ansible:** Set in `env:` section of inventory

## Database

### `DATABASE_URL`
- **Type:** `string`
- **Format:** `postgresql://user:password@host:port/database`
- **Required:** Yes
- **Secret:** Yes
- **Description:** PostgreSQL connection string for AI Portal database
- **Ansible:** Generated from vault vars
- **Example:** `postgresql://busibox_user:secret@10.96.200.203:5432/ai_portal`

## Authentication

> **Note**: As of 2026-01-15, Busibox uses a Zero Trust architecture where AuthZ issues all session JWTs directly. The `busibox-session` cookie contains the session JWT. See `docs/architecture/03-authentication.md` for details.

### `AUTHZ_BASE_URL`
- **Type:** `string`
- **Format:** URL
- **Required:** Yes
- **Secret:** No
- **Description:** Base URL for AuthZ service (Zero Trust authentication)
- **Ansible:** `http://{{ authz_ip }}:8010` or via nginx proxy
- **Example:** `https://localhost/api/authz` (via proxy) or `http://10.96.200.210:8010` (direct)

### `AUTHZ_CLIENT_ID`
- **Type:** `string`
- **Required:** Yes
- **Secret:** No
- **Description:** OAuth client ID for ai-portal to authenticate with AuthZ
- **Default:** `ai-portal`

### `AUTHZ_CLIENT_SECRET`
- **Type:** `string`
- **Required:** Yes
- **Secret:** Yes
- **Description:** OAuth client secret for ai-portal
- **Ansible:** `secrets.authz_client_secret`

### `AUTHZ_ADMIN_TOKEN`
- **Type:** `string`
- **Required:** Yes
- **Secret:** Yes
- **Description:** Admin token for AuthZ management operations
- **Ansible:** `secrets.authz_admin_token`

### `BETTER_AUTH_SECRET`
- **Type:** `string`
- **Length:** ≥32 bytes
- **Required:** Yes (legacy - used for backward compatibility)
- **Secret:** Yes
- **Description:** Secret key for Better Auth session encryption (legacy sessions)
- **Ansible:** `secrets.better_auth_secret`
- **Generation:** `openssl rand -base64 32`

### `BETTER_AUTH_URL`
- **Type:** `string`
- **Format:** URL
- **Required:** Yes
- **Secret:** No (but generated from vault)
- **Description:** Base URL for Better Auth callbacks and redirects
- **Ansible:** `https://{{ domain }}`
- **Example:** `https://portal.example.com`

### `SSO_JWT_SECRET`
- **Type:** `string`
- **Length:** ≥32 bytes
- **Required:** Yes
- **Secret:** Yes
- **Description:** JWT secret for SSO token validation
- **Ansible:** `secrets.jwt_secret`
- **Shared with:** agent-manager, agent-server

### `SSO_TOKEN_EXPIRY`
- **Type:** `number`
- **Unit:** seconds
- **Default:** `900` (15 minutes)
- **Required:** No
- **Description:** SSO token expiration time
- **Ansible:** Set in `env:` section

## Email Configuration

### SMTP (Primary)

#### `SMTP_HOST`
- **Type:** `string`
- **Required:** Yes (for email functionality)
- **Secret:** Yes
- **Description:** SMTP server hostname
- **Ansible:** `secrets.smtp.host`
- **Examples:** `smtp.gmail.com`, `smtp.sendgrid.net`

#### `SMTP_PORT`
- **Type:** `number`
- **Common:** `587` (TLS), `465` (SSL)
- **Required:** Yes (for email functionality)
- **Secret:** Yes
- **Description:** SMTP server port
- **Ansible:** `secrets.smtp.port`

#### `SMTP_USER`
- **Type:** `string`
- **Required:** Yes (for email functionality)
- **Secret:** Yes
- **Description:** SMTP authentication username/email
- **Ansible:** `secrets.smtp.user`

#### `SMTP_PASSWORD`
- **Type:** `string`
- **Required:** Yes (for email functionality)
- **Secret:** Yes
- **Description:** SMTP authentication password
- **Ansible:** `secrets.smtp.password`

#### `SMTP_SECURE`
- **Type:** `string`
- **Values:** `true` | `false`
- **Default:** `true`
- **Required:** No
- **Secret:** Yes
- **Description:** Use SSL/TLS for SMTP connection
- **Ansible:** `secrets.smtp.secure`

### Resend (Fallback)

#### `RESEND_API_KEY`
- **Type:** `string`
- **Format:** `re_...`
- **Required:** No (optional fallback)
- **Secret:** Optional
- **Description:** Resend API key for email sending (fallback if SMTP not configured)
- **Ansible:** `optional_secrets.resend_api_key`
- **Fallback:** Email disabled if neither SMTP nor Resend configured

### Email Settings

#### `EMAIL_FROM`
- **Type:** `string`
- **Format:** `Name <email@domain.com>`
- **Required:** Yes
- **Secret:** Yes
- **Description:** From address for outgoing emails
- **Ansible:** `secrets.smtp.from_email`
- **Example:** `AI Portal <noreply@example.com>`

#### `ADMIN_EMAIL`
- **Type:** `string`
- **Format:** `email@domain.com`
- **Required:** Yes
- **Secret:** Yes
- **Description:** Admin email address for system notifications
- **Ansible:** `secrets.admin_email`

#### `ALLOWED_EMAIL_DOMAINS`
- **Type:** `string`
- **Format:** Comma-separated list
- **Required:** Yes
- **Secret:** Yes
- **Description:** Email domains allowed to authenticate
- **Ansible:** `secrets.allowed_email_domains`
- **Example:** `example.com,company.org`

## LLM Integration

### `LITELLM_BASE_URL`
- **Type:** `string`
- **Format:** URL
- **Required:** Yes
- **Description:** Base URL for liteLLM proxy
- **Ansible:** `http://{{ litellm_ip }}:{{ litellm_port }}/v1`
- **Example:** `http://10.96.200.207:4000/v1`

### `LITELLM_API_KEY`
- **Type:** `string`
- **Required:** Yes
- **Secret:** Yes
- **Description:** API key for liteLLM proxy authentication
- **Ansible:** `secrets.litellm_api_key`

### `OPENAI_API_KEY`
- **Type:** `string`
- **Format:** `sk-...`
- **Required:** Yes (for video generation)
- **Secret:** Yes
- **Description:** OpenAI API key for video generation features
- **Ansible:** `secrets.openai_api_key`

## GitHub OAuth (Deployment Management)

### `GITHUB_CLIENT_ID`
- **Type:** `string`
- **Format:** `Ov23li...`
- **Required:** Yes
- **Secret:** Yes
- **Description:** GitHub OAuth App client ID
- **Ansible:** `secrets.github.client_id`
- **Setup:** https://github.com/settings/developers

### `GITHUB_CLIENT_SECRET`
- **Type:** `string`
- **Required:** Yes
- **Secret:** Yes
- **Description:** GitHub OAuth App client secret
- **Ansible:** `secrets.github.client_secret`
- **Setup:** Generated when creating OAuth App

### `GITHUB_REDIRECT_URI`
- **Type:** `string`
- **Format:** URL
- **Required:** Yes
- **Secret:** Yes
- **Description:** GitHub OAuth callback URL
- **Ansible:** `https://{{ domain }}/api/admin/github/callback`
- **Must match:** GitHub OAuth App settings exactly

## Deployment Management

### `ENCRYPTION_KEY`
- **Type:** `string`
- **Length:** ≥32 bytes
- **Required:** Yes
- **Secret:** Yes
- **Description:** AES-256-GCM key for encrypting secrets in database
- **Ansible:** `secrets.encryption_key` (defaults to `better_auth_secret`)
- **Generation:** `openssl rand -base64 32`
- **Used for:** GitHub tokens, app secrets

### Container IPs

#### `CURRENT_CONTAINER_IP`
- **Type:** `string`
- **Format:** IPv4
- **Required:** Yes
- **Secret:** Yes (via vault)
- **Description:** IP address of container running AI Portal
- **Ansible:** `{{ apps_container_ip }}`
- **Purpose:** Determine if deployment is local or remote

#### `APPS_CONTAINER_IP`
- **Type:** `string`
- **Format:** IPv4
- **Required:** Yes
- **Secret:** Yes (via vault)
- **Description:** IP address of apps container
- **Ansible:** `{{ apps_container_ip }}`
- **Example:** `10.96.200.31`

#### `AGENT_CONTAINER_IP`
- **Type:** `string`
- **Format:** IPv4
- **Required:** Yes
- **Secret:** Yes (via vault)
- **Description:** IP address of agent container
- **Ansible:** `{{ agent_container_ip }}`
- **Example:** `10.96.200.207`

#### `POSTGRES_CONTAINER_IP`
- **Type:** `string`
- **Format:** IPv4
- **Required:** Yes
- **Secret:** Yes (via vault)
- **Description:** IP address of PostgreSQL container
- **Ansible:** `{{ postgres_container_ip }}`
- **Example:** `10.96.200.203`

### SSH Configuration

#### `CONTAINER_SSH_PRIVATE_KEY`
- **Type:** `string`
- **Format:** `base64:...` or file path
- **Required:** No (only for remote deployments)
- **Secret:** Optional
- **Description:** SSH private key for accessing remote containers
- **Ansible:** `secrets.container_ssh_private_key` (optional)
- **Note:** Not needed if `CURRENT_CONTAINER_IP === APPS_CONTAINER_IP`

## Microsoft SSO (Optional)

### `MICROSOFT_CLIENT_ID`
- **Type:** `string`
- **Required:** No
- **Secret:** Optional
- **Description:** Microsoft Entra ID (Azure AD) application client ID
- **Ansible:** `optional_secrets.microsoft_client_id`
- **Setup:** https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps

### `MICROSOFT_CLIENT_SECRET`
- **Type:** `string`
- **Required:** No
- **Secret:** Optional
- **Description:** Microsoft Entra ID application client secret
- **Ansible:** `optional_secrets.microsoft_client_secret`

### `MICROSOFT_TENANT_ID`
- **Type:** `string`
- **Default:** `common`
- **Required:** No
- **Secret:** Optional
- **Description:** Microsoft tenant ID (`common` for multi-tenant)
- **Ansible:** `optional_secrets.microsoft_tenant_id`

## Secret Categories

### Required Secrets (Must be non-empty)
These must be present in vault and deployed:
- `database_url`
- `better_auth_secret`
- `sso_jwt_secret`
- `litellm_api_key`
- `openai_api_key`
- `admin_email`
- `allowed_email_domains`
- `email_from`
- `smtp_host`
- `smtp_port`
- `smtp_user`
- `smtp_password`
- `smtp_secure`
- `github_client_id`
- `github_client_secret`
- `github_redirect_uri`
- `encryption_key`
- `current_container_ip`
- `apps_container_ip`
- `agent_container_ip`
- `postgres_container_ip`

### Optional Secrets (Can be empty/omitted)
These are deployed only if non-empty in vault:
- `resend_api_key` - Fallback email provider
- `microsoft_client_id` - Microsoft SSO
- `microsoft_client_secret` - Microsoft SSO
- `microsoft_tenant_id` - Microsoft SSO
- `container_ssh_private_key` - Remote deployments

## Ansible Configuration

### Vault Location
```
provision/ansible/roles/secrets/vars/vault.yml
```

### Editing Vault
```bash
ansible-vault edit roles/secrets/vars/vault.yml
```

### Vault Example
See: `provision/ansible/roles/secrets/vars/vault.example.yml`

### Inventory Files
- Production: `inventory/production/group_vars/all/00-main.yml`
- Test: `inventory/test/group_vars/all/00-main.yml`
- Local: `inventory/local/group_vars/all.yml`

## Deployment

### Generate .env File
Ansible automatically generates `.env` from vault secrets:

```yaml
# In inventory file:
applications:
  - name: ai-portal
    secrets:
      - database_url
      - github_client_id
      # ... more required secrets
    optional_secrets:
      - resend_api_key
      # ... more optional secrets
```

### Deploy Updated Secrets
```bash
cd provision/ansible
make apps  # Deploys ai-portal with new .env
```

### Verify Environment Variables
```bash
# SSH to apps container
ssh root@10.96.200.31

# Check PM2 environment
pm2 show ai-portal

# Or check .env file
cat /srv/apps/ai-portal/.env
```

## Troubleshooting

### Missing Variable Errors
```
Error: Missing API key
```
**Solution:** Ensure secret is in vault.yml and deployment succeeded

### Wrong Variable Format
```
Error: Invalid DATABASE_URL
```
**Solution:** Check vault.yml format matches examples

### Optional Secret Causing Errors
```
Error: resend_api_key is undefined
```
**Solution:** Add to `optional_secrets:` in inventory

### Deployment Not Updating .env
```bash
# Force deployment with vault
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml \
  --tags ai-portal \
  --ask-vault-pass
```

## Security Best Practices

1. **Vault Encryption:** Always keep vault.yml encrypted
2. **Secret Rotation:** Rotate secrets regularly
3. **Separate Keys:** Use different keys per environment
4. **No Hardcoding:** Never commit unencrypted secrets
5. **Access Control:** Limit who can edit vault.yml

## Related Documentation

- **Setup Guide:** `/docs/guides/github-oauth-setup.md`
- **Deployment System:** `/docs/deployment/manual-deployment-system.md`
- **Vault Management:** `/docs/configuration/ansible-vault.md`
- **Troubleshooting:** `/docs/troubleshooting/environment-variables.md`

