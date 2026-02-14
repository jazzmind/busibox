# Vault Environment-Specific Fix

## Problem

When doing a fresh install after deleting `vault.staging.yml`, the installation script was:
1. Finding the old legacy `vault.yml` file (encrypted with old password)
2. Trying to decrypt it with the wrong password
3. Failing with: "Failed to decrypt vault"

**Error Log**:
```
[INFO] Using existing vault: /root/busibox/provision/ansible/roles/secrets/vars/vault.yml
[INFO] Syncing secrets and protected config to vault...
[ERROR] Failed to decrypt vault
[ERROR] Failed to sync values to vault
```

## Root Cause

The `set_vault_environment()` function in `vault.sh` has fallback logic:
1. Check for environment-specific vault (`vault.staging.yml`)
2. If not found, fall back to legacy vault (`vault.yml`)
3. If legacy vault exists and is encrypted with old password → decryption fails

This fallback made sense for **resuming** installations, but not for **fresh** installations where we want to create a new environment-specific vault.

## Solution

### 1. Force Environment-Specific Vault Creation (Fresh Install)

**File**: `scripts/make/install.sh` (lines 3607-3617)

Added logic to detect when environment-specific vault should be created instead of using legacy:

```bash
# For fresh install, force environment-specific vault (don't use legacy)
# VAULT_ENVIRONMENT is set by set_vault_environment() in vault.sh
if [[ -n "${VAULT_ENVIRONMENT:-}" ]]; then
    local env_vault_path="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.${VAULT_ENVIRONMENT}.yml"
    if [[ ! -f "$env_vault_path" ]] && [[ "$VAULT_FILE" != "$env_vault_path" ]]; then
        warn "Legacy vault detected, but environment-specific vault doesn't exist"
        info "Creating new environment-specific vault: vault.${VAULT_ENVIRONMENT}.yml"
        VAULT_FILE="$env_vault_path"
    fi
fi
```

**Logic**:
- If `vault.staging.yml` doesn't exist
- But `VAULT_FILE` is pointing to legacy `vault.yml`
- Override `VAULT_FILE` to point to `vault.staging.yml` instead
- Create new vault from example template

### 2. Generate Environment-Specific Vault Password

**File**: `scripts/make/install.sh` (lines 3647-3667)

Changed from trying to find existing password file to **generating** it:

```bash
# Use environment-specific vault password file
# VAULT_ENVIRONMENT is set by set_vault_environment() in vault.sh
if [[ -n "${VAULT_ENVIRONMENT:-}" ]]; then
    local vault_pass_file="${HOME}/.busibox-vault-pass-${VAULT_ENVIRONMENT}"
else
    # Fallback for legacy installations
    local vault_pass_file="${HOME}/.vault_pass"
fi

# Generate vault password if it doesn't exist
if [[ ! -f "$vault_pass_file" ]]; then
    if [[ -n "${VAULT_ENVIRONMENT:-}" ]]; then
        info "Generating vault password for $VAULT_ENVIRONMENT environment..."
    else
        info "Generating vault password..."
    fi
    openssl rand -base64 32 > "$vault_pass_file"
    chmod 600 "$vault_pass_file"
    success "Vault password generated: $vault_pass_file"
fi

export ANSIBLE_VAULT_PASSWORD_FILE="$vault_pass_file"
```

**Benefit**:
- Fresh password generated for fresh vault
- No attempt to reuse old password from legacy vault
- Each environment has its own password file

### 3. Force Environment-Specific Vault Usage (Resume Path)

**File**: `scripts/make/install.sh` (lines 3678-3693)

Added similar logic for the **resume** path to prevent using legacy vault:

```bash
# For resume, also force environment-specific vault (don't use legacy)
if [[ -n "${VAULT_ENVIRONMENT:-}" ]]; then
    local env_vault_path="...vault.${VAULT_ENVIRONMENT}.yml"
    if [[ -f "$env_vault_path" ]] && [[ "$VAULT_FILE" != "$env_vault_path" ]]; then
        warn "Legacy vault path detected, switching to environment vault"
        info "Using environment vault: vault.${VAULT_ENVIRONMENT}.yml"
        VAULT_FILE="$env_vault_path"
    fi
fi
```

**Why Needed**:
- `set_vault_environment()` falls back to legacy vault if environment vault doesn't exist
- But during resume, the environment vault DOES exist (created during initial install)
- Need to override the fallback and use the environment-specific vault
- Ensures correct vault is used with correct password

## Expected Behavior After Fix

### Fresh Install (No Environment Vault)

```bash
cd /root/busibox
rm -f provision/ansible/roles/secrets/vars/vault.staging.yml
rm -f ~/.busibox-vault-pass-staging
make install
```

**Output**:
```
[INFO] Creating vault from example...
[INFO]   Source: .../vault.example.yml
[INFO]   Target: .../vault.staging.yml
[SUCCESS] Vault file created: .../vault.staging.yml
[INFO] Generating vault password for staging environment...
[SUCCESS] Vault password generated: ~/.busibox-vault-pass-staging
[INFO] Syncing secrets and protected config to vault...
[SUCCESS] Synced 45+ values to vault
```

