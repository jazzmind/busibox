# Vault Secrets Configuration Guide

## Missing Secrets Error

If you get errors like:
```
❌ Missing or empty secret for application 'agent-server':
Secret key: jwt_secret
```

You need to add the missing secrets to your vault file.

## How to Add Missing Secrets

### On the Server

```bash
cd ~/busibox/provision/ansible

# Edit the encrypted vault
ansible-vault edit roles/secrets/vars/vault.yml
```

### GitHub Token (Required for Private Repos)

If your GitHub repositories are private, you MUST add a GitHub token:

1. **Create GitHub Personal Access Token**:
   - Go to: https://github.com/settings/tokens
   - Click: "Generate new token (classic)"
   - Name: "Busibox Deployment"
   - Scope: ✅ **repo** (full control of private repositories)
   - Copy the token (format: `ghp_...`)

2. **Add to vault**:
   ```yaml
   secrets:
     github_token: "ghp_your_actual_token_here"
   ```

This token will be automatically deployed to `~/.github_token` on containers that run deploywatch scripts.

### JWT Secrets to Add

JWT secrets are used for cross-application authentication. You can:

1. **Generate a shared JWT secret** (recommended for SSO):
   ```bash
   openssl rand -hex 32
   # Example output: a1b2c3d4e5f6...
   ```

2. **Add to vault file**:
   ```yaml
   secrets:
     # ... existing secrets ...
     
     agent-server:
       database_url: "postgresql://..."
       minio_access_key: "..."
       minio_secret_key: "..."
       redis_url: "redis://..."
       jwt_secret: "a1b2c3d4e5f6..."  # ← ADD THIS (your generated secret)
     
     agent-manager:
       database_url: "postgresql://..."
       agent_api_key: "..."
       session_secret: "..."
       jwt_secret: "a1b2c3d4e5f6..."  # ← Same secret for SSO
     
     doc-intel:
       database_url: "postgresql://..."
       openai_api_key: "..."
       better_auth_secret: "..."
       jwt_secret: "a1b2c3d4e5f6..."  # ← Same secret for SSO
     
     innovation:
       database_url: "postgresql://..."
       better_auth_secret: "..."
       openai_api_key: "..."
       jwt_secret: "a1b2c3d4e5f6..."  # ← Same secret for SSO
   ```

## Complete Secrets Structure

Here's the complete structure your vault needs:

```yaml
---
# Network Configuration (Deployment-Specific)
network_base_octets_production: "10.96.200"
network_base_octets_staging: "10.96.201"

# Domain Configuration (Deployment-Specific)
base_domain: "localhost"
ssl_email: "admin@localhost"

# Secrets (Deployment-Specific)
secrets:
  # GitHub Personal Access Token for private repository access
  github_token: "ghp_your_actual_token_here"
  postgresql:
    password: "YOUR_POSTGRES_PASSWORD"
    
  agent-server:
    database_url: "postgresql://busibox_user:YOUR_POSTGRES_PASSWORD@{{ postgres_host }}:{{ postgres_port }}/busibox"
    minio_access_key: "YOUR_MINIO_ACCESS_KEY"
    minio_secret_key: "YOUR_MINIO_SECRET_KEY"
    redis_url: "redis://{{ redis_host }}:{{ redis_port }}"
    jwt_secret: "YOUR_SHARED_JWT_SECRET_32_BYTES"  # REQUIRED
  
  ai-portal:
    database_url: "postgresql://busibox_user:YOUR_POSTGRES_PASSWORD@{{ postgres_host }}:{{ postgres_port }}/ai_portal"
    better_auth_secret: "YOUR_BETTER_AUTH_SECRET"
    resend_api_key: "YOUR_RESEND_API_KEY"
    sso_jwt_secret: "YOUR_SSO_JWT_SECRET"
    litellm_api_key: "YOUR_LITELLM_KEY"
  
  agent-manager:
    database_url: "postgresql://busibox_user:YOUR_POSTGRES_PASSWORD@{{ postgres_host }}:{{ postgres_port }}/agent_client"
    agent_api_key: "YOUR_AGENT_API_KEY"
    session_secret: "YOUR_SESSION_SECRET"
    jwt_secret: "YOUR_SHARED_JWT_SECRET_32_BYTES"  # REQUIRED (same as agent-server)
  
  doc-intel:
    database_url: "postgresql://busibox_user:YOUR_POSTGRES_PASSWORD@{{ postgres_host }}:{{ postgres_port }}/doc_intel"
    openai_api_key: "YOUR_OPENAI_API_KEY"
    better_auth_secret: "YOUR_DOC_INTEL_AUTH_SECRET"
    jwt_secret: "YOUR_SHARED_JWT_SECRET_32_BYTES"  # REQUIRED (same as agent-server)
  
  innovation:
    database_url: "postgresql://busibox_user:YOUR_POSTGRES_PASSWORD@{{ postgres_host }}:{{ postgres_port }}/innovation"
    better_auth_secret: "YOUR_INNOVATION_AUTH_SECRET"
    openai_api_key: "YOUR_OPENAI_API_KEY"  # Can be same as doc-intel
    jwt_secret: "YOUR_SHARED_JWT_SECRET_32_BYTES"  # REQUIRED (same as agent-server)
  
  litellm:
    master_key: "YOUR_LITELLM_MASTER_KEY"
    database_url: "postgresql://busibox_user:YOUR_POSTGRES_PASSWORD@{{ postgres_host }}:{{ postgres_port }}/litellm"
  
  letsencrypt:
    email: "YOUR_SSL_EMAIL"
```

