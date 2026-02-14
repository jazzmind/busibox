---
title: "Vault as Source of Truth"
category: "administrator"
order: 41
description: "Ansible Vault as single source of truth for all secrets across environments"
published: true
---

# Ansible Vault as Single Source of Truth for Secrets

## Overview

**Busibox now uses Ansible Vault as the single source of truth for all secrets across all environments (local Docker, staging, production).**

This eliminates redundancy, reduces security risks, and ensures consistency. The `.env.local` file for Docker development is **auto-generated** from the vault.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Ansible Vault (Encrypted)                 │
│              provision/ansible/roles/secrets/vars/vault.yml  │
│                                                               │
│  • Single source of truth for ALL secrets                    │
│  • Encrypted with ansible-vault                              │
│  • Version controlled (encrypted)                            │
│  • Consistent across all environments                        │
└──────────────────┬─────────────────┬────────────────────────┘
                   │                 │
       ┌───────────▼─────────┐      │
       │  Local Docker        │      │
       │  (.env.local)        │      │
       │  AUTO-GENERATED      │      │
       └──────────────────────┘      │
                                     │
                         ┌───────────▼─────────────┐
                         │  Remote Deployments     │
                         │  (Staging/Production)   │
                         │  Ansible Templates      │
                         └─────────────────────────┘
```

## Why This Approach?

### Before (Problematic)
```
busibox/
├── .env.local                    # Local secrets (manual)
└── provision/ansible/
    └── roles/secrets/vars/
        └── vault.yml             # Production secrets (encrypted)
```

**Problems:**
- ❌ Secrets duplicated in multiple places
- ❌ Easy to forget to update one when changing another
- ❌ `.env.local` might be accidentally committed
- ❌ No single source of truth
- ❌ Different secrets for local vs remote (inconsistent testing)

### After (Improved)
```
busibox/
├── .env.local                    # AUTO-GENERATED from vault
└── provision/ansible/
    └── roles/secrets/vars/
        └── vault.yml             # ONLY place secrets live
```

**Benefits:**
- ✅ Single source of truth (vault.yml)
- ✅ All secrets encrypted
- ✅ Consistent across all environments
- ✅ `.env.local` is ephemeral (can be regenerated anytime)
- ✅ Easier to manage and audit secrets

## Workflow

### Initial Setup (One-Time)

**If you have an existing `.env.local` file:**

```bash
# 1. Migrate your existing .env.local to vault
make vault-migrate

# 2. Verify the vault
cd provision/ansible
ansible-vault view --vault-password-file ~/.vault_pass roles/secrets/vars/vault.yml

# 3. Delete old .env.local (backup is in backups/)
rm .env.local
```

**If starting fresh:**

```bash
# 1. Create vault password file
echo "your-vault-password" > ~/.vault_pass
chmod 600 ~/.vault_pass

# 2. Edit the vault and add your secrets
cd provision/ansible
ansible-vault edit --vault-password-file ~/.vault_pass roles/secrets/vars/vault.yml

# 3. Generate .env.local from vault
make vault-generate-env
```

### Daily Development

**Before starting Docker:**

```bash
# Generate/update .env.local from vault
make vault-generate-env

# Start services
make docker-up
```

**To change secrets:**

```bash
# 1. Edit the vault
cd provision/ansible
ansible-vault edit --vault-password-file ~/.vault_pass roles/secrets/vars/vault.yml

# 2. Regenerate .env.local
make vault-generate-env

# 3. Restart services to pick up changes
make docker-restart
```

## Commands

### Generate .env.local from Vault

```bash
make vault-generate-env
```

- Decrypts vault
- Extracts secrets relevant for local Docker
- Generates `.env.local`
- Overwrites existing `.env.local`

**Use this:**
- Before `make docker-up`
- After editing the vault
- When switching git branches

### Migrate .env.local to Vault

```bash
make vault-migrate
```

- One-time operation
- Reads your current `.env.local`
- Merges values into vault structure
- Creates encrypted vault
- Backs up both files

**Use this:**
- Initial setup (if you have existing `.env.local`)
- Only run once

### Sync Vault Structure

```bash
make vault-sync
```

- Updates vault to match `vault.example.yml` structure
- Preserves your existing secret values
- Identifies removed/missing secrets
- Creates timestamped backups

**Use this:**
- After pulling updates that change vault structure
- When vault.example.yml is updated

## File Locations

```
busibox/
├── .env.local                            # AUTO-GENERATED (gitignored)
├── .env.local.old                        # Manual backup if you moved it
├── scripts/vault/
│   ├── generate-env-from-vault.sh        # Generate .env.local from vault
│   ├── migrate-env-to-vault.sh           # Migrate .env.local to vault
│   └── sync-vault.sh                     # Sync vault structure
└── provision/ansible/
    └── roles/secrets/vars/
        ├── vault.yml                     # ENCRYPTED secrets (gitignored)
        ├── vault.example.yml             # Template (committed)
        └── backups/                      # Timestamped backups (gitignored)
            ├── vault.backup.YYYYMMDD-HHMMSS.yml
            ├── vault.removed.YYYYMMDD-HHMMSS.yml
            └── env.local.backup.YYYYMMDD-HHMMSS
