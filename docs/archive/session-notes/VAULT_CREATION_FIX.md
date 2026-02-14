# Vault Creation Fix for Fresh Installs

## Problem

When doing a fresh Proxmox/staging install after deleting `vault.staging.yml`, the vault file was not being created, leading to errors during secret sync.

## Root Cause

The vault creation logic in `install.sh` (lines 3607-3625) had several issues:

1. **No fallback for VAULT_EXAMPLE**: If `VAULT_EXAMPLE` wasn't properly set by `set_vault_environment()`, the check would fail
2. **No directory creation**: Didn't ensure the target directory existed before copying
3. **Poor error messages**: When something went wrong, no debugging information was provided
4. **Silent failures**: The copy operation could fail without clear indication

## Solution

### Enhanced Vault Creation Logic
**File**: `scripts/make/install.sh` (lines 3607-3633)

**Improvements**:

1. **Fallback for VAULT_EXAMPLE**: If not set, uses sensible default:
   ```bash
   VAULT_EXAMPLE="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.example.yml"
   ```

2. **Directory Creation**: Ensures target directory exists:
   ```bash
   mkdir -p "$(dirname "$VAULT_FILE")"
   ```

3. **Detailed Logging**: Shows what's happening:
   ```
   [INFO] Creating vault from example...
   [INFO]   Source: .../vault.example.yml
   [INFO]   Target: .../vault.staging.yml
   [SUCCESS] Vault file created: .../vault.staging.yml
   ```

4. **Better Error Messages**: If something fails, shows diagnostic info:
   ```
   [ERROR] Vault file not found and no example to copy from
   [ERROR]   Expected: .../vault.example.yml
   [ERROR]   Looking in: .../vars
   ```

## Expected Behavior After Fix

### Fresh Install (No Vault Exists)

```bash
cd /root/busibox
rm -f provision/ansible/roles/secrets/vars/vault.staging.yml  # Delete vault
make install
```

You should see:
```
[INFO] Creating vault from example...
[INFO]   Source: .../vault.example.yml
[INFO]   Target: .../vault.staging.yml
[SUCCESS] Vault file created: .../vault.staging.yml
[INFO] Setting vault password for sync operation
[INFO] Syncing secrets and protected config to vault...
[SUCCESS] Synced X values to vault
```

### Resume Install (Vault Exists)

```bash
make install  # Continue partial install
```

You should see:
```
[INFO] Using existing vault: .../vault.staging.yml
[INFO] Restoring secrets and config from vault...
```

## Vault File Locations

**Environment-Specific Vaults**:
- Production: `provision/ansible/roles/secrets/vars/vault.yml` (or `vault.prod.yml`)
- Staging: `provision/ansible/roles/secrets/vars/vault.staging.yml`
- Development: `provision/ansible/roles/secrets/vars/vault.dev.yml`

**Vault Passwords**:
- Production: `~/.busibox-vault-pass-prod` (or `~/.vault_pass`)
- Staging: `~/.busibox-vault-pass-staging`
- Development: `~/.busibox-vault-pass-dev`

## Vault Creation Process

1. **Environment Detection**: `set_vault_environment()` determines prefix (dev/staging/prod)
2. **Vault Path Setup**: Sets `VAULT_FILE` to environment-specific path
3. **Check Existence**: If vault doesn't exist, proceed to create
4. **Fallback Check**: Ensure `VAULT_EXAMPLE` is set with sensible default
5. **Directory Creation**: Create target directory if needed
6. **Copy Template**: Copy `vault.example.yml` to target path
7. **Generate Secrets**: Generate random passwords and keys
8. **Sync to Vault**: Update vault with generated secrets using `sync_secrets_to_vault()`
9. **Encrypt Vault**: Vault is encrypted with password from `~/.busibox-vault-pass-{env}`

## Related Files

```
scripts/make/install.sh           - Vault creation logic (improved)
scripts/lib/vault.sh              - Vault environment setup
provision/ansible/roles/secrets/vars/
  ├── vault.example.yml           - Template vault (never encrypted)
  ├── vault.yml                   - Legacy single vault
  ├── vault.staging.yml           - Staging environment vault
  └── vault.prod.yml              - Production environment vault
```

## Testing

1. **Test Fresh Install**:
   ```bash
   cd /root/busibox
   rm -f provision/ansible/roles/secrets/vars/vault.staging.yml
   rm -f ~/.busibox-vault-pass-staging
   make clean
   make install
   ```

2. **Test Resume**:
   ```bash
   # Interrupt install with Ctrl+C
   make install  # Should resume and use existing vault
   ```

3. **Test Error Handling**:
   ```bash
   # Temporarily rename vault.example.yml to test error path
   cd provision/ansible/roles/secrets/vars
   mv vault.example.yml vault.example.yml.bak
   cd /root/busibox
   make install  # Should show clear error message
   ```

## Related Fixes

This vault creation fix complements:
1. **Unbound Variable Fix**: Ensures `ANSIBLE_VAULT_PASSWORD_FILE` is always initialized
2. **Application Secrets Sync**: Ensures app secrets are synced to vault
3. **Embedding Model Caching**: Background model downloads work correctly

All four fixes together ensure a smooth Proxmox installation experience.
