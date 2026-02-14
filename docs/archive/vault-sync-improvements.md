---
created: 2026-01-18
updated: 2026-01-18
status: completed
category: development
---

# Vault Sync Script Improvements

## Issues Fixed

### 1. False "Removed Secrets" Warnings

**Problem:**
The `vault-sync` script was reporting legitimate nested dictionary keys (like `authz`, `minio`, `agent-server`) as "removed" even though they existed in both the current vault and `vault.example.yml`.

**Root Cause:**
The Python flattening logic was treating intermediate dictionary keys as separate entities. When comparing flattened structures, it would find keys like `secrets.authz.admin_token` in both files, but the intermediate key `secrets.authz` (which is a dict, not a leaf value) wasn't being properly recognized as "mapped" because child keys existed.

**Fix:**
Updated the comparison logic to check if a key is a prefix of any mapped key:

```python
# Find secrets that don't map to new structure
# Only include keys that are NOT prefixes of any mapped key
# (i.e., it's an intermediate node, not a leaf)
for key in current_flat.keys():
    if key not in mapped_keys:
        # Check if this key is a prefix of any mapped key
        is_prefix = any(mapped.startswith(key + '.') for mapped in mapped_keys)
        if not is_prefix:
            removed_secrets[key] = current_flat[key]
```

This ensures that intermediate dict keys are not reported as "removed" if their child keys are properly mapped.

### 2. Unclear Reporting of Removed Secrets

**Problem:**
When secrets were actually removed (like `token_service_private_key`), the reporting showed the parent key (`agent-server`) instead of the actual removed leaf keys, making it confusing to understand what was removed.

**Fix:**
Added a separate file (`removed_keys.txt`) that lists the full flattened paths of removed secrets:

```python
# Also save the list of removed keys for better reporting
with open('$TEMP_DIR/removed_keys.txt', 'w') as f:
    for key in sorted(removed_secrets.keys()):
        f.write(f'{key}\n')
```

Updated the bash reporting to use this file:

```bash
# Show the flattened keys that were actually removed
if [[ -f "$TEMP_DIR/removed_keys.txt" ]]; then
    cat "$TEMP_DIR/removed_keys.txt" | sed 's/^/    - /'
fi
```

**Example output:**
```
Removed secrets:
    - secrets.agent-server.token_service_private_key
    - secrets.agent-server.token_service_public_key
```

Instead of the confusing:
```
Removed secrets:
    - agent-server
```

### 3. Obsolete Token Service Keys

**Problem:**
The vault contained `token_service_private_key` and `token_service_public_key` under `secrets.agent-server`, but these keys are no longer used by the agent API service.

**Investigation:**
- Checked `srv/agent/app/services/token_service.py` - it's about OAuth token exchange, not cryptographic keys
- Searched codebase for `TOKEN_SERVICE_PRIVATE_KEY` and `TOKEN_SERVICE_PUBLIC_KEY` - no matches
- Confirmed the script `provision/ansible/scripts/generate-token-service-keys.sh` is obsolete

**Actions Taken:**
1. Removed token service keys from `vault.example.yml`
2. Ran `vault-sync` to remove them from active vault (saved to `vault.removed.*.yml` backup)
3. Deleted obsolete script: `provision/ansible/scripts/generate-token-service-keys.sh`
   - A copy remains in `scripts/deprecated/` for reference

### 4. Removed doc-intel Section

**User Changes:**
The user removed the `doc-intel` section from `vault.example.yml` as doc-intel is now managed separately as an add-on app.

**Result:**
The sync script correctly detected this removal and handled it appropriately. No issues.

## Test Results

### Before Fix

```
[WARNING] 4 secret(s) don't map to new structure

Removed secrets:
    -   authz
    -   minio
    -   agent-server
    -   test_credentials
```

This was **incorrect** - `authz`, `minio`, and `agent-server` are all still in `vault.example.yml`.

### After Fix

```
No removed secrets warnings for legitimate keys.

Vault sync completed successfully with no false positives.
```

When we actually had removed keys (token_service), they were properly reported:

```
Removed secrets:
    - secrets.agent-server.token_service_private_key
    - secrets.agent-server.token_service_public_key
```

And saved to encrypted backup: `vault.removed.20260118-195024.yml`

## Files Modified

1. **`scripts/vault/sync-vault.sh`**
   - Fixed comparison logic to handle intermediate dict keys
   - Added `removed_keys.txt` generation for better reporting
   - Improved output formatting

2. **`provision/ansible/roles/secrets/vars/vault.example.yml`**
   - Removed obsolete `token_service_private_key` and `token_service_public_key`
   - Removed `doc-intel` section (user change)

3. **`provision/ansible/roles/secrets/vars/vault.yml`**
   - Synced with example via `vault-sync`
   - Token service keys removed, saved to backup

4. **Deleted:**
   - `provision/ansible/scripts/generate-token-service-keys.sh` (obsolete)

## Verification

```bash
# Run sync
make vault-sync

# Check results - no false warnings
# Vault synced successfully

# Regenerate .env.local
make vault-generate-env

# .env.local generated correctly without token service references
```

## Documentation Updates

No documentation changes needed - the scripts already had comprehensive documentation, and the fixes maintain the existing behavior (just more accurately).

## Summary

✅ Fixed false "removed secrets" warnings for nested dict keys
✅ Improved reporting to show actual removed leaf keys
✅ Removed obsolete token service keys from vault
✅ Deleted obsolete `generate-token-service-keys.sh` script
✅ Vault sync now works correctly with nested structures
✅ `.env.local` generation works correctly with updated vault

The vault sync script now accurately distinguishes between:
- **Intermediate keys** (dictionaries): Not reported as removed if children are mapped
- **Removed leaf keys**: Properly reported with full paths
- **Missing/placeholder keys**: Correctly identified for user action
