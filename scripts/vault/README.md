# Vault Management Scripts

Scripts for managing Ansible vault as the single source of truth for all secrets.

## Scripts

### `generate-env-from-vault.sh`

**Purpose:** Generate `.env.local` for Docker Compose from Ansible vault

**Usage:**
```bash
bash scripts/vault/generate-env-from-vault.sh
# OR
make vault-generate-env
```

**What it does:**
1. Decrypts `provision/ansible/roles/secrets/vars/vault.yml`
2. Extracts secrets relevant for local Docker development
3. Generates `.env.local` with proper format
4. Adds header indicating auto-generation
5. Overwrites existing `.env.local`

**When to use:**
- Before starting Docker services
- After editing the vault
- After git pull (if vault changed)
- When .env.local is missing or stale

### `migrate-env-to-vault.sh`

**Purpose:** One-time migration from `.env.local` to Ansible vault

**Usage:**
```bash
bash scripts/vault/migrate-env-to-vault.sh
# OR
make vault-migrate
```

**What it does:**
1. Reads your current `.env.local` file
2. Decrypts current vault (or uses vault.example.yml as base)
3. Maps `.env.local` values to vault structure
4. Merges values intelligently
5. Creates timestamped backups
6. Encrypts and saves new vault

**When to use:**
- Initial setup (if you have existing `.env.local`)
- Only run once per environment

### `sync-vault.sh`

**Purpose:** Sync vault structure with `vault.example.yml`

**Usage:**
```bash
bash scripts/vault/sync-vault.sh
# OR
make vault-sync
```

**What it does:**
1. Decrypts current vault
2. Reads `vault.example.yml` structure
3. Maps secrets to new structure
4. Identifies removed/missing secrets
5. Creates timestamped backups
6. Encrypts and saves updated vault

**When to use:**
- After pulling updates that change vault structure
- When `vault.example.yml` is updated
- To reorganize vault structure

## Workflow

### Initial Setup

```bash
# If you have existing .env.local
make vault-migrate

# If starting fresh
cd provision/ansible
ansible-vault edit --vault-password-file ~/.vault_pass roles/secrets/vars/vault.yml
cd ../..
make vault-generate-env
```

### Daily Development

```bash
# Before starting Docker
make vault-generate-env
make docker-up

# To change secrets
cd provision/ansible
ansible-vault edit --vault-password-file ~/.vault_pass roles/secrets/vars/vault.yml
cd ../..
make vault-generate-env
make docker-restart
```

## Vault Password

Scripts look for vault password in:
1. `~/.vault_pass` (if exists, use automatically)
2. Interactive prompt (if no password file)

**Setup password file:**
```bash
echo "your-vault-password" > ~/.vault_pass
chmod 600 ~/.vault_pass
```

## Backups

All scripts create timestamped backups in:
```
provision/ansible/roles/secrets/vars/backups/
├── vault.backup.YYYYMMDD-HHMMSS.yml
├── vault.removed.YYYYMMDD-HHMMSS.yml
└── env.local.backup.YYYYMMDD-HHMMSS
```

See `provision/ansible/roles/secrets/vars/backups/README.md` for details.

## Dependencies

- **Python 3** with **PyYAML**: `pip3 install --user pyyaml`
- **ansible-vault**: Already required by Busibox

## Error Handling

All scripts include:
- ✅ Strict error handling (`set -euo pipefail`)
- ✅ Cleanup traps for temp files
- ✅ Backup before changes
- ✅ Rollback on failure
- ✅ User confirmation for destructive operations

## Documentation

- `docs/configuration/vault-as-source-of-truth.md` - Complete guide
- `docs/configuration/vault-sync.md` - Vault synchronization
- `docs/configuration/vault-backup-system.md` - Backup system
- `docs/development/vault-single-source-of-truth-implementation.md` - Implementation details

## Troubleshooting

### "Failed to decrypt vault"

```bash
# Check password
cat ~/.vault_pass

# Or use interactive
cd provision/ansible
ansible-vault edit --ask-vault-pass roles/secrets/vars/vault.yml
```

### "ModuleNotFoundError: No module named 'yaml'"

```bash
pip3 install --user --break-system-packages pyyaml
```

### ".env.local is stale"

```bash
# Regenerate from vault
make vault-generate-env
make docker-restart
```

### "Vault file not found"

```bash
# Create from example
cd provision/ansible
cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml
ansible-vault encrypt --vault-password-file ~/.vault_pass roles/secrets/vars/vault.yml
```

## Architecture

```
┌─────────────────────────────────┐
│    Ansible Vault (Encrypted)    │
│  Single Source of Truth         │
└────────────┬────────────────────┘
             │
     ┌───────┴────────┐
     │                │
     ▼                ▼
┌──────────┐    ┌──────────────┐
│.env.local│    │Ansible       │
│(Docker)  │    │Templates     │
│Generated │    │(Staging/Prod)│
└──────────┘    └──────────────┘
```

## Related Files

- `Makefile` - Vault management targets
- `.gitignore` - Excludes .env.local and vault.yml
- `docker-compose.local.yml` - Uses .env.local
- `provision/ansible/roles/secrets/vars/vault.example.yml` - Vault template
