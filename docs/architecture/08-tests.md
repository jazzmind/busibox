# Testing Architecture

**Created**: 2025-12-21  
**Updated**: 2025-12-21  
**Status**: Active  
**Category**: Architecture  

## Philosophy: No Mocks

**Mocks are forbidden in this codebase.**

Mocks:
- Hide real integration problems
- Create false positives (tests pass but production breaks)
- Add maintenance burden when APIs change
- Make tests useless for catching real bugs

Instead, we use:
- **Real databases** (PostgreSQL, Milvus, Redis)
- **Real services** (MinIO, authz)
- **Real network calls** (within our test infrastructure)
- **Test containers** as a clean, isolated environment

If a test can't run without mocking, the code needs refactoring, not the test.

---

## Test Execution Methods

### 1. Interactive Menu (Recommended for Exploration)

```bash
make test
```

This launches an interactive menu that lets you:
1. Select environment (test/production)
2. Choose what to test (service tests, infrastructure tests, local tests)
3. Pick specific services

**When to use:** Exploring the test suite, running ad-hoc tests, first-time users.

### 2. Command Line (Recommended for CI/CD)

```bash
# Run tests on container
make test SERVICE=authz INV=test

# Run tests locally against container backends
make test SERVICE=authz INV=test MODE=local
```

**Parameters:**
- `SERVICE`: authz, ingest, search, agent, all
- `INV`: test, production (default: test)
- `MODE`: container (default), local

**When to use:** CI/CD pipelines, scripts, repeatable test runs.

### 3. Local Testing Against Remote Containers

```bash
make test-local SERVICE=authz INV=test
```

This runs:
1. Generates `.env.local` with container IPs and vault secrets
2. Runs pytest on your local machine
3. Tests connect to real services on test containers

**When to use:** Rapid development iteration, debugging test failures, IDE integration.

**How it works:**
```
┌─────────────────┐     ┌─────────────────────────────────────┐
│ Local Machine   │     │ Test Containers (Proxmox)           │
│                 │     │                                     │
│ ┌─────────────┐ │     │  ┌──────────┐  ┌──────────┐        │
│ │ pytest      │─┼─────┼─▶│ authz    │  │ postgres │        │
│ │ (local code)│ │     │  └──────────┘  └──────────┘        │
│ └─────────────┘ │     │  ┌──────────┐  ┌──────────┐        │
│                 │     │  │ minio    │  │ milvus   │        │
│ .env.local has: │     │  └──────────┘  └──────────┘        │
│ - container IPs │     │                                     │
│ - vault secrets │     │                                     │
└─────────────────┘     └─────────────────────────────────────┘
```

### 4. Direct Execution on Container

SSH to the container and run pytest directly:

```bash
ssh root@10.96.201.206  # ingest container
cd /srv/ingest
source venv/bin/activate
pytest tests/ -v
```

**When to use:** Deep debugging, checking container state, investigating deployment issues.

---

## Service-Specific Testing

### AuthZ Service

```bash
# On container (via make)
make test SERVICE=authz INV=test

# Locally
make test-local SERVICE=authz INV=test

# Direct
ssh root@10.96.201.210 "cd /srv/authz/app && source ../venv/bin/activate && pytest tests/ -v"
```

**Test coverage:**
- OAuth2 token exchange (60 tests)
- RBAC operations (roles, users, permissions)
- Envelope encryption (keystore, KEK/DEK management)
- Admin endpoints
- Database integration

### Ingest Service

```bash
# On container (via make)
make test SERVICE=ingest INV=test

# Locally
make test-local SERVICE=ingest INV=test

# Direct
ssh root@10.96.201.206 "cd /srv/ingest && source venv/bin/activate && pytest tests/ -v"
```

**Test coverage:**
- File upload with chunking
- Text extraction (PDF, DOCX)
- Semantic chunking
- Embedding generation
- Milvus insertion
- Redis job queue
- API routes with scope enforcement

### Search Service

```bash
make test SERVICE=search INV=test
```

**Test coverage:**
- Hybrid search (dense + sparse)
- Role-based result filtering
- Milvus partition queries

### Agent Service

```bash
make test SERVICE=agent INV=test
```

**Test coverage:**
- Agent execution
- Tool calling
- Workflow management
- SSE streaming

---

## Debugging Test Failures

### Step 1: Identify the Failure Type

```bash
# Run with verbose output
make test SERVICE=ingest INV=test  # Check output for patterns
```

**Import Errors** (during collection):
```
ERROR tests/api/test_files.py
ModuleNotFoundError: No module named 'redis.asyncio'
```
→ See [Python Test Import Gotchas](../development/reference/python-test-import-gotchas.md)

**Test Failures** (during execution):
```
FAILED tests/api/test_upload.py::test_upload_file - AssertionError
```
→ Need to debug the specific test

**Fixture Errors**:
```
ERROR tests/test_something.py::test_func - RuntimeError: Event loop is closed
```
→ Async fixture configuration issue

### Step 2: Run the Failing Test Directly

