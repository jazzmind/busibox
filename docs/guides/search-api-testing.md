# Search API Testing Guide

**Created**: 2025-11-17  
**Updated**: 2025-11-17  
**Status**: Active  
**Category**: Guides

## Overview

The Search API includes comprehensive tests integrated into the Proxmox host Makefile test runner. Tests can be run remotely from the Proxmox host or admin workstation without SSHing into the milvus-lxc container.

## Quick Start

### From Proxmox Host

```bash
cd /root/busibox/provision/ansible

# Quick unit tests (default)
make test-search

# All search tests
make test-search-unit
make test-search-integration

# With coverage
make test-search-coverage

# All services including search
make test-all
```

### From Admin Workstation

```bash
cd provision/ansible

# Production environment
make test-search

# Test environment
make test-search INV=inventory/test
```

## Test Targets

### `make test-search`

**Default search test target** - Runs unit tests only

- **Duration**: ~10 seconds
- **Container**: milvus-lxc (10.96.200.27)
- **Tests**: 21+ unit tests
- **Command**: `ssh root@milvus-lxc 'search-test'`

**Tests covered**:
- Milvus search operations (keyword, semantic, hybrid)
- RRF fusion algorithm
- Search term highlighting with fuzzy matching
- Cross-encoder reranking
- Semantic alignment computation
- All core services

**Example output**:
```
Running search service tests...
Container: milvus-lxc (10.96.200.27)

=== Search API Unit Tests ===
tests/unit/test_milvus_search.py::TestMilvusSearchService::test_init PASSED
tests/unit/test_milvus_search.py::TestMilvusSearchService::test_keyword_search PASSED
tests/unit/test_highlighter.py::TestHighlightingService::test_highlight_exact_match PASSED
...
✓ Search tests passed
```

---

### `make test-search-unit`

**Explicit unit tests** - Same as test-search but explicit

Use when you want to be clear about running unit tests only.

---

### `make test-search-integration`

**Integration tests** - Tests full API with real dependencies

- **Duration**: ~30 seconds
- **Requires**: Milvus, PostgreSQL, embedding service running
- **Tests**: 8+ integration tests
- **Command**: `ssh root@milvus-lxc 'search-test integration'`

**Tests covered**:
- Full API endpoints (POST /search, /search/keyword, /search/semantic)
- Authentication and authorization
- File-based filtering
- Complete search pipeline
- Error handling
- Health checks

**⚠️ Warning**: Requires all services to be running:
- Milvus (port 19530)
- PostgreSQL (pg-lxc)
- Embedding service (litellm-lxc)

**Example**:
```bash
make test-search-integration

# Expected output:
Running search integration tests...
⚠️  Warning: Requires Milvus, PostgreSQL, and embedding service running

tests/integration/test_search_api.py::TestSearchAPI::test_hybrid_search_endpoint PASSED
tests/integration/test_search_api.py::TestSearchAPI::test_keyword_search_endpoint PASSED
...
✓ Search integration tests passed
```

---

### `make test-search-coverage`

**Tests with coverage report** - Measures code coverage

- **Duration**: ~15 seconds
- **Output**: HTML coverage report
- **Command**: `ssh root@milvus-lxc 'search-test coverage'`

**Generates**:
- Terminal coverage summary
- HTML coverage report at `/opt/search/htmlcov/index.html`

**View coverage**:
```bash
# SSH to container
ssh root@10.96.200.27

# View report
cd /opt/search/htmlcov
python3 -m http.server 8080

# Or copy locally
scp -r root@10.96.200.27:/opt/search/htmlcov/ ./search-coverage/
open search-coverage/index.html
```

---

### `make test-all`

**All service tests** - Runs tests for ingest, search, agent, apps

- **Duration**: ~2-5 minutes
- **Services**: All deployed services
- **Use**: Before releases, full validation

**Includes**:
- Ingest API tests (chunking, processing)
- **Search API tests** (search, reranking, highlighting)
- Agent API tests (endpoints, auth)
- AI Portal tests (frontend, components)

```bash
make test-all

# Output:
Running ingest service tests...
✓ Ingest tests passed

Running search service tests...
✓ Search tests passed

Running agent service tests...
✓ Agent tests passed

Running app tests...
✓ AI Portal tests passed

================================
All service tests passed!
================================
```

## Direct Container Access

If you prefer to run tests directly on the container:

### SSH to milvus-lxc

