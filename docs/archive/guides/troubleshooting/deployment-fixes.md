# Deployment Fixes Applied

## Issues Fixed

### 1. ✅ Group Creation Error (nextjs_app role)
**Error**: `Group appuser does not exist`

**Fix**: Added group creation task before user creation in `roles/nextjs_app/tasks/install.yml`

```yaml
- name: Create app group
  group:
    name: "{{ app_group }}"
    system: yes
```

### 2. ✅ MinIO Webhook Configuration Error
**Error**: `A specified destination ARN does not exist or is not well-formed`

**Fix**: Made webhook configuration conditional on agent API being accessible in `roles/minio/tasks/main.yml`

- Checks if agent API is healthy first
- Configures webhook endpoint in MinIO admin config
- Only runs if agent API is running
- Already had `ignore_errors: yes` so non-blocking

### 3. ✅ Jinja2 Template Syntax Error (app_deployer)
**Error**: `No filter named 'route_key'`

**Fix**: Corrected template syntax in `roles/app_deployer/tasks/validate.yml`

Changed from:
```yaml
route_keys: "{{ route_keys + [item | route_key] }}"
```

To:
```yaml
route_keys: "{{ route_keys + [route_key] }}"
```

The `route_key` is a `vars` variable, not a filter.

### 4. ⚠️ Domain Configuration Issue (Requires Manual Fix)

**Problem**: Domain showing as `test.ai.ai.localhost` (double "ai")

**Root Cause**: Vault has incorrect `base_domain` value

**Expected**:
```
base_domain: "localhost"
domain: "ai.localhost"  (calculated)
full_domain: "test.ai.localhost"  (calculated)
```

**Current (incorrect)**:
```
base_domain: "ai.localhost"  ← WRONG
domain: "ai.ai.localhost"  (calculated incorrectly)
full_domain: "test.ai.ai.localhost"  (calculated incorrectly)
```

**Fix Required** (On Proxmox Host):

```bash
cd /root/busibox/provision/ansible
nano roles/secrets/vars/vault.yml
```

Change:
```yaml
base_domain: "ai.localhost"
```

To:
```yaml
base_domain: "localhost"
```

Then re-run:
```bash
ansible-playbook -i inventory/test/hosts.yml site.yml --limit apps --tags nextjs
```

## How Domain Variables Work

```yaml
# In vault.yml
base_domain: "localhost"

# In inventory/test/group_vars/all/00-main.yml
subdomain: test
domain: "ai.{{ base_domain }}"          # → "ai.localhost"
full_domain: "{{ subdomain }}.{{ domain }}"  # → "test.ai.localhost"
```

**Result**:
- Production: `ai.localhost`
- Test: `test.ai.localhost`
- Agents (prod): `agents.ai.localhost`
- Agents (test): `agents.test.ai.localhost`

## Deployment Status

### ✅ Completed Successfully
- PostgreSQL (TEST-pg-lxc)
- MinIO (TEST-files-lxc)
- Milvus (TEST-milvus-lxc)

### ⏳ Pending/Failed
- Agent Server (TEST-agent-lxc) - Failed on route validation
- Apps (TEST-apps-lxc) - Not yet deployed

### Next Steps

1. **Fix vault.yml** (see section 4 above)
2. **Re-run deployment**:
   ```bash
   cd /root/busibox/provision/ansible
   ansible-playbook -i inventory/test/hosts.yml site.yml --limit agent,apps
   ```

3. **Verify**:
   ```bash
   # Check systemd services
   pct exec 301 -- systemctl list-units --type=service --state=running | grep -E '(ai-portal|agent-manager|doc-intel|innovation)'
   
   # Test health endpoints
   curl http://10.96.201.201:3000/api/health  # ai-portal
   curl http://10.96.201.202:4111/auth/health # agent-server
   ```

4. **Deploy Nginx** (optional - for external access):
   ```bash
   ansible-playbook -i inventory/test/hosts.yml site.yml --limit proxy --tags nginx
   ```

## Git Commits

All fixes have been committed:
- `c3881b1` - MinIO webhook configuration fix
- `3d07724` - Jinja2 template syntax fix
- `8b1da66` - Group creation fix

## Configuration Files Reference

- **Network/Domain**: `roles/secrets/vars/vault.yml` (vault)
- **Applications**: `inventory/test/group_vars/all/00-main.yml`
- **Apps Overrides**: `inventory/test/group_vars/apps.yml`
- **Proxy Overrides**: `inventory/test/group_vars/proxy.yml`

---

**Last Updated**: 2025-10-23
**Status**: Ready for re-deployment after vault fix

