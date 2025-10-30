# Configuration Migration Checklist

**Date**: 2025-10-23  
**Purpose**: Steps to complete the configuration refactoring

## Overview

The Ansible configuration has been refactored to use:
- ✅ Calculated IP addresses (not hardcoded)
- ✅ Variable-based domains (not hardcoded)
- ✅ Secrets-only vault file (no env-specific config)
- ✅ All 5 Node apps defined (ai-portal, agent-client, doc-intel, innovation, agent-server)

## Pre-Migration Tasks

### 1. Backup Current Configuration
```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Backup vault file if it exists
[ -f roles/secrets/vars/vault.yml ] && \
  cp roles/secrets/vars/vault.yml roles/secrets/vars/vault.yml.backup

# Commit current state
git add .
git commit -m "Backup before configuration refactoring"
```

### 2. Review New Structure
- [ ] Read `CONFIGURATION_GUIDE.md`
- [ ] Review `vault.example.yml` for new secret structure
- [ ] Review `inventory/production/group_vars/all.yml`
- [ ] Review `inventory/test/group_vars/all.yml`

## Migration Steps

### 3. Update Vault File

**Option A: Create New Vault File** (Recommended)
```bash
# Copy example
cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml

# Edit and add real secrets
ansible-vault edit roles/secrets/vars/vault.yml

# Add secrets for all applications:
# - postgresql.password
# - agent-server (database_url, minio_access_key, minio_secret_key, redis_url, jwt_secret)
# - ai-portal (database_url, better_auth_secret, resend_api_key, sso_jwt_secret, litellm_api_key)
# - agent-client (database_url, agent_api_key, jwt_secret, session_secret)
# - doc-intel (database_url, openai_api_key, better_auth_secret, jwt_secret)
# - innovation (database_url, better_auth_secret, jwt_secret, openai_api_key)
# - litellm (master_key, database_url)
# - letsencrypt (email, optional: cloudflare_api_token)
```

**Option B: Migrate Existing Vault File**
```bash
# Decrypt existing vault
ansible-vault decrypt roles/secrets/vars/vault.yml

# Manually migrate secrets to new structure (see vault.example.yml)
# Remove any environment-specific values (IPs, domains, etc.)

# Re-encrypt
ansible-vault encrypt roles/secrets/vars/vault.yml
```

### 4. Verify Configuration

```bash
# Test variable resolution (production)
ansible-inventory -i inventory/production --list | grep -A 5 "proxy_ip"

# Test variable resolution (test)
ansible-inventory -i inventory/test --list | grep -A 5 "proxy_ip"

# Check syntax
ansible-playbook --syntax-check -i inventory/production site.yml
ansible-playbook --syntax-check -i inventory/test site.yml
```

### 5. Verify Application Definitions

Check that all 5 applications are properly configured:

```bash
# Production
ansible-inventory -i inventory/production --list | grep -A 30 "applications"

# Test
ansible-inventory -i inventory/test --list | grep -A 30 "applications"
```

Expected applications:
- [ ] agent-server (port 4111, internal only)
- [ ] ai-portal (port 3000, domain routing)
- [ ] agent-client (port 3001, subdomain + path routing)
- [ ] doc-intel (port 3002, subdomain + path routing)
- [ ] innovation (port 3003, subdomain + path routing)

### 6. Test Environment Deployment

```bash
# Deploy to test environment first
ansible-playbook -i inventory/test site.yml --ask-vault-pass

# Verify services are running
ansible -i inventory/test apps -a "pm2 list"
ansible -i inventory/test proxy -a "nginx -t"
```

### 7. Verify Application Access (Test)

- [ ] https://test.ai.jaycashman.com (ai-portal)
- [ ] https://agents.test.ai.jaycashman.com (agent-client)
- [ ] https://docs.test.ai.jaycashman.com (doc-intel)
- [ ] https://innovation.test.ai.jaycashman.com (innovation)
- [ ] Check agent-server health: `curl http://10.96.201.202:4111/auth/health`

### 8. Production Deployment