### Fresh Install (Legacy Vault Exists)

```bash
# Old vault.yml exists, but vault.staging.yml doesn't
make install
```

**Output**:
```
[WARN] Legacy vault detected, but environment-specific vault doesn't exist
[INFO] Creating new environment-specific vault: vault.staging.yml
[INFO] Creating vault from example...
[SUCCESS] Vault file created: .../vault.staging.yml
[INFO] Generating vault password for staging environment...
[SUCCESS] Vault password generated: ~/.busibox-vault-pass-staging
[INFO] Syncing secrets and protected config to vault...
[SUCCESS] Synced 45+ values to vault
```

## Vault File Structure

### Before Fix
```
provision/ansible/roles/secrets/vars/
├── vault.yml                    # Legacy (encrypted with old password)
├── vault.example.yml            # Template (unencrypted)
└── (no vault.staging.yml)

~/.vault_pass                    # Old password (wrong)
~/.busibox-vault-pass-staging    # Doesn't exist
```

**Result**: Install tries to use `vault.yml` with wrong password → fails

### After Fix
```
provision/ansible/roles/secrets/vars/
├── vault.yml                    # Legacy (ignored during fresh install)
├── vault.staging.yml            # NEW (created from example)
└── vault.example.yml            # Template

~/.vault_pass                    # Old password (ignored)
~/.busibox-vault-pass-staging    # NEW (generated fresh)
```

**Result**: Install creates fresh `vault.staging.yml` with fresh password → succeeds

## Benefits

1. **Clean Separation**: Each environment has its own vault and password
2. **No Legacy Issues**: Fresh installs don't get confused by old vaults
3. **Automatic Password Generation**: No manual password setup needed
4. **Secure**: Each environment has unique encryption password
5. **Resumable**: Existing vaults still work for resume operations

## Testing

### Test 1: Fresh Install with Legacy Vault

```bash
cd /root/busibox
# Keep vault.yml (old)
rm -f provision/ansible/roles/secrets/vars/vault.staging.yml
rm -f ~/.busibox-vault-pass-staging
make clean
make install
```

**Expected**:
- Detects legacy vault exists but environment vault doesn't
- Creates `vault.staging.yml` from example
- Generates fresh password in `~/.busibox-vault-pass-staging`
- Syncs secrets successfully

### Test 2: Fresh Install without Any Vault

```bash
cd /root/busibox
rm -f provision/ansible/roles/secrets/vars/vault*.yml
rm -f ~/.busibox-vault-pass-*
make clean
make install
```

**Expected**:
- Creates `vault.staging.yml` from `vault.example.yml`
- Generates password in `~/.busibox-vault-pass-staging`
- Syncs secrets successfully

### Test 3: Resume with Existing Environment Vault

```bash
# Interrupt with Ctrl+C
make install
```

**Expected**:
- Uses existing `vault.staging.yml`
- Uses existing `~/.busibox-vault-pass-staging`
- Decrypts and syncs successfully

## Related Fixes

This is fix **#5** in the series:
1. ✅ Embedding model caching for Proxmox
2. ✅ Unbound variable errors
3. ✅ Application secrets sync
4. ✅ Vault file creation
5. ✅ **Environment-specific vault handling** (this fix)

## Files Modified

```
scripts/make/install.sh
  - Lines 3607-3616: Force environment-specific vault creation
  - Lines 3644-3654: Generate environment-specific vault password
```

## Key Improvements

### Before
```bash
# Tries to use legacy vault.yml with wrong password
VAULT_FILE="/root/busibox/provision/ansible/roles/secrets/vars/vault.yml"
ANSIBLE_VAULT_PASSWORD_FILE="~/.vault_pass"  # Wrong password
# → Decryption fails
```

### After
```bash
# Creates fresh environment-specific vault
VAULT_FILE="/root/busibox/provision/ansible/roles/secrets/vars/vault.staging.yml"
ANSIBLE_VAULT_PASSWORD_FILE="~/.busibox-vault-pass-staging"  # Fresh password
# → Success
```

## Migration Path

### Moving from Legacy to Environment-Specific Vaults

If you have an existing `vault.yml` and want to migrate:

```bash
cd /root/busibox/provision/ansible/roles/secrets/vars

# Production
cp vault.yml vault.prod.yml
# or
mv vault.yml vault.prod.yml

# Staging
cp vault.prod.yml vault.staging.yml

# Decrypt and update staging-specific values
ansible-vault decrypt vault.staging.yml
# Edit staging-specific network octets, domains, etc.
ansible-vault encrypt vault.staging.yml

# Update password files
mv ~/.vault_pass ~/.busibox-vault-pass-prod
# Generate new staging password
openssl rand -base64 32 > ~/.busibox-vault-pass-staging
chmod 600 ~/.busibox-vault-pass-staging

# Re-encrypt staging vault with new password
ansible-vault rekey vault.staging.yml
```

## Conclusion

Fresh Proxmox installations now correctly:
1. Create environment-specific vaults instead of using legacy
2. Generate fresh passwords for each environment
3. Avoid decryption errors from mismatched passwords
4. Maintain clean separation between environments