```bash
ssh root@10.96.200.27  # or milvus-lxc
```

### Run Tests

```bash
# Quick unit tests
search-test

# Specific modes
search-test unit
search-test integration
search-test all
search-test coverage
search-test fast  # Skip slow tests

# With verbose output
search-test unit -vv
search-test integration -s  # Show print statements
```

### Test Locations

```
/opt/search/
├── src/                # Source code
├── tests/              # Test files
│   ├── unit/          # Unit tests
│   ├── integration/   # Integration tests
│   └── conftest.py    # Fixtures
├── pytest.ini         # Pytest configuration
└── venv/              # Virtual environment
```

## Environment Selection

Test different environments by specifying `INV`:

### Production (Default)

```bash
make test-search
# Uses: inventory/production/hosts.yml
# Container: milvus-lxc (10.96.200.27)
```

### Test Environment

```bash
make test-search INV=inventory/test
# Uses: inventory/test/hosts.yml
# Container: TEST-milvus-lxc (10.96.201.204)
```

## Workflow Examples

### After Deploying Search API

```bash
# Deploy search API
make search_api

# Run quick tests
make test-search

# If passing, try integration
make test-search-integration
```

### Before Production Release

```bash
# Test on test environment first
make test-search INV=inventory/test
make test-search-integration INV=inventory/test

# If all pass, test production
make test-search
make test-search-integration

# Full validation
make test-all
```

### Development Cycle

```bash
# 1. Make changes locally
vim srv/search/src/services/highlighter.py

# 2. Commit changes
git commit -am "fix: Improve highlighting algorithm"

# 3. Deploy to test
make search_api INV=inventory/test

# 4. Run tests
make test-search INV=inventory/test

# 5. If passing, deploy to production
make search_api
make test-search
```

## Test Output Details

### Passing Tests

```
=== Search API Unit Tests ===
collected 21 items

tests/unit/test_milvus_search.py ........  [ 38%]
tests/unit/test_highlighter.py .......     [ 71%]
tests/unit/test_reranker.py ......         [100%]

21 passed in 8.52s
✓ Search tests passed
```

### Failing Tests

```
=== Search API Unit Tests ===
FAILED tests/unit/test_highlighter.py::test_highlight_exact_match
...
Search tests FAILED
```

**Troubleshooting**:
1. SSH to container: `ssh root@10.96.200.27`
2. Run verbose: `search-test unit -vv`
3. Check logs: `journalctl -u search-api -f`
4. Verify dependencies: `systemctl status search-api`

## CI/CD Integration

### GitLab CI

```yaml
test:
  stage: test
  script:
    - cd provision/ansible
    - make test-all INV=inventory/test
```

### GitHub Actions

```yaml
- name: Run Search Tests
  run: |
    cd provision/ansible
    make test-search INV=inventory/test
```

## Best Practices

1. **Always test after deployment**
   ```bash
   make search_api && make test-search
   ```

2. **Use test environment first**
   ```bash
   make search_api INV=inventory/test
   make test-search INV=inventory/test
   ```

3. **Run coverage periodically**
   ```bash
   make test-search-coverage
   ```

4. **Test all services before release**
   ```bash
   make test-all
   ```

5. **Use verbose output for debugging**
   ```bash
   ssh root@milvus-lxc 'search-test unit -vv'
   ```

## Troubleshooting

### Tests Won't Run

**Problem**: `ERROR: Could not resolve milvus-lxc IP address`

**Solution**: Check inventory file
```bash
cat inventory/production/hosts.yml | grep milvus
# Should show: ansible_host: 10.96.200.27
```

### Integration Tests Fail

**Problem**: Integration tests fail with connection errors

**Solution**: Verify services are running
```bash
# Check Milvus
curl http://10.96.200.27:9091/healthz

# Check PostgreSQL
ssh root@10.96.200.26 'systemctl status postgresql'

# Check embedding service
curl http://10.96.200.30:8000/health
```

### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'services'`

**Solution**: PYTHONPATH is set by search-test script automatically. If running pytest directly:
```bash
cd /opt/search
export PYTHONPATH=/opt/search/src:$PYTHONPATH
pytest tests/
```

## References

- **Makefile Reference**: `docs/reference/makefile-test-targets.md`
- **Test README**: `srv/search/tests/README.md`
- **Search API Docs**: `docs/architecture/search-service.md`
- **Deployment Guide**: `docs/deployment/search-api.md`