## Generate All Secrets at Once

```bash
# Generate secrets
echo "JWT_SECRET=$(openssl rand -hex 32)"
echo "BETTER_AUTH_SECRET=$(openssl rand -hex 32)"
echo "SESSION_SECRET=$(openssl rand -hex 32)"
echo "SSO_JWT_SECRET=$(openssl rand -hex 32)"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 32)"
echo "MINIO_ACCESS_KEY=$(openssl rand -hex 32)"
echo "MINIO_SECRET_KEY=$(openssl rand -hex 32)"
echo "AGENT_API_KEY=$(openssl rand -hex 32)"
echo "LITELLM_MASTER_KEY=$(openssl rand -hex 32)"
```

Copy the output and use in your vault file.

## Important Notes

1. **Shared JWT Secret**: Use the SAME `jwt_secret` for `agent-server`, `agent-manager`, `doc-intel`, and `innovation` to enable cross-app authentication (SSO)

2. **Database URLs**: Replace `YOUR_POSTGRES_PASSWORD` with the actual password (or use the variable substitution if it works in your setup)

3. **Keep Vault Encrypted**: After editing:
   ```bash
   # Vault is automatically re-encrypted when you save with ansible-vault edit
   # To verify it's encrypted:
   head -1 roles/secrets/vars/vault.yml
   # Should show: $ANSIBLE_VAULT;1.1;AES256
   ```

4. **Required vs Optional**:
   - **Required** (deployment will fail): `jwt_secret`, `database_url`
   - **Optional** (app-specific features): `openai_api_key`, `resend_api_key`

## Quick Fix

The fastest way to fix your current error:

```bash
# On server
cd ~/busibox/provision/ansible

# Generate ONE shared JWT secret
JWT_SECRET=$(openssl rand -hex 32)
echo "Use this JWT secret: $JWT_SECRET"

# Edit vault
ansible-vault edit roles/secrets/vars/vault.yml

# Add jwt_secret to each app:
# agent-server:
#   jwt_secret: "paste-the-generated-secret-here"
# agent-manager:
#   jwt_secret: "paste-the-same-secret-here"
# doc-intel:
#   jwt_secret: "paste-the-same-secret-here"
# innovation:
#   jwt_secret: "paste-the-same-secret-here"

# Save and exit

# Re-run playbook
ansible-playbook -i inventory/test site.yml --ask-vault-pass
```