```

## How .env.local is Generated

The `scripts/vault/generate-env-from-vault.sh` script:

1. **Decrypts vault** using `~/.vault_pass` or prompts for password
2. **Extracts relevant secrets:**
   - PostgreSQL credentials → `POSTGRES_PASSWORD`
   - MinIO credentials → `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`
   - LLM API keys → `OPENAI_API_KEY`, `BEDROCK_API_KEY`, `LITELLM_API_KEY`
   - AuthZ secrets → `AUTHZ_ADMIN_TOKEN`, `AUTHZ_MASTER_KEY`
   - AI Portal secrets → `BETTER_AUTH_SECRET`, `SSO_JWT_SECRET`
   - Agent Manager secrets → `AGENT_MANAGER_CLIENT_ID`, `AGENT_MANAGER_CLIENT_SECRET`
   - GitHub token → `GITHUB_AUTH_TOKEN`
3. **Adds development defaults:**
   - `POSTGRES_USER=busibox_user` (hardcoded for local)
   - `LITELLM_BASE_URL=http://localhost:4000/v1` (Docker network)
   - `MARKER_ENABLED=true`, `COLPALI_ENABLED=false` (feature flags)
4. **Writes** to `.env.local` with header indicating auto-generation

## Docker Compose Integration

The `docker-compose.local.yml` file loads `.env.local`:

```yaml
services:
  authz-api:
    env_file:
      - .env.local  # Auto-generated from vault
    environment:
      # Service-specific overrides can go here
      POSTGRES_DB: authz
```

All secrets come from `.env.local`, which comes from the vault.

## Security Best Practices

### ✅ DO

- **Store vault password securely:** `~/.vault_pass` with `chmod 600`
- **Commit vault.yml to git:** It's encrypted, safe to commit
- **Regenerate .env.local regularly:** Keep it in sync with vault
- **Use different vault passwords** for staging vs production
- **Backup vault files:** Automated timestamped backups in `backups/`

### ❌ DON'T

- **Don't edit .env.local manually:** It will be overwritten
- **Don't commit .env.local:** Already gitignored
- **Don't share vault password:** Each team member should have vault access
- **Don't commit ~/.vault_pass:** Personal file, not in repo

## Troubleshooting

### "Vault file not found"

```bash
# Create vault from example
cd provision/ansible
cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml

# Edit and add your secrets
ansible-vault encrypt roles/secrets/vars/vault.yml
```

### "Failed to decrypt vault"

```bash
# Check vault password file
cat ~/.vault_pass

# Or use interactive password
cd provision/ansible
ansible-vault edit --ask-vault-pass roles/secrets/vars/vault.yml
```

### ".env.local is stale"

```bash
# Regenerate from vault
make vault-generate-env

# Restart services
make docker-restart
```

### "I accidentally deleted .env.local"

```bash
# No problem! Regenerate it
make vault-generate-env
```

### "Vault and .env.local are out of sync"

```bash
# The vault is the source of truth
# Regenerate .env.local from vault
make vault-generate-env
```

## Migration Checklist

If you're transitioning from manual `.env.local` management:

- [ ] Backup your current `.env.local`: `cp .env.local .env.local.backup`
- [ ] Run migration: `make vault-migrate`
- [ ] Verify vault contents: `ansible-vault view provision/ansible/roles/secrets/vars/vault.yml`
- [ ] Test generation: `make vault-generate-env`
- [ ] Compare generated vs original: `diff .env.local .env.local.backup`
- [ ] Test Docker: `make docker-restart`
- [ ] Verify services work
- [ ] Delete old `.env.local.backup` once satisfied
- [ ] Update team documentation/onboarding

## Integration with Ansible Deployments

For **Staging/Production deployments**, Ansible templates use the vault directly:

```jinja2
# roles/ingest_api/templates/ingest-api.env.j2
DATABASE_URL=postgresql://{{ postgres_user }}:{{ secrets.postgresql.password }}@{{ postgres_host }}/ingest
LITELLM_API_KEY={{ secrets.litellm_api_key }}
```

The **same secrets** from the vault are used for:
- Local Docker (via `.env.local`)
- Remote deployments (via Ansible templates)

This ensures consistency and reduces configuration drift.

## Future Enhancements

Potential improvements:

1. **Auto-regenerate on vault changes:**
   - Git hook to regenerate `.env.local` after `git pull` if vault changed
   
2. **Per-environment vaults:**
   - `vault.local.yml` - Local development secrets
   - `vault.staging.yml` - Staging secrets
   - `vault.production.yml` - Production secrets
   
3. **Vault validation:**
   - Script to verify all required secrets are present
   - Check for placeholder values (`CHANGE_ME_*`)
   
4. **IDE integration:**
   - VS Code task to regenerate `.env.local`
   - Auto-completion for vault keys

## Related Documentation

- `docs/configuration/vault-sync.md` - Vault structure synchronization
- `docs/configuration/vault-backup-system.md` - Backup and restore
- `provision/ansible/roles/secrets/vars/vault.example.yml` - Vault template
- `provision/ansible/roles/secrets/vars/backups/README.md` - Backup directory guide

## Summary

**Old way:**
- Manually maintain `.env.local` and `vault.yml` separately
- Risk of inconsistency and duplication

**New way:**
- Edit vault → Generate `.env.local` → Start Docker
- Single source of truth, consistent everywhere
