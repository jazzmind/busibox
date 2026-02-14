# Application Secrets Sync Fix

## Problem

When deploying to Proxmox, Ansible's secrets validation was failing because application-specific secrets (like `database_url`, `email_from`, `smtp_*`, etc.) were not being synced to the vault during installation.

The `sync_secrets_to_vault()` function only synced infrastructure secrets (PostgreSQL password, MinIO keys, JWT secrets, etc.) but not the application-specific secrets required by apps defined in `apps.yml`.

## Root Cause

1. **Infrastructure vs Application Secrets**: The vault sync function handled infrastructure secrets but not application secrets
2. **Missing Variables**: Variables like `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_HOST` weren't being set during secret generation
3. **Vault Structure Mismatch**: Application secrets need to be stored as `secrets.{app_name}.{secret_key}` in the vault

## Solution

### 1. Extended `generate_secrets()` Function
**File**: `scripts/make/install.sh`

Added generation/initialization of application-specific variables:
- PostgreSQL connection details (`POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`)
- Email/SMTP configuration with defaults
- GitHub OAuth placeholders
- Encryption key (defaults to JWT secret)

### 2. Extended `sync_secrets_to_vault()` Function
**File**: `scripts/lib/vault.sh`

Added syncing of application secrets for:

**AI Portal**:
- `database_url` - Constructed from PostgreSQL variables
- `sso_jwt_secret` - From SSO_JWT_SECRET
- `litellm_api_key` - From LITELLM_API_KEY
- `openai_api_key` - From OPENAI_API_KEY (if set)
- `allowed_email_domains` - From ALLOWED_DOMAINS
- `email_from`, `smtp_*` - Email configuration
- `github_client_id`, `github_client_secret`, `github_redirect_uri` - OAuth config
- `encryption_key` - For encrypting sensitive data
- Container IPs - Using Ansible variables for dynamic resolution

**Agent Manager**:
- `database_url` - Constructed from PostgreSQL variables
- `agent_api_key` - From LITELLM_API_KEY
- `jwt_secret`, `session_secret`, `sso_jwt_secret` - From SSO_JWT_SECRET
- `litellm_api_key` - From LITELLM_API_KEY

## Vault Structure

Application secrets are stored in the vault with this structure:

```yaml
secrets:
  # Infrastructure secrets
  postgresql:
    password: "..."
  minio:
    root_user: "..."
    root_password: "..."
  jwt_secret: "..."
  authz_master_key: "..."
  litellm_api_key: "..."
  
  # Application secrets
  ai_portal:
    database_url: "postgresql://user:pass@host:port/db"
    sso_jwt_secret: "..."
    litellm_api_key: "..."
    email_from: "noreply@busibox.local"
    smtp_host: "localhost"
    # ... etc
    
  agent_manager:
    database_url: "postgresql://user:pass@host:port/db"
    agent_api_key: "..."
    jwt_secret: "..."
    # ... etc
```

## Default Values

The fix provides sensible defaults for optional configuration:

**Email/SMTP** (can be customized later):
- `email_from`: `noreply@busibox.local`
- `smtp_host`: `localhost`
- `smtp_port`: `25`
- `smtp_secure`: `false`

**GitHub OAuth** (must be configured for production):
- `github_client_id`: `CHANGE_ME`
- `github_client_secret`: `CHANGE_ME`
- `github_redirect_uri`: `https://localhost/portal/api/admin/github/callback`

**Container IPs**: Use Ansible variables (e.g., `{{ core_apps_ip }}`) for dynamic resolution

## Testing

Run installation on Proxmox:
```bash
cd /root/busibox
make install
```

The secrets validation should now pass without errors.

## Related Issues Fixed

This fix also resolves:
1. **Unbound Variable Error**: Fixed `ANSIBLE_VAULT_PASSWORD_FILE` unbound variable issue
2. **Embedding Model Caching**: Added Proxmox support for embedding model pre-caching

## Files Modified

```
scripts/make/install.sh          - Extended generate_secrets()
scripts/lib/vault.sh             - Extended sync_secrets_to_vault()
                                 - Fixed unbound variable checks
```

## Future Improvements

1. **Dynamic Application Discovery**: Automatically detect applications from `apps.yml` and sync their secrets
2. **Secret Validation**: Warn about placeholder values (like `CHANGE_ME`) that should be configured
3. **Interactive Configuration**: Prompt for optional secrets during installation
4. **Secret Rotation**: Add commands to rotate secrets safely
