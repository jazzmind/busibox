# Recent Fixes Summary

## Issues Resolved

### 1. Subdomain Routing Issue
**Problem**: Wildcard DNS `*.ai.localhost` doesn't cover sub-subdomains like `agents.test.ai.localhost`

**Solution**: Changed subdomain pattern for test environment:
- ❌ Old: `agents.test.ai.localhost` (requires `*.*.ai.localhost`)
- ✅ New: `agents-test.ai.localhost` (covered by `*.ai.localhost`)

**Files Changed**:
- `provision/ansible/inventory/test/group_vars/all/00-main.yml`
- `provision/ansible/roles/nginx/tasks/configure-placeholders.yml`

### 2. SSL Certificate Not Being Used
**Problem**: Provisioned SSL certificates from vault were not being deployed correctly

**Solution**:
- Fixed `provisioned.yml` to use `full_domain` (test.ai.localhost) instead of just `domain` (ai.localhost)
- Added proper chain file handling (checks if chain exists and has content)
- Creates fullchain certificate only when chain is provided
- Better error messages and summary output

**Files Changed**:
- `provision/ansible/roles/nginx/tasks/provisioned.yml`
- `provision/ansible/roles/nginx/tasks/configure-placeholders.yml`

### 3. SSL Upload Script Overwrites Vault
**Problem**: `scripts/upload-ssl-cert.sh` was replacing the entire vault file, destroying all other secrets

**Solution**: Complete rewrite using Python + PyYAML:
- Loads existing vault content
- Merges SSL certificates into `secrets.ssl_certificates` section
- Preserves all other vault content
- Better validation and preview
- Handles chain file as optional

**Files Changed**:
- `scripts/upload-ssl-cert.sh` (complete rewrite)

## Testing Instructions

### 1. Add Local DNS Entries
Edit `/etc/hosts` on your local machine:

```bash
sudo nano /etc/hosts
```

Add these lines:
```
10.96.201.200 test.ai.localhost
10.96.201.200 agents-test.ai.localhost
10.96.201.200 docs-test.ai.localhost
10.96.201.200 innovation-test.ai.localhost
```

### 2. Upload SSL Certificate (if using provisioned mode)
```bash
cd /path/to/busibox
bash scripts/upload-ssl-cert.sh /path/to/cert.crt /path/to/cert.key /path/to/chain.crt
```

The script will:
- Validate certificate and key match
- Show certificate details
- Merge into existing vault (preserving other secrets)
- Re-encrypt vault if it was encrypted
- Provide next steps

### 3. Deploy Placeholder Mode
```bash
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --tags nginx --ask-vault-pass -e placeholder_mode=true
```

### 4. Test Routes in Browser

**Main Domain (AI Portal)**:
- https://test.ai.localhost

**Path-based routing**:
- https://test.ai.localhost/agents (Agent Client)
- https://test.ai.localhost/docs (Doc Intel)
- https://test.ai.localhost/innovation (Innovation)

**Subdomain routing**:
- https://agents-test.ai.localhost
- https://docs-test.ai.localhost
- https://innovation-test.ai.localhost

Accept the self-signed certificate warning (for test) or it should work directly (if using provisioned certs).

### 5. Test with curl
```bash
# Main domain
curl -k https://test.ai.localhost

# Path-based routing
curl -k https://test.ai.localhost/agents
curl -k https://test.ai.localhost/docs
curl -k https://test.ai.localhost/innovation

# Subdomain routing
curl -k https://agents-test.ai.localhost
curl -k https://docs-test.ai.localhost
curl -k https://innovation-test.ai.localhost
```

Each route should show a unique placeholder page with:
- Different color gradient
- App-specific icon and title
- Current URL displayed at the bottom (for debugging)

## Expected Results

1. **Each route shows correct placeholder**:
   - Main domain → AI Portal (purple gradient)
   - `/agents` or `agents-test` subdomain → Agent Client (pink gradient)
   - `/docs` or `docs-test` subdomain → Doc Intel (green gradient)
   - `/innovation` or `innovation-test` subdomain → Innovation (orange gradient)

2. **SSL works correctly**:
   - Self-signed: Browser shows warning but connection is encrypted
   - Provisioned: No warning, full green lock

3. **JavaScript displays current URL**:
   - At bottom of each page
   - Helps verify correct page is being served
   - Should match the URL in browser address bar

## Common Issues

### "All routes show the same page"
**Cause**: Accessing by IP address instead of domain name  
**Fix**: MUST access via domain names (add to /etc/hosts)

### "SSL certificate not found"
**Cause**: Vault secrets not loaded or wrong domain used  
**Fix**: Check that:
- Vault is decrypted during deployment (`--ask-vault-pass`)
- `ssl_mode: provisioned` in `group_vars/all/00-main.yml`
- Certificate uploaded with correct domain name
- `secrets.ssl_certificates` exists in vault

### "Connection refused"
**Cause**: NGINX not running or wrong IP  
**Fix**: 
- SSH to proxy container: `pct enter <container_id>`
- Check NGINX: `systemctl status nginx`
- Check listening ports: `ss -tlnp | grep 443`

### "DNS not resolving"
**Cause**: /etc/hosts not updated or typo in domain  
**Fix**: 
- Verify /etc/hosts entries
- Clear DNS cache: `sudo dscacheutil -flushcache` (macOS)
- Use curl with -v to see DNS resolution

## Next Steps

Once placeholder routing is working correctly:

1. **Disable placeholder mode**:
   ```bash
   # Edit inventory/test/group_vars/all/00-main.yml
   # Remove or comment out: placeholder_mode: true
   ```

2. **Deploy applications**:
   ```bash
   cd provision/ansible
   ansible-playbook -i inventory/test/hosts.yml site.yml --tags app_deployer --ask-vault-pass
   ```

3. **Switch NGINX to application routing**:
   ```bash
   ansible-playbook -i inventory/test/hosts.yml site.yml --tags nginx --ask-vault-pass
   ```

4. **Verify applications are running**:
   - Check health endpoints
   - Test login flows
   - Verify database connections

## Documentation

See also:
- `docs/SUBDOMAIN_PATTERN.md` - Detailed explanation of subdomain pattern
- `docs/APP_DEPLOYMENT.md` - Application deployment guide  
- `docs/DEBUG_DEPLOYMENT.md` - Debugging guide
- `provision/ansible/SETUP.md` - Initial setup instructions


