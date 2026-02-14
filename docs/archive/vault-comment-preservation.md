---
created: 2026-01-18
updated: 2026-01-18
status: completed
category: development
---

# Vault Sync: Comment Preservation & Configure Menu Updates

## Changes Made

### 1. Vault Sync Now Preserves Comments

**Problem:** The vault sync script was using `yaml.dump()` which strips all comments from the YAML file, losing valuable documentation.

**Solution:** Created a new Python script (`scripts/vault/preserve_comments.py`) that:
- Reads `vault.example.yml` line by line, preserving all comments and structure
- Parses YAML key-value pairs while tracking the path (e.g., `secrets.openai.api_key`)
- Substitutes values from the current vault where they exist
- Keeps all comments, blank lines, and formatting intact

**Result:** Synced vault now retains all helpful comments like:
```yaml
  # GitHub Access Token (for private repository deployments)
  # Create at: https://github.com/settings/tokens
  # Scope: repo (full control of private repositories)
  # Used by deploywatch scripts to access private repos
  github_token: "actual_token_here"
```

### 2. Removed Obsolete TOKEN_SERVICE Option

**Problem:** The configure menu had an option to "Generate TOKEN_SERVICE Keys (agent-server)" but:
- The script doesn't exist at that path (`scripts/generate/generate-token-service-keys.sh`)
- AuthZ now handles token generation/validation
- The agent service doesn't use `TOKEN_SERVICE_PRIVATE_KEY` or `TOKEN_SERVICE_PUBLIC_KEY`

**Changes Made:**
- Removed "Generate TOKEN_SERVICE Keys" from Secrets & Keys menu
- Added "Generate .env.local from Vault" option instead
- Updated menu option numbering from [1-5] to [1-5] (reused slot 4)
- Updated main menu description from "Secrets & Keys (TOKEN_SERVICE, vault)" to "Secrets & Keys (vault, secrets)"

### 3. Added Vault Generate to Configure Menus

**Enhancement:** Added "Generate .env.local from Vault" option to both:
- **Proxmox Secrets & Keys menu** (option 4)
- **Docker Configuration menu** (option 5)

This allows users to regenerate `.env.local` directly from the configure interface.

## Implementation Details

### preserve_comments.py Script

```python
# Key features:
1. Line-by-line processing (preserves comments)
2. Path tracking with indent-based stack
3. Value substitution while maintaining format
4. Handles quotes, templates ({{}}), booleans, numbers
```

**How it works:**
1. Parse each line of vault.example.yml
2. If it's a value line (has a colon), determine the full path
3. Look up that path in the current (flattened) vault
4. Substitute the value if it exists, otherwise keep placeholder
5. Preserve original formatting (quotes, spacing, etc.)

### Updated sync-vault.sh

**Before:**
```bash
python3 -c "yaml.dump(...)  # Strips comments
```

**After:**
```bash
# Use comment-preserving script
python3 preserve_comments.py current.yml example.yml > new.yml

# Then analyze changes (removed/missing secrets)
python3 -c "... analyze ..."
```

### Updated configure.sh

**Proxmox Secrets & Keys Menu:**
```bash
menu "Secrets & Keys" \
    "Edit Ansible Vault (secrets)" \              # Was option 2, now option 1
    "View Vault Variables (masked)" \             # Was option 3, now option 2
    "Sync Vault with Example (update structure)" \ # Was option 4, now option 3
    "Generate .env.local from Vault" \            # NEW - was "TOKEN_SERVICE"
    "Back"
```

**Docker Configuration Menu:**
```bash
menu "Docker Configuration" \
    "App Configuration (admin, OAuth clients)" \
    "Edit Ansible Vault (secrets)" \
    "View Vault Variables (masked)" \
    "Sync Vault with Example (update structure)" \
    "Generate .env.local from Vault" \            # NEW
    "Back to Main Menu"
```

## Testing

### Test Comment Preservation

```bash
# Decrypt current vault
cd provision/ansible
ansible-vault view --vault-password-file ~/.vault_pass roles/secrets/vars/vault.yml > /tmp/current.yml

# Run preserve script
cd ../..
python3 scripts/vault/preserve_comments.py /tmp/current.yml provision/ansible/roles/secrets/vars/vault.example.yml | head -80

# ✓ All comments preserved
# ✓ Structure maintained
# ✓ Values substituted where they exist
```

### Test Sync Script

```bash
# Run full sync
make vault-sync

# ✓ Comments preserved in synced vault
# ✓ Values correctly substituted
# ✓ Missing secrets reported
# ✓ Removed secrets saved to backup
```

### Test Configure Menu

```bash
make configure

# Navigate to Secrets & Keys
# ✓ Option 1: Edit Ansible Vault
# ✓ Option 2: View Vault Variables
# ✓ Option 3: Sync Vault with Example
# ✓ Option 4: Generate .env.local from Vault (NEW)
# ✓ No TOKEN_SERVICE option
```

## Benefits

### Comment Preservation
✅ **Better Documentation** - All helpful comments maintained
✅ **Easier Onboarding** - New team members see explanations
✅ **Clear Instructions** - "Create at:", "Generate with:", etc.
✅ **No Information Loss** - Sync doesn't destroy context

### Menu Cleanup
✅ **Removed Broken Option** - TOKEN_SERVICE script doesn't exist
✅ **Added Useful Option** - Generate .env.local frequently needed
✅ **Consistent Interface** - Same options in both menus
✅ **Modern Workflow** - Reflects AuthZ-based architecture

## Files Modified

1. **`scripts/vault/preserve_comments.py`** (NEW)
   - Line-by-line YAML processing
   - Comment preservation
   - Value substitution

2. **`scripts/vault/sync-vault.sh`**
   - Uses `preserve_comments.py` for main sync
   - Separate analysis step for removed/missing
   - Better success message

3. **`scripts/make/configure.sh`**
   - Removed TOKEN_SERVICE option
   - Added Generate .env.local option
   - Updated menu descriptions
   - Fixed option numbering

## Future Enhancements

Potential improvements for `vault.example.yml` comments:

1. **Mark Required vs Optional**
   ```yaml
   # REQUIRED: Admin email for initial setup
   admin_email: "CHANGE_ME_ADMIN_EMAIL"
   
   # OPTIONAL: Resend API key for email notifications
   resend_api_key: "CHANGE_ME_RESEND_API_KEY"
   ```

2. **Add Validation Hints**
   ```yaml
   # Must be 32 bytes (use: openssl rand -base64 32)
   jwt_secret: "CHANGE_ME_JWT_SECRET_32_BYTES"
   ```

3. **Security Warnings**
   ```yaml
   # ⚠️  CRITICAL: Back this up securely! Loss means data loss.
   authz_master_key: "CHANGE_ME_AUTHZ_MASTER_KEY"
   ```

4. **Example Values**
   ```yaml
   # Example: wes@example.com
   admin_email: "CHANGE_ME_ADMIN_EMAIL"
   ```

## Related Documentation

- `docs/configuration/vault-as-source-of-truth.md` - Vault architecture
- `docs/configuration/vault-sync.md` - Sync process
- `docs/development/vault-sync-improvements.md` - Previous fixes

## Summary

✅ Vault sync now preserves all comments and structure
✅ Removed obsolete TOKEN_SERVICE generation option
✅ Added Generate .env.local to configure menus
✅ Clean, consistent configuration interface
✅ AuthZ-based token architecture properly reflected

The vault sync process is now much more user-friendly, maintaining all the helpful documentation that guides users through configuration!
