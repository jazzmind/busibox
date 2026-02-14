---
title: "Vault Sync"
category: "administrator"
order: 43
description: "Keep Ansible vault in sync with vault.example.yml structure"
published: true
---

# Vault Sync with Example

## Overview

The `sync-vault` command keeps your Ansible vault in sync with the canonical `vault.example.yml` structure. As the example file evolves (new secrets added, old ones removed, structure reorganized), this tool automatically updates your vault to match while preserving your secret values.

## Purpose

### The Problem

Over time, your vault can drift from the example structure:
- New secrets are added to the example but missing from your vault
- Old secrets exist in your vault but are removed from the example
- Structure changes (secrets are moved or reorganized)
- You're not sure if you have all the required secrets

### The Solution

The sync tool:
1. **Maps** your current secret values to the new example structure
2. **Preserves** all your existing secret values (never loses data)
3. **Identifies** secrets that don't fit the new structure (saved separately)
4. **Reports** which secrets still need values (have placeholders)
5. **Backs up** your current vault before making changes

## Usage

### Via Make Menu (Recommended)

```bash
make
# Select Configure
# Choose "Sync Vault with Example" (option 4)
```

### Direct Command

```bash
bash scripts/vault/sync-vault.sh
```

## What It Does

### Step-by-Step Process

1. **Decrypts** your current vault (prompts for password if needed)

2. **Analyzes** both files:
   - Current vault structure and values
   - Example vault structure and placeholders

3. **Maps** secrets from current to example:
   ```yaml
   # Current vault has:
   secrets.postgresql.password: "prod-password"
   
   # Example has same structure:
   secrets.postgresql.password: "CHANGE_ME_PASSWORD"
   
   # Result: Maps your password to new structure
   secrets.postgresql.password: "prod-password"
   ```

4. **Identifies unmapped** secrets:
   ```yaml
   # Current vault has:
   old_service.api_key: "old-key"
   
   # Example doesn't have old_service
   # Result: Moved to vault.removed.yml
   ```

5. **Reports missing** secrets:
   ```yaml
   # Example has:
   new_service.api_key: "CHANGE_ME_KEY"
   
   # Current vault doesn't have new_service
   # Result: Kept in new vault with placeholder, reported as missing
   ```

6. **Creates** new vault:
   - Same structure as example
   - Filled with your current values where they map
   - Placeholders for new secrets
   - Encrypted with same password

7. **Saves** removed secrets:
   - Encrypted to `vault.removed.yml`
   - Can be reviewed later
   - Safe to delete if confirmed obsolete

### Example Output

```
┌────────────────────────────────────────────────────────────────────┐
│                    Sync Vault with Example                         │
└────────────────────────────────────────────────────────────────────┘

This will:
  1. Decrypt your current vault
  2. Map secrets to vault.example.yml structure
  3. Create a new vault with updated structure
  4. Save unmapped secrets to vault.removed.yml
  5. Report secrets that need to be added

⚠  Your current vault will be backed up to vault.backup.yml

Continue with vault sync? (y/n): y

──────────────────────────────────────────────────────────────────────
ℹ  Decrypting current vault...
✓ Vault decrypted

ℹ  Creating synced vault...
✓ Vault structure synced

──────────────────────────────────────────────────────────────────────
ℹ  Sync completed. Summary:

  Current vault: 42 secrets
  New vault:     45 secrets

⚠ 3 secret(s) don't map to new structure

Removed secrets:
    - old_litellm.legacy_key
    - deprecated_service.token
    - test_credentials.user

⚠ 5 secret(s) need values (still have placeholders)

Missing secrets:
    - agent-manager.litellm_api_key
    - agent-manager.resend_api_key
    - agent-manager.email_from
    - new_service.api_key
    - new_service.secret_key

──────────────────────────────────────────────────────────────────────

Apply these changes? (y/n): y

ℹ  Applying changes...
✓ Current vault backed up to vault.backup.yml
✓ New vault saved
✓ Removed secrets saved to vault.removed.yml (encrypted)

──────────────────────────────────────────────────────────────────────
✓ Vault sync complete!
──────────────────────────────────────────────────────────────────────

ℹ  Next steps:

  1. Review removed secrets:
     cd provision/ansible
     ansible-vault view roles/secrets/vars/vault.removed.yml

  2. Add missing secrets:
     cd provision/ansible
     ansible-vault edit roles/secrets/vars/vault.yml

  Secrets that need values:
     - agent-manager.litellm_api_key
     - agent-manager.resend_api_key
     - agent-manager.email_from
     - new_service.api_key
     - new_service.secret_key

  3. Test the new vault:
     make configure → Verify Configuration

  Backups available at:
     provision/ansible/roles/secrets/vars/backups/
     - vault.backup.YYYYMMDD-HHMMSS.yml (your previous vault)
     - vault.removed.YYYYMMDD-HHMMSS.yml (unmapped secrets, if any)
```

