# Troubleshooting: search-test Command Not Found

**Created**: 2025-11-17  
**Updated**: 2025-11-17  
**Status**: Active  
**Category**: Troubleshooting

## Problem

When running `make test-search`, you get:

```
bash: line 1: search-test: command not found
Search tests FAILED
make: *** [Makefile:179: test-search] Error 1
```

## Root Cause

The `search-test` script hasn't been deployed to the target milvus-lxc container yet. This script is installed by the `search_api` Ansible role (deployed via `make search-api`).

## Solution

Deploy the search API first, which installs the test runner:

### For Test Environment

```bash
cd /root/busibox/provision/ansible

# Deploy search services (Milvus + Search API)
make search INV=inventory/test

# Or just deploy Search API if Milvus already deployed
make search-api INV=inventory/test

# Now tests will work
make test-search INV=inventory/test
```

### For Production Environment

```bash
cd /root/busibox/provision/ansible

# Deploy search services
make search

# Or just Search API
make search-api

# Now tests will work
make test-search
```

## Verification

After deployment, verify the script exists:

```bash
# For test environment (adjust IP if needed)
ssh root@10.96.200.204 'which search-test'
# Should output: /usr/local/bin/search-test

# Try running it
ssh root@10.96.200.204 'search-test --help'
```

## Complete Deployment Order

When setting up search from scratch:

```bash
cd /root/busibox/provision/ansible

# 1. Deploy Milvus (if not already deployed)
make milvus INV=inventory/test

# 2. Deploy Search API (includes test script)
make search-api INV=inventory/test

# 3. Now you can run tests
make test-search INV=inventory/test
make test-search-integration INV=inventory/test

# Or use the combined target
make search INV=inventory/test  # Does steps 1-2
```

## What Gets Deployed

When you run `make search-api`, Ansible:

1. Creates search user and directories
2. Copies search service source code
3. Copies test files to `/opt/search/tests/`
4. Copies pytest configuration
5. Installs dependencies (including pytest)
6. **Deploys `/usr/local/bin/search-test`** ← This is what's missing
7. Starts search-api service

## Direct Container Access

If you want to verify or run tests directly:

```bash
# SSH to the container
ssh root@10.96.200.204  # Test environment
# or
ssh root@10.96.200.27   # Production

# Check if search-test exists
ls -la /usr/local/bin/search-test

# Run tests directly
search-test
search-test integration
search-test --help
```

## Related Issues

### "Virtual environment not found"

If you get:
```
ERROR: Virtual environment not found at /opt/search/venv
```

**Solution**: Deploy the full search_api role:
```bash
make search-api INV=inventory/test
```

### "Search service not found at /opt/search"

**Solution**: You're on the wrong container. Search tests run on milvus-lxc:
```bash
# Check your Makefile is using correct IP
grep MILVUS_IP provision/ansible/Makefile

# Check inventory
cat inventory/test/hosts.yml | grep milvus -A 2
```

## Prevention

Always deploy services before running tests:

```bash
# Bad (tests will fail)
make test-search INV=inventory/test

# Good (deploy first)
make search-api INV=inventory/test
make test-search INV=inventory/test

# Or use the combined search target
make search INV=inventory/test  # Deploys everything
make test-search INV=inventory/test  # Now this works
```

## Quick Fix Script

If you just want to get tests working:

```bash
#!/bin/bash
# quick-fix-search-tests.sh

ENV="${1:-test}"  # Default to test environment
INV_FILE="inventory/${ENV}/hosts.yml"

echo "Deploying search_api to ${ENV} environment..."
cd /root/busibox/provision/ansible

# Deploy search API
make search-api INV=inventory/${ENV}

echo ""
echo "✓ Search API deployed"
echo ""
echo "You can now run:"
echo "  make test-search INV=inventory/${ENV}"
echo "  make test-search-integration INV=inventory/${ENV}"
```

Save and run:
```bash
cd /root/busibox
chmod +x quick-fix-search-tests.sh
./quick-fix-search-tests.sh test
```

## References

- **Search API Deployment**: `docs/deployment/search-api.md`
- **Search API Testing**: `docs/guides/search-api-testing.md`
- **Makefile Targets**: `docs/reference/makefile-test-targets.md`
- **Architecture**: `docs/architecture/search-service.md`

