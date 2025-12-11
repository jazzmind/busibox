# Testing Guide: Agent Server

## Local Testing (No External Dependencies)

The test suite is designed to run **completely standalone** with no external service dependencies.

### Quick Start

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/srv/agent

# 1. Install dependencies (if not already done)
pip install -e ".[dev]"

# 2. Run all tests
pytest tests/ -v

# 3. Run with coverage
pytest tests/ --cov=app --cov-report=html --cov-report=term

# 4. Run specific test file
pytest tests/unit/test_run_service.py -v

# 5. Run only integration tests
pytest tests/integration/ -v
```

### Environment Variables for Tests

**✅ NO environment variables required for tests!**

Tests use:
- **In-memory SQLite** database (`sqlite+aiosqlite:///:memory:`)
- **Mock HTTP clients** for Busibox services (search/ingest/RAG)
- **Mock JWT validation** (no real auth service needed)
- **Mock token exchange** (no real OAuth server needed)

### Test Structure

```
tests/
├── conftest.py              # Shared fixtures (DB, mocks, test data)
├── test_health.py           # Smoke test (FastAPI boots)
├── unit/
│   └── test_run_service.py  # Run service logic tests
└── integration/
    └── test_api_runs.py     # API endpoint tests
```

### Test Fixtures (from conftest.py)

- `test_engine` - In-memory SQLite database engine
- `test_session` - Async database session
- `test_client` - FastAPI test HTTP client
- `mock_principal` - Mock authenticated user
- `admin_principal` - Mock admin user
- `test_agent` - Sample agent definition
- `test_run` - Sample run record
- `test_token` - Sample token grant

### Running Tests with Different Verbosity

```bash
# Minimal output
pytest tests/

# Verbose (show test names)
pytest tests/ -v

# Very verbose (show test details)
pytest tests/ -vv

# Show print statements
pytest tests/ -s

# Stop on first failure
pytest tests/ -x

# Run only failed tests from last run
pytest tests/ --lf
```

### Coverage Reports

```bash
# Generate HTML coverage report
pytest tests/ --cov=app --cov-report=html

# Open report in browser
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux

# Terminal coverage report
pytest tests/ --cov=app --cov-report=term-missing
```

### Test Categories

#### Unit Tests (`tests/unit/`)

Test individual functions/classes in isolation with mocks:

```bash
pytest tests/unit/ -v
```

**What's tested**:
- Timeout calculation (`get_agent_timeout`)
- Run creation with success/failure/timeout scenarios
- Agent registry operations
- Token service logic
- Error handling paths

#### Integration Tests (`tests/integration/`)

Test API endpoints with real FastAPI app:

```bash
pytest tests/integration/ -v
```

**What's tested**:
- POST `/runs` - Create agent run
- GET `/runs/{id}` - Retrieve run details
- Error responses (404, 401)
- Request/response schemas

### Debugging Tests

```bash
# Run with Python debugger
pytest tests/ --pdb

# Drop into debugger on failure
pytest tests/ --pdb --maxfail=1

# Show local variables on failure
pytest tests/ -l

# Increase log output
pytest tests/ --log-cli-level=DEBUG
```

### Writing New Tests

**Unit Test Template**:

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_my_feature(test_session, mock_principal):
    """Test description."""
    # Arrange
    with patch("app.services.my_service.external_call") as mock_call:
        mock_call.return_value = {"result": "success"}
        
        # Act
        result = await my_function(test_session, mock_principal)
        
        # Assert
        assert result.status == "success"
        mock_call.assert_called_once()
```

**Integration Test Template**:

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_my_endpoint(test_client: AsyncClient):
    """Test endpoint description."""
    # Arrange
    with patch("app.api.my_route.get_principal") as mock_auth:
        mock_auth.return_value = Principal(sub="test-user", roles=["user"])
        
        # Act
        response = await test_client.post(
            "/my-endpoint",
            json={"data": "value"},
            headers={"Authorization": "Bearer test-token"}
        )
        
        # Assert
        assert response.status_code == 200
        assert response.json()["status"] == "success"
```

## Integration Testing with Busibox Services

For testing with real Busibox services (search/ingest/RAG), you'll need a running Busibox environment.

### Prerequisites

1. **Busibox services running** (test or production environment)
2. **Environment variables set** (see `.env.example`)
3. **Valid OAuth credentials** for token exchange

### Setup for Integration Testing

```bash
# 1. Copy .env.example to .env
cp .env.example .env

# 2. Update .env with real service URLs and credentials
# Edit .env with your Busibox configuration

# 3. Run integration tests (skip mocked tests)
pytest tests/integration/ -v -m "not mock"
```

### Environment Variables for Integration Tests

```bash
# Required for real Busibox integration
export DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/db"
export SEARCH_API_URL="http://10.96.200.30:8003"
export INGEST_API_URL="http://10.96.200.31:8001"
export RAG_API_URL="http://10.96.200.32:8004"
export AUTH_TOKEN_URL="http://10.96.200.33:8080/oauth/token"
export AUTH_CLIENT_ID="agent-server-client"
export AUTH_CLIENT_SECRET="your-secret"
export REDIS_URL="redis://10.96.200.34:6379/0"
```

## CI/CD Testing

Tests are designed to run in CI/CD pipelines with no external dependencies:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    pip install -e ".[dev]"
    pytest tests/ --cov=app --cov-report=xml

- name: Upload coverage
  uses: codecov/codecov-action@v3
```

## Troubleshooting

### Import Errors

```bash
# Ensure package is installed in editable mode
pip install -e ".[dev]"

# Or install dependencies directly
pip install pytest pytest-asyncio httpx
```

### Database Errors

```bash
# Tests use in-memory SQLite - no setup needed
# If you see SQLite errors, check that aiosqlite is installed
pip install aiosqlite
```

### Async Errors

```bash
# Ensure pytest-asyncio is installed
pip install pytest-asyncio

# Check pyproject.toml has:
# [tool.pytest.ini_options]
# asyncio_mode = "auto"
```

### Mock Errors

```bash
# If mocks aren't working, check patch paths
# Use full module path: "app.services.run_service.agent_registry"
# Not relative: "agent_registry"
```

## Test Coverage Goals

- **Overall**: 90%+ coverage
- **Critical paths**: 100% coverage (run service, auth, API endpoints)
- **Error handling**: All error paths tested
- **Edge cases**: Timeouts, failures, invalid input

### Current Coverage

```bash
# Check current coverage
pytest tests/ --cov=app --cov-report=term-missing

# Expected output:
# app/services/run_service.py    95%
# app/api/runs.py                 90%
# app/auth/tokens.py              85%
# ... (more files)
# TOTAL                           90%
```

## Next Steps

1. ✅ Run tests locally to verify setup
2. ✅ Add more unit tests for uncovered modules
3. ✅ Add e2e tests for complete user journeys
4. ✅ Set up CI/CD pipeline with automated testing
5. ✅ Add integration tests with real Busibox services


