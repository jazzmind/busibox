# Makefile Test Targets Reference

## Overview

The Ansible Makefile provides convenient targets for running tests on remote containers from the Proxmox host. This allows you to validate deployments and run test suites without manually SSH-ing into each container.

## Location

- **Makefile**: `provision/ansible/Makefile`
- **Run from**: Proxmox host or admin workstation
- **Requires**: SSH access to containers

## Quick Start

```bash
# From Proxmox host
cd /root/busibox/provision/ansible

# Run ingest tests (default, fast)
make test-ingest

# Run all tests
make test-all

# Run with test environment
make test-ingest INV=inventory/test
```

## Test Targets

### Default Test Target

```bash
make test
```

**What it does**: Runs the default test suite (currently ingest tests)

**Use when**: Quick validation after deployment

**Output**:
```
Running ingest service tests...
Container: ingest-lxc (10.96.200.30)

=== Ingest Chunker Tests ===
collected 23 items
...
✓ Ingest tests passed
================================
All tests passed!
================================
```

---

### Ingest Service Tests

#### 1. Quick Chunker Tests (Default)

```bash
make test-ingest
```

**What it does**: Runs chunker tests only (~5 seconds, 23 tests)

**Container**: ingest-lxc

**Command executed**: `ssh root@<INGEST_IP> 'ingest-test'`

**Use when**:
- After deploying ingest service
- Validating chunking fixes
- Quick smoke test

**Expected output**: 23 tests passing

---

#### 2. All Ingest Tests (Including Integration)

```bash
make test-ingest-all
```

**What it does**: Runs all ingest tests including integration tests (~60 seconds)

**Container**: ingest-lxc

**Command executed**: `ssh root@<INGEST_IP> 'ingest-test all'`

**Use when**:
- Full validation before release
- Testing complete ingestion pipeline
- Integration testing with Milvus, PostgreSQL, MinIO

**Warning**: Requires all services (Milvus, PostgreSQL, MinIO, Redis) to be running

---

#### 3. Ingest Tests with Coverage

```bash
make test-ingest-coverage
```

**What it does**: Runs chunker tests with code coverage report

**Container**: ingest-lxc

**Command executed**: `ssh root@<INGEST_IP> 'ingest-test coverage'`

**Use when**:
- Measuring test coverage
- Identifying untested code
- Generating coverage reports

**Output**: 
- Test results
- Coverage percentage
- HTML report location

**View coverage**:
```bash
ssh root@<INGEST_IP>
# View /srv/ingest/htmlcov/index.html
```

---

### Search API Tests

#### 1. Quick Unit Tests (Default)

```bash
make test-search
```

**What it does**: Runs search API unit tests (~10 seconds, 21+ tests)

**Container**: milvus-lxc

**Command executed**: `ssh root@<MILVUS_IP> 'search-test'`

**Use when**:
- After deploying search API
- Validating search functionality
- Quick smoke test

**Tests covered**:
- Milvus search operations (keyword, semantic, hybrid)
- RRF fusion algorithm
- Search term highlighting
- Cross-encoder reranking
- Semantic alignment

**Expected output**: All unit tests passing

---

#### 2. Unit Tests Only

```bash
make test-search-unit
```

**What it does**: Runs search API unit tests explicitly (~10 seconds)

**Container**: milvus-lxc

**Command executed**: `ssh root@<MILVUS_IP> 'search-test unit'`

**Use when**:
- Testing core search logic
- Validating algorithms
- Quick validation without external dependencies

---

#### 3. Integration Tests

```bash
make test-search-integration
```

**What it does**: Runs search API integration tests (~30 seconds)

**Container**: milvus-lxc

**Command executed**: `ssh root@<MILVUS_IP> 'search-test integration'`

**Use when**:
- Full validation before release
- Testing complete search pipeline
- Integration testing with Milvus, PostgreSQL, embedding service