**ONLY after successful test deployment:**

```bash
# Review production configuration one more time
cat inventory/production/group_vars/all.yml

# Deploy to production
ansible-playbook -i inventory/production site.yml --ask-vault-pass

# Verify services
ansible -i inventory/production apps -a "pm2 list"
ansible -i inventory/production proxy -a "nginx -t"
```

### 9. Verify Application Access (Production)

- [ ] https://ai.jaycashman.com (ai-portal)
- [ ] https://www.ai.jaycashman.com (ai-portal)
- [ ] https://agents.ai.jaycashman.com (agent-client)
- [ ] https://ai.jaycashman.com/agents (agent-client)
- [ ] https://docs.ai.jaycashman.com (doc-intel)
- [ ] https://ai.jaycashman.com/docs (doc-intel)
- [ ] https://innovation.ai.jaycashman.com (innovation)
- [ ] https://ai.jaycashman.com/innovation (innovation)
- [ ] Check agent-server health: `curl http://10.96.200.202:4111/auth/health`

## Post-Migration Tasks

### 10. Update Documentation

- [ ] Update deployment runbooks with new configuration
- [ ] Document any custom vault passwords
- [ ] Update team wiki/docs with new structure

### 11. Cleanup

```bash
# Remove backup files if everything works
rm -f roles/secrets/vars/vault.yml.backup

# Commit final state
git add .
git commit -m "Complete configuration refactoring with calculated IPs and all apps"
git push origin 002-deploy-app-servers
```

### 12. Team Communication

- [ ] Notify team of new configuration structure
- [ ] Share `CONFIGURATION_GUIDE.md`
- [ ] Update any CI/CD pipelines to use new vault structure

## Rollback Plan

If something goes wrong:

```bash
# Restore backup vault
cp roles/secrets/vars/vault.yml.backup roles/secrets/vars/vault.yml

# Revert Git changes
git reset --hard <commit-before-changes>

# Redeploy previous configuration
ansible-playbook -i inventory/production site.yml --ask-vault-pass
```

## Common Issues & Solutions

### Issue: Ansible can't resolve variables
**Solution**: Check that `network_base_octets` is set in `all.yml` and variables use proper Jinja2 syntax `{{ var }}`

### Issue: Secrets not injecting into applications
**Solution**: 
1. Verify secret names match in `vault.yml` and `applications[].secrets[]`
2. Check vault file is encrypted: `ansible-vault view roles/secrets/vars/vault.yml`
3. Verify application names use hyphens, not underscores

### Issue: Wrong IP addresses used
**Solution**: Verify `hosts.yml` uses `{{ proxy_ip }}` format, not hardcoded IPs

### Issue: Applications not building
**Solution**: Check `build_command` is set for Next.js apps, verify Node version

### Issue: NGINX routing not working
**Solution**: Check `routes` configuration, verify domains resolve to proxy IP

## Verification Commands

```bash
# Show all variables for production
ansible-inventory -i inventory/production --list --yaml

# Show all variables for test
ansible-inventory -i inventory/test --list --yaml

# Test vault decryption
ansible-vault view roles/secrets/vars/vault.yml

# Ping all hosts
ansible -i inventory/production all -m ping
ansible -i inventory/test all -m ping

# Check service status
ansible -i inventory/production apps -a "pm2 status"
ansible -i inventory/production proxy -a "systemctl status nginx"
```

## Success Criteria

Migration is complete when:

- [x] All configuration files updated
- [ ] Vault file updated with all secrets
- [ ] Test environment deployed successfully
- [ ] All test apps accessible
- [ ] Production environment deployed successfully
- [ ] All production apps accessible
- [ ] No hardcoded IPs in configuration
- [ ] No hardcoded domains in configuration
- [ ] All 5 Node apps running
- [ ] Documentation updated
- [ ] Team notified

## Notes

- The configuration now supports easy scaling (just change `network_base_octets`)
- All apps share JWT secrets for cross-app authentication
- Test environment mirrors production structure
- SSL mode differs: production uses provisioned certs, test uses self-signed

