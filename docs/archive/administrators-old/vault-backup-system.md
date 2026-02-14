---
title: "Vault Backup System"
category: "administrator"
order: 42
description: "Timestamped backup system for Ansible Vault sync operations"
published: true
---

# Vault Sync: Timestamped Backup System

## Enhancement

The vault sync tool now creates **timestamped backups** instead of overwriting `vault.backup.yml` each time, preventing loss of previous backup files.

## Before vs After

### Before (Problematic)
```
roles/secrets/vars/
├── vault.yml (active)
├── vault.backup.yml (overwritten each sync)
└── vault.removed.yml (overwritten each sync)
```

**Problem:** Running sync multiple times would overwrite previous backups, losing ability to restore from earlier states.

### After (Safe)
```
roles/secrets/vars/
├── vault.yml (active)
└── backups/
    ├── vault.backup.20260118-140523.yml
    ├── vault.backup.20260118-153012.yml
    ├── vault.removed.20260118-140523.yml
    └── vault.removed.20260118-153012.yml
```

**Benefit:** Every sync creates a new timestamped backup. You can restore from any previous state or compare changes over time.

## Timestamp Format

**Format:** `YYYYMMDD-HHMMSS`

**Example:** `20260118-153012` = January 18, 2026 at 3:30:12 PM

**Files:**
- `vault.backup.20260118-153012.yml` - Full vault before that sync
- `vault.removed.20260118-153012.yml` - Unmapped secrets from that sync

## Implementation

### Changes Made

**File:** `scripts/vault/sync-vault.sh`

1. Added timestamp generation:
   ```bash
   TIMESTAMP=$(date +%Y%m%d-%H%M%S)
   ```

2. Created backup directory structure:
   ```bash
   VAULT_BACKUP_DIR="${ANSIBLE_DIR}/roles/secrets/vars/backups"
   VAULT_BACKUP="${VAULT_BACKUP_DIR}/vault.backup.${TIMESTAMP}.yml"
   VAULT_REMOVED_BACKUP="${VAULT_BACKUP_DIR}/vault.removed.${TIMESTAMP}.yml"
   ```

3. Create directory before backup:
   ```bash
   mkdir -p "$VAULT_BACKUP_DIR"
   ```

4. Updated reporting to show timestamped filenames

### .gitignore Updated

Added to ignore all backup files:
```gitignore
provision/ansible/roles/secrets/vars/backups/
```

Backups are local-only and never committed (they contain secrets).

## Usage Examples

### Restore from Recent Backup

```bash
cd provision/ansible/roles/secrets/vars

# List available backups (most recent first)
ls -lt backups/vault.backup.*.yml

# Restore from specific backup
cp backups/vault.backup.20260118-140523.yml vault.yml

# Verify
cd ../../../..
ansible-vault view provision/ansible/roles/secrets/vars/vault.yml
```

### Compare Vault Changes

```bash
cd provision/ansible/roles/secrets/vars

# Decrypt two backups to compare
ansible-vault decrypt backups/vault.backup.20260118-140523.yml --output=/tmp/vault1.yml
ansible-vault decrypt backups/vault.backup.20260118-153012.yml --output=/tmp/vault2.yml

# Compare
diff /tmp/vault1.yml /tmp/vault2.yml

# Cleanup
rm /tmp/vault1.yml /tmp/vault2.yml
```

### Audit Vault Evolution

```bash
cd provision/ansible/roles/secrets/vars/backups

# See when syncs happened
ls -lh vault.backup.*.yml

# View what was removed at each sync
for f in vault.removed.*.yml; do
    echo "=== $f ==="
    ansible-vault view "$f" | grep '^  [a-z_]' | sed 's/:.*$//'
done
```

## Backup Management

### Recommended Retention

**Development:**
- Keep all backups (disk space is cheap)
- Clean up when backups/ exceeds 100MB

**Production:**
- Keep backups for 90 days minimum
- Keep all backups from major version changes permanently
- Document backup timestamps in deployment logs

### Cleanup Commands

**By Age:**
```bash
cd provision/ansible/roles/secrets/vars/backups

# Remove backups older than 30 days
find . -name "vault.*.yml" -mtime +30 -delete
```

**By Count:**
```bash
# Keep only last 10 backups of each type
ls -t vault.backup.*.yml | tail -n +11 | xargs rm -f
ls -t vault.removed.*.yml | tail -n +11 | xargs rm -f
```

**Archive Old Backups:**
```bash
# Create archive of old backups
tar czf vault-backups-archive-$(date +%Y%m).tar.gz \
    $(find . -name "vault.*.yml" -mtime +90)

# Remove files now in archive
find . -name "vault.*.yml" -mtime +90 -delete
```

## Safety Features

### 1. Never Lose Previous Backups

Each sync creates a new backup file instead of overwriting. Even if you run sync multiple times in quick succession, each run preserves its state.

### 2. Timestamped Filenames

Timestamps ensure:
- Unique filenames (no overwrites)
- Chronological sorting (`ls -t`)
- Easy identification of when sync happened
- Correlation with deployment logs

### 3. Backup Before Any Changes

The backup is created **before** applying changes to the vault. If sync fails, your original vault remains unchanged.

### 4. Encrypted Backups

All backup files are encrypted with ansible-vault using your vault password. They're as secure as your main vault.

## Restoring from Backup

### Simple Restore

```bash
cd provision/ansible/roles/secrets/vars
cp backups/vault.backup.20260118-140523.yml vault.yml
```

### Merge Approach

If you want to selectively restore some secrets:

```bash
# Decrypt both vaults
ansible-vault decrypt vault.yml --output=/tmp/current.yml
ansible-vault decrypt backups/vault.backup.20260118-140523.yml --output=/tmp/backup.yml

# Manually merge (copy specific secrets from backup to current)
# Edit /tmp/current.yml

# Re-encrypt
ansible-vault encrypt /tmp/current.yml --output=vault.yml

# Cleanup
rm /tmp/current.yml /tmp/backup.yml
```

## Monitoring Backups

### Check Backup Size

```bash
cd provision/ansible/roles/secrets/vars/backups
du -sh .
# Example output: 2.4M

# If too large, clean up old backups
```

### Count Backups

```bash
ls -1 vault.backup.*.yml | wc -l
# Keep under 20-30 for easy management
```

### List Recent Backups

```bash
ls -lt vault.backup.*.yml | head -5
```

## Integration with Git

The `backups/` directory is gitignored:
```gitignore
provision/ansible/roles/secrets/vars/backups/
```

This means:
- ✅ Backups are local-only (secure)
- ✅ Not committed to repository
- ✅ Each developer/server has their own backups
- ❌ Backups are not shared across team (by design - they contain secrets)

For team backup/sharing, use your organization's secure secret storage system.

## Related Files

- `scripts/vault/sync-vault.sh` - Vault sync script
- `provision/ansible/roles/secrets/vars/backups/README.md` - Backup directory guide
- `.gitignore` - Excludes backups from git
- `docs/configuration/vault-sync.md` - Main sync documentation

## Best Practices

1. **Before major changes:** Run sync to create a known-good backup
2. **After sync:** Test deployment before cleaning up backups
3. **Regular cleanup:** Monthly review of backup count/size
4. **Archive strategy:** Compress and archive backups older than 90 days
5. **Document syncs:** Note backup timestamps in deployment logs for critical changes