**Warning**: Requires all services (Milvus, PostgreSQL, liteLLM/embedding) to be running

**Tests covered**:
- Full API endpoints
- Authentication and authorization
- File filtering
- Complete search flow
- Error handling

---

#### 4. Tests with Coverage

```bash
make test-search-coverage
```

**What it does**: Runs search tests with code coverage report

**Container**: milvus-lxc

**Command executed**: `ssh root@<MILVUS_IP> 'search-test coverage'`

**Use when**:
- Measuring test coverage
- Identifying untested code
- Generating coverage reports

**Output**: 
- Test results
- Coverage percentage
- HTML report location

**View coverage**:
```bash
ssh root@<MILVUS_IP>
# View /opt/search/htmlcov/index.html
```

---

### Agent Service Tests

```bash
make test-agent
```

**What it does**: Runs agent API tests

**Container**: agent-lxc

**Command executed**: `ssh root@<AGENT_IP> 'cd /srv/agent && source venv/bin/activate && npm test'`

**Use when**:
- After deploying agent service
- Validating agent API endpoints
- Testing agent functionality

**Note**: Requires agent service to have test suite configured

---

### App Tests

```bash
make test-apps
```

**What it does**: Runs AI Portal tests

**Container**: apps-lxc

**Command executed**: `ssh root@<APPS_IP> 'cd /srv/apps/ai-portal && npm test'`

**Use when**:
- After deploying AI Portal
- Validating frontend functionality
- Testing UI components

**Note**: Requires AI Portal to have test suite configured

---

### All Tests

```bash
make test-all
```

**What it does**: Runs tests for all services (ingest, search, agent, apps)

**Use when**:
- Full system validation
- Before major releases
- Comprehensive testing

**Duration**: ~2-5 minutes (depending on test suites)

**Services tested**:
- Ingest API (chunking, processing)
- Search API (search, reranking, highlighting)
- Agent API (endpoints, auth)
- AI Portal (frontend, components)

---

## Environment Selection

All test targets support environment selection via the `INV` variable:

### Production (Default)

```bash
make test-ingest
# Uses inventory/production
```

### Test Environment

```bash
make test-ingest INV=inventory/test
```

### Custom Inventory

```bash
make test-ingest INV=inventory/custom
```

---

## How It Works

### IP Address Resolution

The Makefile automatically resolves container IPs from the inventory:

```makefile
INGEST_IP=$(shell ansible-inventory -i $(INV) --host ingest-lxc 2>/dev/null | grep -o '"ansible_host": "[^"]*"' | cut -d'"' -f4 || echo "10.96.200.30")
```

**Fallback**: If inventory lookup fails, uses default production IPs

### SSH Execution

Tests are executed via SSH:

```bash
ssh root@<CONTAINER_IP> '<test-command>'
```

**Requirements**:
- SSH key authentication configured
- Root access to containers
- Test infrastructure deployed

---

## Common Workflows

### After Deployment

```bash
# Deploy ingest service
make ingest

# Run tests to validate
make test-ingest
```

### Full Deployment + Testing

```bash
# Deploy all services
make all

# Run all tests
make test-all
```

### Test-Driven Deployment

```bash
# Deploy to test environment
make ingest INV=inventory/test

# Run tests on test environment
make test-ingest INV=inventory/test

# If tests pass, deploy to production
make ingest

# Validate production
make test-ingest
```

### Coverage Analysis

```bash
# Run with coverage
make test-ingest-coverage

# Copy coverage report to local machine
scp -r root@10.96.200.30:/srv/ingest/htmlcov ./ingest-coverage

# Open in browser
open ingest-coverage/index.html
```

---

## Troubleshooting

### Issue: SSH Connection Failed

**Symptom**: `ssh: connect to host 10.96.200.30 port 22: Connection refused`

**Solutions**:
1. Verify container is running: `pct status 206`
2. Check SSH service: `pct enter 206` then `systemctl status ssh`
3. Verify IP address: `pct config 206 | grep net0`

