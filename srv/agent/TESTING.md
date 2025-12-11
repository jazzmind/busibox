# Agent Server Testing Guide

## Overview

The agent server has comprehensive test coverage with unit, integration, and e2e tests. Tests can be run locally or on deployed infrastructure.

## Quick Start

### Local Testing

```bash
# Setup virtual environment (first time only)
cd /path/to/busibox/srv/agent
bash scripts/setup-venv.sh
source venv/bin/activate

# Run all tests
make test

# Run specific test suites
make test-unit           # Fast, isolated unit tests
make test-integration    # Integration tests with DB
make test-cov            # Tests with coverage report
```

### Deployed Testing (via MCP)

```bash
# From busibox/provision/ansible directory

# Test environment
make test-agent INV=inventory/test
make test-agent-unit INV=inventory/test
make test-agent-integration INV=inventory/test
make test-agent-coverage INV=inventory/test

# Production environment
make test-agent
make test-agent-unit
make test-agent-integration
make test-agent-coverage

# Interactive test menu
make test-menu
```

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures (DB, auth, agents)
├── test_health.py           # Smoke test
├── unit/                    # Fast, isolated tests
│   ├── test_auth_tokens.py  # JWT validation, claims
│   ├── test_token_service.py # Token caching/exchange
│   ├── test_busibox_client.py # HTTP client
│   └── test_run_service.py  # Run execution logic
└── integration/             # Tests with real DB
    ├── test_api_runs.py     # Runs API endpoints
    └── test_weather_agent.py # LiteLLM integration
```

## Test Categories

### Unit Tests (`tests/unit/`)

**Purpose**: Fast, isolated tests with mocked dependencies

**Coverage**:
- JWT validation (exp/nbf/iat, issuer/audience, signature)
- Token caching and refresh logic
- HTTP client request formatting
- Run service execution flow
- Agent timeout handling

**Run**:
```bash
pytest tests/unit/ -v
# or
make test-unit
```

### Integration Tests (`tests/integration/`)

**Purpose**: Test API endpoints with real database

**Coverage**:
- Runs API (POST /runs, GET /runs/{id})
- SSE streaming (/streams/runs/{id})
- Agent execution with mocked Busibox services
- Weather agent with LiteLLM (requires LiteLLM running)

**Run**:
```bash
pytest tests/integration/ -v
# or
make test-integration
```

### E2E Tests (future)

**Purpose**: Full stack tests with all services

**Coverage** (planned):
- Agent execution with real search/ingest/RAG calls
- Scheduled runs
- Workflow execution

## Test Fixtures

### Database Fixtures

```python
@pytest.fixture
async def test_session(test_engine) -> AsyncSession:
    """In-memory SQLite session for fast tests"""

@pytest.fixture
async def test_agent(test_session) -> AgentDefinition:
    """Pre-created agent definition"""

@pytest.fixture
async def test_run(test_session, test_agent) -> RunRecord:
    """Pre-created run record"""
```

### Auth Fixtures

```python
@pytest.fixture
def mock_principal() -> Principal:
    """Mock authenticated user"""

@pytest.fixture
def admin_principal() -> Principal:
    """Mock admin user"""

@pytest.fixture
def mock_jwt_token() -> str:
    """Mock JWT token string"""
```

## Writing Tests

### Unit Test Example

```python
@pytest.mark.asyncio
async def test_token_cache_hit(test_session, test_token):
    """Test that cached tokens are returned"""
    principal = Principal(sub="user-123", ...)
    
    token = await get_or_exchange_token(
        session=test_session,
        principal=principal,
        scopes=["search.read"],
        purpose="test",
    )
    
    assert token.access_token == test_token.token
```

### Integration Test Example

```python
@pytest.mark.asyncio
async def test_create_run_endpoint(test_client, test_agent):
    """Test POST /runs endpoint"""
    with patch("app.api.runs.get_principal") as mock_auth:
        mock_auth.return_value = Principal(sub="test-user", ...)
        
        response = await test_client.post(
            "/runs",
            json={"agent_id": str(test_agent.id), "input": {"prompt": "test"}},
            headers={"Authorization": "Bearer test-token"}
        )
        
        assert response.status_code == 202
        assert response.json()["status"] in ["running", "succeeded"]
```

## Coverage Requirements

- **Overall**: 90%+ (FR-033, SC-005)
- **Auth/Token**: 100% (security-critical)
- **Agent Execution**: 80%+ (complex logic)

**Generate coverage report**:
```bash
pytest tests/ --cov=app --cov-report=html --cov-report=term
# View report: open htmlcov/index.html
```

## Continuous Integration

Tests run automatically:
1. **Pre-deployment**: Unit tests must pass before deploying
2. **Post-deployment**: Integration tests verify deployment
3. **Scheduled**: Nightly e2e tests on production

## Troubleshooting

### Tests fail with "pytest not found"

```bash
# Install test dependencies
pip install -r requirements.test.txt
# or
make install-dev
```

### Tests fail with database errors

```bash
# Tests use in-memory SQLite by default
# Check conftest.py for TEST_DATABASE_URL

# For PostgreSQL tests (integration):
createdb agent_server_test
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agent_server_test pytest
```

### Tests fail with auth errors

```bash
# Unit tests mock auth - check fixtures in conftest.py
# Integration tests require valid JWT - check test setup

# For deployed tests, ensure auth service is running:
curl http://authz-lxc:8080/.well-known/jwks.json
```

### Tests timeout

```bash
# Increase timeout for slow tests
pytest tests/ -v --timeout=60

# Or skip slow tests
pytest tests/unit/ -v  # Only fast unit tests
```

## Best Practices

1. **Test Isolation**: Each test should be independent
2. **Mock External Services**: Use mocks for Busibox APIs in unit tests
3. **Use Fixtures**: Reuse common setup via pytest fixtures
4. **Clear Names**: Test names should describe what they test
5. **Fast Feedback**: Keep unit tests fast (<100ms each)
6. **Coverage**: Aim for high coverage, but focus on critical paths

## Related Documentation

- **Quickstart**: `quickstart.md` - Setup and first test
- **Research**: `research.md` - Testing strategy and patterns
- **Tasks**: `tasks.md` - Test implementation tasks
- **Busibox Testing**: `/provision/ansible/TESTING.md` - Infrastructure tests
