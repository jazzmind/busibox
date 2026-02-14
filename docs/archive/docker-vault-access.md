---
created: 2026-01-18
updated: 2026-01-18
status: resolved
category: development
---

# Docker Configuration Menu - Vault Editing Access

## Problem

When running `make` → Configure on local Docker environment, the vault editing options were not available. Developers could not edit the Ansible vault to update secrets when working locally.

## Root Cause

The vault editing functionality was only available in the `secrets_configuration()` function, which was only accessible from the `proxmox_menu()`, not from the `docker_menu()`.

**Before:**
```
Docker Menu:
1. App Configuration
2. Back to Main Menu

Proxmox Menu:
1. Verify Configuration
2. Model Configuration
3. Container Configuration
4. App Configuration
5. Secrets & Keys ← Vault editing here
6. Back
```

## Solution

Added vault editing options directly to the Docker menu so they're accessible regardless of backend.

**After:**
```
Docker Menu:
1. App Configuration
2. Edit Ansible Vault (secrets)      ← NEW
3. View Vault Variables (masked)     ← NEW
4. Back to Main Menu

Proxmox Menu:
(unchanged - still has Secrets & Keys submenu)
```

## Why Vault Editing is Needed for Docker

Even when running locally with Docker, developers may need to edit the vault to:

1. **Update secrets** for local testing
2. **Add new secrets** when developing features that require them
3. **Prepare secrets** for deployment to staging/production
4. **Test vault-based configuration** before deploying

The vault file is shared between Docker and Proxmox deployments, so having easy access to edit it locally is important.

## Changes Made

**File:** `scripts/make/configure.sh`

**Modified:** `docker_menu()` function (lines 743-766)

**Added:**
- Option 2: Edit Ansible Vault (opens vault in editor)
- Option 3: View Vault Variables (shows keys with masked values)

Both options work the same way as in the Proxmox menu:
```bash
ansible-vault edit roles/secrets/vars/vault.yml   # Edit
ansible-vault view roles/secrets/vars/vault.yml   # View
```

## Usage

```bash
# From busibox directory
make
# Select Configure
# Now options 2 & 3 are available for vault access
```

### Edit Vault
- Opens vault in your default editor (via `$EDITOR`)
- Prompts for vault password if not in `~/.vault_pass`
- Saves changes encrypted

### View Vault
- Shows variable keys with values masked: `secret_key: <masked>`
- Useful for seeing what secrets are defined without exposing values
- Read-only (no changes saved)

## Benefits

1. **Consistency**: Same vault editing options available in both Docker and Proxmox
2. **Developer workflow**: Can edit secrets locally without switching to Proxmox
3. **Faster iteration**: Update secrets and test immediately with `make docker-up`
4. **Better DX**: No need to remember separate commands or paths

## Related Documentation

- `provision/ansible/roles/secrets/vars/vault.example.yml` - Vault template
- `docs/deployment/agent-manager-env-vars.md` - Environment variable reference
- `scripts/make/configure.sh` - Configuration menu script

## Vault Password Setup

For easier vault editing, create a password file:

```bash
echo 'your-vault-password' > ~/.vault_pass
chmod 600 ~/.vault_pass
```

Then ansible-vault commands won't prompt for password each time.
