# Vault File Migration Guide

## Problem

The `vault.yml` file was previously tracked in git but is now gitignored (deployment-specific). This causes merge conflicts when pulling updates.

## Solution

### Option 1: Fresh Start (Recommended)

1. **Save your current vault**:
   ```bash
   cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible
   
   # If vault is encrypted
   ansible-vault decrypt roles/secrets/vars/vault.yml
   
   # Copy to safe location
   cp roles/secrets/vars/vault.yml ~/vault-backup.yml
   ```

2. **Remove from git tracking**:
   ```bash
   cd /Users/wessonnenreich/Code/sonnenreich/busibox
   
   # Remove from git index (but keep local file)
   git rm --cached provision/ansible/roles/secrets/vars/vault.yml
   
   # Pull latest changes
   git pull origin 002-deploy-app-servers
   ```

3. **Restore your deployment-specific vault**:
   ```bash
   cd provision/ansible
   
   # Copy your backup back
   cp ~/vault-backup.yml roles/secrets/vars/vault.yml
   
   # Re-encrypt
   ansible-vault encrypt roles/secrets/vars/vault.yml
   ```

### Option 2: Use Git Attributes

Add to `.git/info/attributes`:
```bash
provision/ansible/roles/secrets/vars/vault.yml merge=ours
```

Then configure the merge strategy:
```bash
git config merge.ours.driver true
```

This tells git to always keep YOUR version of vault.yml during merges.

### Option 3: Selective Pull

```bash
# Pull everything except vault.yml
git fetch origin 002-deploy-app-servers
git checkout origin/002-deploy-app-servers -- . ':!provision/ansible/roles/secrets/vars/vault.yml'
```

## Prevention (For Future)

The vault.yml is now gitignored, so this won't happen again. The file structure is now:

```
provision/ansible/
├── roles/secrets/vars/
│   ├── vault.example.yml     ✅ In git (template)
│   └── vault.yml             ❌ NOT in git (deployment-specific)
├── inventory/production/group_vars/all/
│   ├── 00-main.yml           ✅ In git (generic config)
│   └── vault.yml             → symlink to ../../roles/secrets/vars/vault.yml
└── inventory/test/group_vars/all/
    ├── 00-main.yml           ✅ In git (generic config)
    └── vault.yml             → symlink to ../../roles/secrets/vars/vault.yml
```

## Verification

After migration, verify vault is properly ignored:

```bash
# Should show vault.yml as ignored
git status

# Should NOT show vault.yml
git ls-files | grep vault.yml
# (symlinks are tracked, but the actual vault.yml is not)

# Vault variables should load
ansible-inventory -i inventory/test --list | grep network_base_octets
```

## Symlink Setup

The new structure requires symlinks so vault variables load at inventory time:

```bash
cd provision/ansible

# Production symlink (already created)
cd inventory/production/group_vars/all
ln -sf ../../../../roles/secrets/vars/vault.yml vault.yml

# Test symlink (already created)
cd ../../test/group_vars/all  
ln -sf ../../../../roles/secrets/vars/vault.yml vault.yml
```

## Troubleshooting

### Error: "vault.yml has merge conflicts"
Solution: Use Option 1 above (recommended)

### Error: "network_base_octets_test is undefined"
Solution: Vault isn't loading. Check:
1. Symlinks exist in `inventory/*/group_vars/all/vault.yml`
2. Actual vault exists at `roles/secrets/vars/vault.yml`
3. Vault contains required variables

### Error: "Cannot pull because of local changes"
Solution:
```bash
# Stash vault
git stash push provision/ansible/roles/secrets/vars/vault.yml

# Pull
git pull

# Restore vault (it's now gitignored, so no conflict)
git stash pop
```

## Current Status

As of the latest commit:
- ✅ vault.yml is gitignored
- ✅ vault.example.yml is the template (in git)
- ✅ Symlinks created for inventory variable loading
- ✅ .gitignore updated to exclude deployment-specific files
- ❌ Your existing vault.yml may still be tracked (migration needed)