## Use Cases

### 1. After Updating Busibox Repository

```bash
git pull origin main
# New secrets added to vault.example.yml

make configure
# Select "Sync Vault with Example"
# Tool identifies what's new and what changed
```

### 2. After Renaming Services

Example: `agent-manager` → `agent-manager`

The sync tool:
- Creates new `agent-manager` section with example structure
- Keeps old `agent-manager` secrets in `vault.removed.yml`
- You manually copy values from removed to new structure
- Delete `vault.removed.yml` when done

### 3. Cleaning Up Old Secrets

```bash
# Run sync to identify obsolete secrets
make configure → Sync Vault with Example

# Review what was removed
cd provision/ansible
ansible-vault view roles/secrets/vars/vault.removed.yml

# Confirm they're obsolete and delete
rm roles/secrets/vars/vault.removed.yml
```

### 4. Onboarding New Environment

```bash
# Start with vault.example.yml
cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml
ansible-vault encrypt roles/secrets/vars/vault.yml

# Edit with your values
ansible-vault edit roles/secrets/vars/vault.yml

# Sync verifies you have everything
make configure → Sync Vault with Example
# Reports any placeholders still needing values
```

## Files Created

| File | Purpose | Location | When |
|------|---------|----------|------|
| `vault.yml` | Updated vault (replaces original) | `roles/secrets/vars/` | After confirmation |
| `vault.backup.YYYYMMDD-HHMMSS.yml` | Timestamped backup | `roles/secrets/vars/backups/` | Before changes |
| `vault.removed.YYYYMMDD-HHMMSS.yml` | Unmapped secrets | `roles/secrets/vars/backups/` | If any unmapped |

All files use the same vault password.

**Backup Directory:** `provision/ansible/roles/secrets/vars/backups/`

This directory accumulates timestamped backups from each sync operation, allowing you to:
- Restore from any previous state
- Compare changes over time
- Audit vault evolution

**Example:**
```
backups/
├── vault.backup.20260118-140523.yml
├── vault.backup.20260118-153012.yml
├── vault.removed.20260118-140523.yml
└── vault.removed.20260118-153012.yml
```

### Cleanup Old Backups

Backups are never automatically deleted. To clean up old backups:

```bash
cd provision/ansible/roles/secrets/vars/backups

# List backups
ls -lh

# Remove backups older than 30 days
find . -name "vault.backup.*.yml" -mtime +30 -delete
find . -name "vault.removed.*.yml" -mtime +30 -delete

# Or keep only last 10 backups
ls -t vault.backup.*.yml | tail -n +11 | xargs rm -f
ls -t vault.removed.*.yml | tail -n +11 | xargs rm -f
```

## Safety Features

### 1. No Data Loss

