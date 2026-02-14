---
created: 2026-01-18
updated: 2026-01-18
status: active
category: development
---

# Environment File Consolidation

## Overview

Consolidated multiple confusing environment example files into a single `env.example` file for local development only. Production/staging deployments now exclusively use Ansible-generated configuration.

## Changes Made

### Agent-Manager Repository

**Removed:**
- `env.local.example` - Redundant local development example
- `env.production.example` - Obsolete; production uses Ansible

**Updated:**
- `env.example` - Comprehensive example for local development only
- `.gitignore` - Now explicitly allows `env.example` to be tracked
- `SETUP.md` - Updated to reference `env.example`
- `docs/guides/AUTHENTICATION.md` - Updated to reference `env.example`

## New Convention

### Local Development
**File**: `env.example` → copy to `.env.local`

Contains all configuration needed to run agent-manager locally, with options for:
- Connecting to local Docker services
- Connecting to remote Proxmox services
- Complete setup instructions inline

### Docker/Proxmox Deployments
**Source**: Ansible vault → auto-generated `.env`

Environment variables are automatically generated from:
- `busibox/provision/ansible/group_vars/all/apps.yml` - Non-secret env vars
- `busibox/provision/ansible/roles/secrets/vars/vault.yml` - Secret values

**Users should NEVER create or edit `.env` files manually on deployed environments.**

## Benefits

### Before (Confusing)
```
env.example              - Generic example
env.local.example        - Local dev example  
env.production.example   - Production example (obsolete)
```

Developers had to figure out which file to use, and production examples could get out of sync with actual Ansible configuration.

### After (Clear)
```
env.example              - Single source for local dev
```

- One file to maintain
- Clear instructions for both local Docker and remote Proxmox
- Production config is exclusively in Ansible (single source of truth)
- No confusion about which example to use

## Migration Guide

### For Developers

**Old workflow:**
```bash
cp env.local.example .env.local
# or
cp env.production.example .env.production
```

**New workflow:**
```bash
cp env.example .env.local
# Edit .env.local as needed for your setup
```

### For Production/Staging

**No change required.** Deployments continue to use Ansible-generated config.

If you were manually editing `.env` files on servers (don't do this!), you should instead:
1. Update values in Ansible vault
2. Redeploy the application

## Environment Variable Sources

### Local Development
```
env.example (copy to .env.local)
  ↓
.env.local (gitignored, local-only)
  ↓
Your local dev server
```

### Docker (Local)
```
busibox/docker-compose.local.yml
  ↓
Environment variables injected into containers
```

### Proxmox (Production/Staging)
```
Ansible vault
  ↓
apps.yml (non-secret env vars)
  ↓
app_deployer role generates .env on server
  ↓
Application reads .env at runtime
```

## What Goes Where

### env.example (Local Dev Only)
- Service URLs (localhost or Proxmox IPs)
- Development credentials
- Debug flags
- Local-only settings (NODE_TLS_REJECT_UNAUTHORIZED=0)
- Setup instructions

### Ansible apps.yml (All Deployments)
- Non-secret environment variables
- Service URLs (using Ansible variables)
- Feature flags
- Node environment (development/production)
- Port configuration

### Ansible vault.yml (All Deployments)
- Database credentials
- API keys
- OAuth secrets
- JWT secrets
- Email credentials
- Admin tokens

## Files to Update for New Environment Variables

When adding a new environment variable:

1. **If secret (password, API key, etc.):**
   - Add to `provision/ansible/roles/secrets/vars/vault.yml`
   - Add to `provision/ansible/roles/secrets/vars/vault.example.yml`
   - Add to app's `secrets` list in `provision/ansible/group_vars/all/apps.yml`
   - Add example to `env.example` (with placeholder value)

2. **If non-secret (URL, flag, etc.):**
   - Add to app's `env` section in `provision/ansible/group_vars/all/apps.yml`
   - Add to `env.example` with appropriate local dev value

3. **If local-dev only (debug flag, TLS skip, etc.):**
   - Add ONLY to `env.example`
   - Do not add to Ansible config

## Related Documentation

- `provision/ansible/group_vars/all/apps.yml` - App environment configuration
- `provision/ansible/roles/secrets/vars/vault.example.yml` - Secret templates
- `docs/deployment/agent-manager-env-vars.md` - Missing environment variables added
- `agent-manager/env.example` - Local development configuration

## Validation

After these changes:

### Local Development Still Works
```bash
cd agent-manager
cp env.example .env.local
# Edit .env.local
npm run dev
# ✓ Should work as before
```

### Production Deployment Unchanged
```bash
cd busibox/provision/ansible
ansible-playbook -i inventory/staging/hosts.yml site.yml --tags app_deployer -e "deploy_app=agent-manager"
# ✓ Generates .env from vault as before
```

### No Committed Secrets
```bash
git status
# ✓ Only env.example should be tracked
# ✓ .env.local remains gitignored
```

## Future Applications

This same pattern should be applied to other applications:
- **ai-portal**: Consolidate to single `env.example`
- **doc-intel**: (if deployed via Ansible in future)
- **foundation**: (if deployed via Ansible in future)
- **project-analysis**: (if deployed via Ansible in future)

All production/staging deployments should use Ansible-generated configuration exclusively.