### Issue: Test Command Not Found

**Symptom**: `ingest-test: command not found`

**Solutions**:
1. Verify test infrastructure is deployed:
   ```bash
   ssh root@10.96.200.30 'which ingest-test'
   ```
2. Redeploy ingest service:
   ```bash
   make ingest
   ```

### Issue: Tests Failing

**Symptom**: Tests run but fail

**Solutions**:
1. Check logs:
   ```bash
   ssh root@10.96.200.30 'journalctl -u ingest-worker -n 100'
   ```
2. Run tests manually with verbose output:
   ```bash
   ssh root@10.96.200.30 'ingest-test -vv'
   ```
3. Verify dependencies:
   ```bash
   ssh root@10.96.200.30 'cd /srv/ingest && source venv/bin/activate && python -m spacy info en_core_web_sm'
   ```

### Issue: Wrong Environment

**Symptom**: Tests running against wrong environment

**Solution**: Always specify `INV` variable:
```bash
make test-ingest INV=inventory/test
```

---

## Integration with CI/CD

### Example: GitLab CI

```yaml
test:
  stage: test
  script:
    - cd provision/ansible
    - make test-all INV=inventory/test
  only:
    - merge_requests
```

### Example: GitHub Actions

```yaml
- name: Run Tests
  run: |
    cd provision/ansible
    make test-all INV=inventory/test
```

### Example: Jenkins

```groovy
stage('Test') {
    steps {
        dir('provision/ansible') {
            sh 'make test-all INV=inventory/test'
        }
    }
}
```

---

## Best Practices

1. **Always test after deployment**
   ```bash
   make ingest && make test-ingest
   ```

2. **Use test environment first**
   ```bash
   make ingest INV=inventory/test
   make test-ingest INV=inventory/test
   ```

3. **Run coverage periodically**
   ```bash
   make test-ingest-coverage
   ```

4. **Test all services before release**
   ```bash
   make test-all
   ```

5. **Document test failures**
   - Capture output
   - Check logs
   - File issues

---

## Quick Reference Card

```
Command                          Description
-------------------------------  ----------------------------------
make test                        Run default tests (ingest)
make test-ingest                 Run ingest chunker tests (fast)
make test-ingest-all             Run all ingest tests (slow)
make test-ingest-coverage        Run with coverage report
make test-search                 Run search API unit tests (fast)
make test-search-unit            Run search unit tests only
make test-search-integration     Run search integration tests
make test-search-coverage        Run search tests with coverage
make test-agent                  Run agent API tests
make test-apps                   Run AI Portal tests
make test-all                    Run all service tests

# With environment selection
make test-ingest INV=inventory/test
make test-search INV=inventory/test

# After deployment
make ingest && make test-ingest
```

---

## Related Documentation

- **Test Runner**: `docs/reference/ingest-test-runner.md`
- **Deployment**: `DEPLOY_CHUNKING_FIXES.md`
- **Testing Strategy**: `TESTING.md`
- **Makefile**: `provision/ansible/Makefile`

---

## Future Enhancements

Planned test targets:

1. **Database Tests**
   ```bash
   make test-pg        # PostgreSQL schema/migration tests
   make test-milvus    # Milvus vector operations tests
   ```

2. **Service Integration Tests**
   ```bash
   make test-integration  # Full pipeline tests
   ```

3. **Performance Tests**
   ```bash
   make test-perf      # Load testing
   ```

4. **Security Tests**
   ```bash
   make test-security  # Security scanning
   ```

---

## Conclusion

The Makefile test targets provide a convenient, consistent way to run tests across all services from a single location. This enables:

- ✅ Quick validation after deployment
- ✅ Consistent test execution
- ✅ Environment-aware testing
- ✅ CI/CD integration
- ✅ Coverage analysis
- ✅ Reduced manual SSH operations

Use `make test-ingest` as your go-to command for validating ingestion fixes!