- Original vault backed up with timestamp before any changes
- Unmapped secrets saved to timestamped `vault.removed.YYYYMMDD-HHMMSS.yml`
- Backups accumulate in `backups/` directory (never auto-deleted)
- Can restore from any previous backup:
  ```bash
  cd provision/ansible/roles/secrets/vars
  cp backups/vault.backup.20260118-140523.yml vault.yml
  ```

### 2. Confirmation Required

- Shows summary before applying changes
- You can cancel after seeing what would change
- No surprises

### 3. Encrypted Throughout

- All intermediate files encrypted
- Removed secrets file encrypted
- Backup encrypted
- Vault password never stored in plaintext

### 4. Validation

- Reports missing secrets (need values)
- Reports removed secrets (review needed)
- Counts secrets to verify nothing lost

## Troubleshooting

### "Failed to decrypt vault (check password)"

**Problem:** Wrong vault password

**Solution:**
```bash
# Try manually
cd provision/ansible
ansible-vault view roles/secrets/vars/vault.yml

# If that works, try sync again
```

### "Vault file is not encrypted"

**Problem:** Current vault is in plaintext

**Solution:**
```bash
cd provision/ansible
ansible-vault encrypt roles/secrets/vars/vault.yml
```

### "Example file not found"

**Problem:** Missing `vault.example.yml`

**Solution:**
```bash
# Update from git
git pull

# Or restore from template
cd provision/ansible/roles/secrets/vars
# File should be version controlled
```

### Python YAML Errors

**Problem:** Invalid YAML in current vault or example

**Solution:**
```bash
# Validate vault YAML
cd provision/ansible
ansible-vault view roles/secrets/vars/vault.yml | python3 -c "import yaml, sys; yaml.safe_load(sys.stdin)"

# Validate example YAML
python3 -c "import yaml; yaml.safe_load(open('roles/secrets/vars/vault.example.yml'))"
```

## Best Practices

### 1. Run Sync Regularly

- After pulling updates from git
- Before major deployments
- When adding new services
- Quarterly maintenance

### 2. Review Removed Secrets

Always check `vault.removed.yml` before deleting:
```bash
cd provision/ansible
ansible-vault view roles/secrets/vars/vault.removed.yml

# Keep backup until confirmed obsolete
# Delete only when certain secrets aren't needed
```

### 3. Update Example as Source of Truth

When adding new secrets:
1. Update `vault.example.yml` first
2. Run sync to update your vault
3. Fill in the placeholder values

This keeps the example canonical.

### 4. Test After Sync

```bash
# Verify vault structure
make configure → Verify Configuration

# Test with actual deployment
make deploy-apps  # or specific service
```

## Integration with Existing Tools

### Works With

- **ansible-vault edit**: Edit the synced vault normally
- **ansible-vault view**: View synced vault
- **Deployment playbooks**: Use synced vault seamlessly
- **CI/CD**: Can be automated (with `--vault-password-file`)

### Part of Workflow

```
vault.example.yml (canonical)
    ↓
  sync-vault.sh
    ↓
vault.yml (your secrets)
    ↓
Ansible playbooks
    ↓
Deployed applications
```

## Related Documentation

- `provision/ansible/roles/secrets/vars/vault.example.yml` - Canonical structure
- `docs/deployment/agent-manager-env-vars.md` - Secret reference
- `scripts/vault/sync-vault.sh` - Sync script source
- `scripts/make/configure.sh` - Configuration menu

## Technical Details

### Secret Path Mapping

Secrets are mapped by their full path:

```yaml
secrets:
  postgresql:
    password: "value"
```

Path: `secrets.postgresql.password`

The tool flattens both structures, compares paths, then unflattens with mapped values.

### Placeholder Detection

A secret is considered "missing" (needs a value) if it contains:
- `CHANGE_ME`
- `your-`
- Ends with `-here`

These are common placeholders in the example file.

### Structure Preservation

Comments from `vault.example.yml` are preserved in the new vault where possible. YAML structure (indentation, order) follows the example.
