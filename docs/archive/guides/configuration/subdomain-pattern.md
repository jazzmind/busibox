# Subdomain Pattern for Multi-Environment Deployments

## Problem

When using wildcard DNS certificates like `*.ai.localhost`, you can create:
- ✅ `ai.localhost`
- ✅ `test.ai.localhost`
- ✅ `agents.ai.localhost`
- ❌ `agents.test.ai.localhost` (sub-subdomain - NOT covered by wildcard)

Sub-subdomains require either:
1. A multi-level wildcard certificate (`*.*.ai.localhost`)
2. Separate certificates for each environment
3. A different subdomain pattern

## Solution: Environment Suffix Pattern

We use environment suffixes for non-production environments:

### Production
- Main domain: `ai.localhost`
- Subdomains:
  - `agents.ai.localhost` → Agent Client
  - `docs.ai.localhost` → Doc Intel
  - `innovation.ai.localhost` → Innovation

### Test Environment
- Main domain: `test.ai.localhost`
- Subdomains:
  - `agents-test.ai.localhost` → Agent Client
  - `docs-test.ai.localhost` → Doc Intel
  - `innovation-test.ai.localhost` → Innovation

This pattern:
- ✅ Works with single-level wildcard certificates
- ✅ Keeps environments clearly separated
- ✅ Allows easy DNS management
- ✅ Follows common industry patterns

## Configuration

### Production Environment
```yaml
# inventory/production/group_vars/all/00-main.yml
domain: "ai.{{ base_domain }}"
full_domain: "{{ domain }}"  # ai.localhost

applications:
  - name: agent-manager
    routes:
      - type: subdomain
        subdomain: agents  # → agents.ai.localhost
```

### Test Environment
```yaml
# inventory/test/group_vars/all/00-main.yml
env_suffix: test
domain: "ai.{{ base_domain }}"
full_domain: "{{ env_suffix }}.{{ domain }}"  # test.ai.localhost

applications:
  - name: agent-manager
    routes:
      - type: subdomain
        subdomain: agents-test  # → agents-test.ai.localhost
```

## DNS Configuration

For domain `ai.localhost` with test environment:

```dns
# A Records
ai.localhost.              A    YOUR_PRODUCTION_IP
test.ai.localhost.         A    YOUR_TEST_IP

# Wildcard for subdomains
*.ai.localhost.            A    YOUR_PRODUCTION_IP
```

This allows:
- `agents.ai.localhost` → Production
- `agents-test.ai.localhost` → Test (covered by wildcard)
- `test.ai.localhost` → Test main domain

## Path-Based Routing (Fallback)

All environments also support path-based routing on the main domain:

### Production
- `ai.localhost/` → AI Portal
- `ai.localhost/agents` → Agent Client
- `ai.localhost/docs` → Doc Intel
- `ai.localhost/innovation` → Innovation

### Test
- `test.ai.localhost/` → AI Portal
- `test.ai.localhost/agents` → Agent Client
- `test.ai.localhost/docs` → Doc Intel
- `test.ai.localhost/innovation` → Innovation

Path-based routing:
- ✅ Works with any SSL certificate
- ✅ Requires only one domain
- ❌ Shares session cookies (less isolation)
- ❌ May have routing conflicts

## SSL Certificate Requirements

### Production (provisioned certificates)
- Certificate must cover:
  - `ai.localhost`
  - `*.ai.localhost` (wildcard)

### Test (self-signed or Let's Encrypt)
- Certificate must cover:
  - `test.ai.localhost`
  - OR use `*.ai.localhost` wildcard (same as production)

## Testing Locally

Add to `/etc/hosts` on your local machine:

```
10.96.201.200 test.ai.localhost
10.96.201.200 agents-test.ai.localhost
10.96.201.200 docs-test.ai.localhost
10.96.201.200 innovation-test.ai.localhost
```

Then test:
```bash
# Main domain
curl -k https://test.ai.localhost

# Subdomains
curl -k https://agents-test.ai.localhost
curl -k https://docs-test.ai.localhost
curl -k https://innovation-test.ai.localhost

# Path-based routing
curl -k https://test.ai.localhost/agents
curl -k https://test.ai.localhost/docs
curl -k https://test.ai.localhost/innovation
```

## Migration from Old Pattern

If you previously used `agents.test.ai.localhost`:

1. Update `inventory/test/group_vars/all/00-main.yml`:
   - Change `subdomain: agents` → `subdomain: agents-test`
   - Change `subdomain: docs` → `subdomain: docs-test`
   - Change `subdomain: innovation` → `subdomain: innovation-test`

2. Update DNS:
   - Add A records for `agents-test.ai.localhost`, etc.
   - OR ensure wildcard `*.ai.localhost` covers them

3. Update `/etc/hosts` for local testing

4. Redeploy NGINX:
   ```bash
   cd provision/ansible
   ansible-playbook -i inventory/test/hosts.yml site.yml --tags nginx --ask-vault-pass
   ```

## Alternative Patterns

### Pattern 1: Separate base domains per environment
- Production: `ai.localhost`
- Test: `ai-test.localhost` or `ai.test.localhost`
- Requires: Separate DNS zones and SSL certificates

### Pattern 2: Port-based routing
- Production: `https://ai.localhost:443`
- Test: `https://ai.localhost:8443`
- Requires: Firewall rules, not user-friendly

### Pattern 3: Environment subdomains (current)
- Production: `agents.ai.localhost`
- Test: `agents-test.ai.localhost`
- ✅ **Recommended**: Best balance of simplicity and isolation


