# Busibox Ansible Setup Guide

## Quick Setup (New Deployment)

### 1. Clone Repository
```bash
git clone <repo-url> busibox-deployment
cd busibox-deployment/provision/ansible
```

### 2. Create Vault File
```bash
# Copy template
cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml

# Edit with your deployment-specific values
ansible-vault edit roles/secrets/vars/vault.yml

# Setup vault symlinks for inventory loading
./setup-vault-links.sh
```

**Required values in vault.yml**:
```yaml
# Network Configuration
network_base_octets_production: "10.96.200"  # Your production network
network_base_octets_staging: "10.96.201"        # Your test network

# Domain Configuration
base_domain: "yourdomain.com"                # Your domain
ssl_email: "admin@yourdomain.com"            # SSL email

# Secrets
secrets:
  postgresql:
    password: "YOUR_POSTGRES_PASSWORD"
  # ... (see vault.example.yml for complete structure)
```

### 3. Verify Setup
```bash
# Check that vault variables load
ansible-inventory -i inventory/test --list | grep network_base_octets

# Should show:
# "network_base_octets": "10.96.201",
# "network_base_octets_staging": "10.96.201",
```

### 4. Deploy
```bash
# Test environment
ansible-playbook -i inventory/test site.yml --ask-vault-pass

# Production environment
ansible-playbook -i inventory/production site.yml --ask-vault-pass
```

## File Structure

```
provision/ansible/
├── roles/secrets/vars/
│   ├── vault.example.yml          # Template (version controlled)
│   └── vault.yml                  # YOUR deployment config (gitignored)
│
├── inventory/
│   ├── production/
│   │   ├── hosts.yml              # Host definitions
│   │   └── group_vars/
│   │       ├── all/
│   │       │   ├── 00-main.yml    # Generic config
│   │       │   └── vault.yml      # → symlink to vault
│   │       ├── apps.yml           # App-specific overrides
│   │       └── proxy.yml          # Proxy-specific overrides
│   └── test/
│       └── (same structure)
│
└── Documentation...
```

## How Vault Loading Works

1. **Vault file**: `roles/secrets/vars/vault.yml` (deployment-specific)
2. **Symlinks**: `inventory/*/group_vars/all/vault.yml` → point to vault file
3. **Ansible loads**: All YAML files in `group_vars/all/` directory
4. **Result**: Vault variables available at inventory parse time

## Symlink Verification

The symlinks should already exist, but verify:

```bash
cd provision/ansible

# Check production symlink
ls -la inventory/production/group_vars/all/vault.yml
# Should show: vault.yml -> ../../../../roles/secrets/vars/vault.yml

# Check test symlink
ls -la inventory/test/group_vars/all/vault.yml
# Should show: vault.yml -> ../../../../roles/secrets/vars/vault.yml
```

If symlinks are missing, recreate them:

```bash
cd provision/ansible

# Production
cd inventory/production/group_vars/all
ln -sf ../../../../roles/secrets/vars/vault.yml vault.yml

# Test
cd ../../test/group_vars/all
ln -sf ../../../../roles/secrets/vars/vault.yml vault.yml
```

## Troubleshooting

### Error: "network_base_octets_staging is undefined"

**Cause**: Vault variables not loading at inventory time

**Solution**:
1. Verify vault file exists: `ls -la roles/secrets/vars/vault.yml`
2. Verify symlinks exist: `ls -la inventory/*/group_vars/all/vault.yml`
3. Verify vault contains required variables:
   ```bash
   ansible-vault view roles/secrets/vars/vault.yml | grep network_base_octets
   ```

### Error: "vault.yml has merge conflicts"

**Cause**: Vault was previously in git, now it's gitignored

**Solution**: See `VAULT_MIGRATION.md`

### Error: "Could not find vault password"

**Cause**: Vault is encrypted but no password provided

**Solution**: Use `--ask-vault-pass` or configure vault password file

## Initial Vault Password

When you first create the vault:

```bash
# Create unencrypted
cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml

# Edit with your values
vim roles/secrets/vars/vault.yml  # or nano, or your editor

# Encrypt it
ansible-vault encrypt roles/secrets/vars/vault.yml
# (you'll be asked to create a password)

# Future edits
ansible-vault edit roles/secrets/vars/vault.yml
# (you'll need the password)
```

## Vault Password Management

### Option 1: Ask Each Time
```bash
ansible-playbook -i inventory/test site.yml --ask-vault-pass
```

### Option 2: Password File
```bash
# Create password file (NEVER commit this!)
echo "your-vault-password" > ~/.vault_pass
chmod 600 ~/.vault_pass

# Use in playbook
ansible-playbook -i inventory/test site.yml --vault-password-file ~/.vault_pass
```

### Option 3: Environment Variable
```bash
export ANSIBLE_VAULT_PASSWORD_FILE=~/.vault_pass
ansible-playbook -i inventory/test site.yml
```

## Multiple Deployments

For multiple customer deployments, use separate directories:

```bash
# Customer 1
git clone <repo> ~/deployments/customer1
cd ~/deployments/customer1/provision/ansible
cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml
# Edit with customer1 values
ansible-vault encrypt roles/secrets/vars/vault.yml

# Customer 2
git clone <repo> ~/deployments/customer2
cd ~/deployments/customer2/provision/ansible
cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml
# Edit with customer2 values
ansible-vault encrypt roles/secrets/vars/vault.yml
```

See `DEPLOYMENT_SPECIFIC.md` for details.

## Updating Generic Code

When the generic infrastructure code is updated:

```bash
cd ~/deployments/customer1

# Stash your vault if there are conflicts (shouldn't happen with gitignore)
# git stash push provision/ansible/roles/secrets/vars/vault.yml

# Pull latest code
git pull origin main

# Your vault.yml is preserved (gitignored)
# Redeploy
cd provision/ansible
ansible-playbook -i inventory/production site.yml --ask-vault-pass
```

## Checklist

- [ ] Repository cloned
- [ ] `vault.yml` created from template
- [ ] Network base octets configured
- [ ] Domain configured
- [ ] All secrets filled in
- [ ] Vault encrypted
- [ ] Symlinks verified
- [ ] Inventory variables load correctly
- [ ] Test deployment successful
- [ ] Production deployment successful

## Next Steps

1. **Review**: `CONFIGURATION_GUIDE.md` for complete details
2. **Migrate**: If you have an existing vault, see `VAULT_MIGRATION.md`
3. **Deploy**: Follow the deployment guide in `DEPLOYMENT_SUMMARY.md`

