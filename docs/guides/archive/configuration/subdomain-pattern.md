# Subdomain Pattern for Multi-Environment Deployments

## Problem

When using wildcard DNS certificates like `*.ai.jaycashman.com`, you can create:
- ✅ `ai.jaycashman.com`
- ✅ `test.ai.jaycashman.com`
- ✅ `agents.ai.jaycashman.com`
- ❌ `agents.test.ai.jaycashman.com` (sub-subdomain - NOT covered by wildcard)

Sub-subdomains require either:
1. A multi-level wildcard certificate (`*.*.ai.jaycashman.com`)
2. Separate certificates for each environment
3. A different subdomain pattern

## Solution: Environment Suffix Pattern

We use environment suffixes for non-production environments:

### Production
- Main domain: `ai.jaycashman.com`
- Subdomains:
  - `agents.ai.jaycashman.com` → Agent Client
  - `docs.ai.jaycashman.com` → Doc Intel
  - `innovation.ai.jaycashman.com` → Innovation

### Test Environment
- Main domain: `test.ai.jaycashman.com`
- Subdomains:
  - `agents-test.ai.jaycashman.com` → Agent Client
  - `docs-test.ai.jaycashman.com` → Doc Intel
  - `innovation-test.ai.jaycashman.com` → Innovation

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
full_domain: "{{ domain }}"  # ai.jaycashman.com

applications:
  - name: agent-manager
    routes:
      - type: subdomain
        subdomain: agents  # → agents.ai.jaycashman.com
```

### Test Environment
```yaml
# inventory/test/group_vars/all/00-main.yml
env_suffix: test
domain: "ai.{{ base_domain }}"
full_domain: "{{ env_suffix }}.{{ domain }}"  # test.ai.jaycashman.com

applications:
  - name: agent-manager
    routes:
      - type: subdomain
        subdomain: agents-test  # → agents-test.ai.jaycashman.com
```

## DNS Configuration

For domain `ai.jaycashman.com` with test environment:

```dns
# A Records
ai.jaycashman.com.              A    YOUR_PRODUCTION_IP
test.ai.jaycashman.com.         A    YOUR_TEST_IP

# Wildcard for subdomains
*.ai.jaycashman.com.            A    YOUR_PRODUCTION_IP
```

This allows:
- `agents.ai.jaycashman.com` → Production
- `agents-test.ai.jaycashman.com` → Test (covered by wildcard)
- `test.ai.jaycashman.com` → Test main domain

## Path-Based Routing (Fallback)

All environments also support path-based routing on the main domain:

### Production
- `ai.jaycashman.com/` → AI Portal
- `ai.jaycashman.com/agents` → Agent Client
- `ai.jaycashman.com/docs` → Doc Intel
- `ai.jaycashman.com/innovation` → Innovation

### Test
- `test.ai.jaycashman.com/` → AI Portal
- `test.ai.jaycashman.com/agents` → Agent Client
- `test.ai.jaycashman.com/docs` → Doc Intel
- `test.ai.jaycashman.com/innovation` → Innovation

Path-based routing:
- ✅ Works with any SSL certificate
- ✅ Requires only one domain
- ❌ Shares session cookies (less isolation)
- ❌ May have routing conflicts

## SSL Certificate Requirements

### Production (provisioned certificates)
- Certificate must cover:
  - `ai.jaycashman.com`
  - `*.ai.jaycashman.com` (wildcard)

### Test (self-signed or Let's Encrypt)
- Certificate must cover:
  - `test.ai.jaycashman.com`
  - OR use `*.ai.jaycashman.com` wildcard (same as production)

## Testing Locally

Add to `/etc/hosts` on your local machine:

```
10.96.201.200 test.ai.jaycashman.com
10.96.201.200 agents-test.ai.jaycashman.com
10.96.201.200 docs-test.ai.jaycashman.com
10.96.201.200 innovation-test.ai.jaycashman.com
```

Then test:
```bash
# Main domain
curl -k https://test.ai.jaycashman.com

# Subdomains
curl -k https://agents-test.ai.jaycashman.com
curl -k https://docs-test.ai.jaycashman.com
curl -k https://innovation-test.ai.jaycashman.com

# Path-based routing
curl -k https://test.ai.jaycashman.com/agents
curl -k https://test.ai.jaycashman.com/docs
curl -k https://test.ai.jaycashman.com/innovation
```

## Migration from Old Pattern

If you previously used `agents.test.ai.jaycashman.com`:

1. Update `inventory/test/group_vars/all/00-main.yml`:
   - Change `subdomain: agents` → `subdomain: agents-test`
   - Change `subdomain: docs` → `subdomain: docs-test`
   - Change `subdomain: innovation` → `subdomain: innovation-test`

2. Update DNS:
   - Add A records for `agents-test.ai.jaycashman.com`, etc.
   - OR ensure wildcard `*.ai.jaycashman.com` covers them

3. Update `/etc/hosts` for local testing

4. Redeploy NGINX:
   ```bash
   cd provision/ansible
   ansible-playbook -i inventory/test/hosts.yml site.yml --tags nginx --ask-vault-pass
   ```

## Alternative Patterns

### Pattern 1: Separate base domains per environment
- Production: `ai.jaycashman.com`
- Test: `ai-test.jaycashman.com` or `ai.test.jaycashman.com`
- Requires: Separate DNS zones and SSL certificates

### Pattern 2: Port-based routing
- Production: `https://ai.jaycashman.com:443`
- Test: `https://ai.jaycashman.com:8443`
- Requires: Firewall rules, not user-friendly

### Pattern 3: Environment subdomains (current)
- Production: `agents.ai.jaycashman.com`
- Test: `agents-test.ai.jaycashman.com`
- ✅ **Recommended**: Best balance of simplicity and isolation