```bash
# SSH to container
ssh root@10.96.201.206

# Run single test with full output
cd /srv/ingest
source venv/bin/activate
pytest tests/api/test_files.py::test_get_file_metadata_success -v -s --tb=long
```

### Step 3: Check Service Dependencies

```bash
# Check if services are running
ssh root@10.96.201.206 "systemctl status ingest-api ingest-worker"
ssh root@10.96.201.203 "systemctl status postgresql"
ssh root@10.96.201.205 "systemctl status minio"
ssh root@10.96.201.204 "systemctl status milvus"
```

### Step 4: Check Logs

```bash
# Get service logs
ssh root@10.96.201.206 "journalctl -u ingest-api -n 100 --no-pager"
ssh root@10.96.201.206 "journalctl -u ingest-worker -n 100 --no-pager"
```

### Step 5: Check Environment

```bash
# Verify environment variables are set
ssh root@10.96.201.206 "cat /srv/ingest/.env | grep -v PASSWORD"

# Verify PYTHONPATH
ssh root@10.96.201.206 "cd /srv/ingest && source venv/bin/activate && python -c 'import sys; print(sys.path[:5])'"
```

---

## Common Issues and Solutions

### Issue: "ModuleNotFoundError: No module named 'X.Y'; 'X' is not a package"

**Cause:** Something is corrupting `sys.modules` or a file is shadowing a package.

**Debug:**
```bash
# Check for files shadowing packages
find /srv/ingest/src -name "redis.py" -o -name "minio.py"

# Check for sys.modules pollution in tests
grep -r "sys\.modules\[" tests/
```

**Solution:** See [Python Test Import Gotchas](../development/reference/python-test-import-gotchas.md)

### Issue: Tests pass individually but fail together

**Cause:** Test isolation problem - one test is affecting another.

**Debug:**
```bash
# Binary search to find the interfering test
pytest tests/test_a.py tests/api/ --collect-only  # Works?
pytest tests/test_b.py tests/api/ --collect-only  # Works?
pytest tests/test_a.py tests/test_b.py tests/api/ --collect-only  # Fails?
```

**Solution:** The test collected between the working and failing state is the culprit.

### Issue: "RuntimeError: Event loop is closed"

**Cause:** pytest-asyncio fixture scope mismatch.

**Solution:** Ensure conftest.py has proper event_loop fixture:
```python
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
```

### Issue: Connection refused to service

**Cause:** Service not running or wrong IP.

**Debug:**
```bash
# Check if service is listening
ssh root@10.96.201.206 "netstat -tlnp | grep 8002"

# Test connectivity
curl http://10.96.201.206:8002/health
```

**Solution:** Start the service or fix the IP in environment.

### Issue: Stale .pyc files causing import errors

**Cause:** Deleted/renamed Python files leave behind .pyc that Python still loads.

**Solution:**
```bash
# Clear pycache
find /srv/ingest -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null
```

Our Ansible deployment now cleans these automatically before syncing.

---

## Test Infrastructure

### Container IP Addresses (Test Environment)

| Service    | IP              | Port  |
|------------|-----------------|-------|
| postgres   | 10.96.201.203   | 5432  |
| milvus     | 10.96.201.204   | 19530 |
| minio      | 10.96.201.205   | 9000  |
| ingest     | 10.96.201.206   | 8002  |
| search     | 10.96.201.207   | 8001  |
| authz      | 10.96.201.210   | 8010  |
| agent      | 10.96.201.208   | 8011  |

### Required Secrets

Tests require these secrets from `vault.yml`:
- `secrets.postgresql.password` - Database password
- `secrets.authz.admin_token` - Admin token for RBAC
- `secrets.authz.master_key` - Encryption master key
- `secrets.minio.access_key` / `secret_key` - Object storage

The `make test` and `make test-local` commands extract these automatically.

---

## Writing New Tests

### Rules

1. **No mocks** - Use real services
2. **No sys.modules manipulation** - Breaks other tests
3. **Async tests use fixtures** - Don't create your own event loops
4. **Clean up after yourself** - Delete test data
5. **Tests must be idempotent** - Running twice gives same result

### Template

```python
"""
Tests for {feature}.

Uses real {service} - no mocks.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from api.main import app


@pytest.fixture
async def client():
    """Create test client with real app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def test_data(client):
    """Create test data, clean up after."""
    # Create
    response = await client.post("/items", json={"name": "test"})
    item_id = response.json()["id"]
    
    yield {"id": item_id}
    
    # Cleanup
    await client.delete(f"/items/{item_id}")


async def test_get_item(client, test_data):
    """Test getting an item."""
    response = await client.get(f"/items/{test_data['id']}")
    assert response.status_code == 200
    assert response.json()["name"] == "test"
```

---

## Related Documentation

- [Python Test Import Gotchas](../development/reference/python-test-import-gotchas.md) - Module import debugging
- [2025-12-21 Redis Import Error](../development/session-notes/2025-12-21-ingest-test-redis-import-error.md) - Case study
- [Ingest Test Runner Reference](../development/reference/ingest-test-runner.md) - Service-specific testing
- [Bootstrap Test Credentials](../guides/auth-api/bootstrap-test-credentials.md) - Setting up test auth

