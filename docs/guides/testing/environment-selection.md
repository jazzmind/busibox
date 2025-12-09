# Running Tests in Different Environments

**Created**: 2025-11-17  
**Updated**: 2025-11-17  
**Status**: Active  
**Category**: Guides

## Overview

All test targets in the Ansible Makefile are now **environment-aware**. They automatically detect which environment you're testing (production or test) based on the `INV` variable.

## Quick Examples

### Production Environment (Default)

```bash
cd /root/busibox/provision/ansible

# Deploy and test in production
make search-api              # Deploy to production
make test-search             # Test in production

make ingest                  # Deploy ingest to production  
make test-ingest             # Test ingest in production

make test-all                # Test all services in production
```

### Test Environment

```bash
cd /root/busibox/provision/ansible

# Deploy and test in test environment
make search-api INV=inventory/test
make test-search INV=inventory/test

make ingest INV=inventory/test
make test-ingest INV=inventory/test

make test-all INV=inventory/test
```

## How It Works

### Automatic IP Detection

The Makefile dynamically extracts container IPs from the inventory file:

```makefile
# When INV=inventory/production (default):
MILVUS_IP = 10.96.200.27   # Production milvus-lxc

# When INV=inventory/test:
MILVUS_IP = 10.96.201.204  # TEST-milvus-lxc
```

### Detection Logic

For each service, the Makefile:

1. **Tries production hostname**: `ansible-inventory --host ingest-lxc`
2. **Falls back to test hostname**: `ansible-inventory --host TEST-ingest-lxc`
3. **Falls back to default**: Hardcoded production IP as last resort

This means the **same command** works in both environments:

```bash
# These automatically use the correct environment based on INV
make test-search                    # Uses production IPs
make test-search INV=inventory/test # Uses test IPs
```

## All Test Targets

### Search API Tests

```bash
# Production
make test-search                   # Quick unit tests
make test-search-unit              # Explicit unit tests
make test-search-integration       # Integration tests
make test-search-coverage          # With coverage report

# Test environment
make test-search INV=inventory/test
make test-search-integration INV=inventory/test
```

### Ingest Service Tests

```bash
# Production
make test-ingest                   # Quick tests
make test-ingest-all               # All tests
make test-ingest-coverage          # With coverage

# Test environment
make test-ingest INV=inventory/test
make test-ingest-all INV=inventory/test
```

### Agent API Tests

```bash
# Production
make test-agent

# Test environment
make test-agent INV=inventory/test
```

### AI Portal Tests

```bash
# Production
make test-apps

# Test environment
make test-apps INV=inventory/test
```

### All Services

```bash
# Production
make test-all

# Test environment
make test-all INV=inventory/test
```

## Workflow Examples

### Testing Production After Deployment

```bash
cd /root/busibox/provision/ansible

# Deploy to production
make search
make ingest
make agent

# Test production
make test-search
make test-ingest
make test-agent

# Or test everything
make test-all
```

### Testing Test Environment Before Production

```bash
cd /root/busibox/provision/ansible

# Deploy to test
make search INV=inventory/test
make ingest INV=inventory/test

# Test in test environment
make test-search INV=inventory/test
make test-ingest INV=inventory/test

# If tests pass, deploy to production
make search
make ingest

# Verify production
make test-search
make test-ingest
```

### Quick Smoke Test After Code Changes

```bash
# 1. Update code locally
vim srv/search/src/services/highlighter.py
git commit -am "fix: Improve highlighting"

# 2. Deploy to test
make search-api INV=inventory/test

# 3. Run quick tests
make test-search INV=inventory/test

# 4. If passing, deploy to production
make search-api
make test-search
```

## Troubleshooting

### Wrong IP Being Used

**Problem**: Tests are connecting to wrong container

**Debug**:
```bash
# Check what IPs are being detected
cd /root/busibox/provision/ansible
make test-search -n  # Dry run shows variables

# Manually check inventory
ansible-inventory -i inventory/production --host milvus-lxc | grep ansible_host
ansible-inventory -i inventory/test --host TEST-milvus-lxc | grep ansible_host
```

**Solution**: Ensure your inventory files have correct IPs configured

### Tests Fail in Production But Pass in Test

**Problem**: Tests work in test but fail in production

**Check**:
```bash
# Verify production services are running
ssh root@10.96.200.27 'systemctl status search-api'
ssh root@10.96.200.206 'systemctl status ingest-api'

# Check production service logs
ssh root@10.96.200.27 'journalctl -u search-api -n 100'

# Run tests with verbose output
ssh root@10.96.200.27 'search-test unit -vv'
```

### Container Not Found

**Problem**: `ERROR: Could not resolve milvus-lxc IP address`

**Solution**: Check inventory file
```bash
cat inventory/production/hosts.yml | grep -A 3 milvus
cat inventory/test/hosts.yml | grep -A 3 milvus

# Ensure hosts are defined with ansible_host
```

## Environment Variables

The Makefile respects these variables:

### INV (Inventory)

```bash
INV=inventory/production  # Default
INV=inventory/test        # Test environment
```

### Container IPs (Auto-detected)

```bash
INGEST_IP   # Ingest container IP
AGENT_IP    # Agent container IP  
APPS_IP     # Apps container IP
MILVUS_IP   # Milvus container IP
```

These are automatically set based on `INV`, but you can override:

```bash
# Override specific IP for testing
make test-search MILVUS_IP=10.96.201.204
```

## Verifying Environment Detection

### Check Which Environment You're Using

```bash
cd /root/busibox/provision/ansible

# Default (production)
make test-search -n | grep "Container:"
# Should show production IP

# Test environment
make test-search INV=inventory/test -n | grep "Container:"
# Should show test IP
```

### Verify IP Detection

```bash
# Production
ansible-inventory -i inventory/production --host milvus-lxc | grep ansible_host

# Test  
ansible-inventory -i inventory/test --host TEST-milvus-lxc | grep ansible_host
```

## Best Practices

1. **Always specify environment explicitly for test**:
   ```bash
   # Good: Clear which environment
   make test-search INV=inventory/test
   
   # Risky: Might accidentally test production
   make test-search
   ```

2. **Test in test environment first**:
   ```bash
   make search-api INV=inventory/test
   make test-search INV=inventory/test
   # If passing:
   make search-api
   make test-search
   ```

3. **Use test-all for comprehensive validation**:
   ```bash
   # Before releases
   make test-all INV=inventory/test
   make test-all  # Production
   ```

4. **Run tests after every deployment**:
   ```bash
   make search && make test-search
   make ingest && make test-ingest
   ```

## CI/CD Integration

### GitLab CI

```yaml
test:staging:
  stage: test
  script:
    - cd provision/ansible
    - make test-all INV=inventory/test
  
test:production:
  stage: test
  script:
    - cd provision/ansible
    - make test-all
  only:
    - main
```

### GitHub Actions

```yaml
- name: Test in Staging
  run: |
    cd provision/ansible
    make test-all INV=inventory/test

- name: Test in Production
  if: github.ref == 'refs/heads/main'
  run: |
    cd provision/ansible
    make test-all
```

## References

- **Makefile Test Targets**: `docs/testing/makefile-test-targets.md`
- **Search API Testing**: `docs/guides/search-api-testing.md`
- **Inventory Structure**: `provision/ansible/inventory/*/hosts.yml`
- **Network Configuration**: `provision/ansible/inventory/*/group_vars/all/00-main.yml`

