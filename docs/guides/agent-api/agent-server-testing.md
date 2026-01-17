---
title: Agent Server Testing Guide
category: guides
created: 2025-12-12
updated: 2025-12-12
status: active
tags: [agent-server, testing, pytest, integration-tests]
---

# Agent Server Testing Guide

## Overview

The agent server has comprehensive test coverage with unit, integration, and e2e tests. Tests can be run locally during development or on deployed infrastructure.

## Quick Start

### Local Testing

```bash
# Setup virtual environment (first time only)
cd /Users/wessonnenreich/Code/sonnenreich/busibox/srv/agent
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
│   ├── test_agents_core.py  # Agent validation
│   ├── test_run_service.py  # Run execution logic
│   ├── test_dispatcher.py   # Dispatcher schema validation
│   ├── test_dynamic_loader.py # Dynamic agent loading
│   ├── test_scheduler.py    # Scheduled runs
│   ├── test_workflow_engine.py # Workflow execution
│   └── test_scorer_service.py # Performance evaluation
└── integration/             # Tests with real DB
    ├── test_api_runs.py     # Runs API endpoints
    ├── test_api_streams.py  # SSE streaming
    ├── test_api_agents.py   # Agent CRUD
    ├── test_api_schedule.py # Scheduling API
    ├── test_api_workflows.py # Workflow API
    ├── test_api_scores.py   # Scoring API
    ├── test_personal_agents.py # Personal agent filtering
    ├── test_dispatcher_routing.py # Query routing
    ├── test_tool_crud.py    # Tool CRUD operations
    ├── test_workflow_crud.py # Workflow CRUD
    └── test_evaluator_crud.py # Evaluator CRUD
```

## Test Categories

### Unit Tests

**Purpose**: Fast, isolated tests with mocked dependencies

**Coverage**:
- JWT validation (exp/nbf/iat, issuer/audience, signature)
- Token caching and refresh logic
- HTTP client request formatting
- Run service execution flow
- Agent timeout handling
- Dispatcher schema validation
- Dynamic agent loading
- Scheduled run management
- Workflow execution engine
- Performance scoring

**Run**:
```bash
pytest tests/unit/ -v
# or
make test-unit
```

**Test Count**: 117 unit tests

### Integration Tests

**Purpose**: Test API endpoints with real database

**Coverage**:
- Runs API (POST /runs, GET /runs/{id})
- SSE streaming (/streams/runs/{id})
- Agent execution with mocked Busibox services
- Personal agent filtering and authorization
- Dispatcher query routing
- Tool/workflow/evaluator CRUD operations
- Schedule management
- Workflow execution
- Performance scoring

**Run**:
```bash
pytest tests/integration/ -v
# or
make test-integration
```

**Test Count**: 40+ integration tests

### E2E Tests (future)

**Purpose**: Full stack tests with all services

**Coverage** (planned):
- Agent execution with real search/ingest/RAG calls
- Scheduled runs with actual cron triggers
- Workflow execution with real tool calls
- End-to-end authentication flow

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

### Agent Fixtures

```python
@pytest.fixture
def test_agent_definition() -> AgentDefinition:
    """Test agent with tools"""

@pytest.fixture
def test_tool_definition() -> ToolDefinition:
    """Test tool definition"""

@pytest.fixture
def test_workflow_definition() -> WorkflowDefinition:
    """Test workflow with steps"""
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

## Test Results

### Current Status

**Final Test Results** (as of 2025-12-11):
- ✅ **62 tests PASSED** (94%)
- ⏭️ **4 tests SKIPPED** (6% - tracing span collection)
- ❌ **0 tests FAILED**
- ⚠️ **14 warnings** (deprecation warnings, not errors)
- ⏱️ **~6.5 minutes** execution time

### Coverage Requirements

- **Overall**: 90%+ (FR-033, SC-005)
- **Auth/Token**: 100% (security-critical)
- **Agent Execution**: 80%+ (complex logic)

**Generate coverage report**:
```bash
pytest tests/ --cov=app --cov-report=html --cov-report=term
# View report: open htmlcov/index.html
```

## Test Execution

### Run All Tests

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/srv/agent
source venv/bin/activate
pytest tests/ -v
```

### Run Specific Test File

```bash
pytest tests/unit/test_auth_tokens.py -v
pytest tests/integration/test_api_runs.py -v
```

### Run Specific Test Function

```bash
pytest tests/unit/test_auth_tokens.py::test_validate_bearer_success -v
```

### Run Tests with Coverage

```bash
pytest tests/ --cov=app --cov-report=html --cov-report=term
```

### Run Tests in Parallel

```bash
pytest tests/ -n auto  # Uses all CPU cores
```

### Run Tests with Verbose Output

```bash
pytest tests/ -vv -s  # Very verbose, show print statements
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
createdb test_agent_server
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test_agent_server pytest
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

### Import errors

```bash
# Reinstall dependencies
cd /srv/agent
source .venv/bin/activate
pip install -e .

# Check Python version
python --version  # Must be 3.11+

# Check installed packages
pip list | grep -E "(fastapi|pydantic|sqlalchemy)"
```

## Best Practices

1. **Test Isolation**: Each test should be independent
2. **Mock External Services**: Use mocks for Busibox APIs in unit tests
3. **Use Fixtures**: Reuse common setup via pytest fixtures
4. **Clear Names**: Test names should describe what they test
5. **Fast Feedback**: Keep unit tests fast (<100ms each)
6. **Coverage**: Aim for high coverage, but focus on critical paths
7. **Async Tests**: Use `@pytest.mark.asyncio` for async functions
8. **Database Cleanup**: Use fixtures with proper teardown
9. **Error Testing**: Test both success and failure paths
10. **Documentation**: Add docstrings to complex tests

## Test Data

### Dispatcher Test Queries

The dispatcher uses a comprehensive test dataset at `tests/fixtures/dispatcher_queries.json`:

```json
[
  {
    "query": "What does our Q4 report say about revenue?",
    "expected_tools": ["doc_search"],
    "expected_confidence": 0.9
  },
  {
    "query": "What is the weather today?",
    "expected_tools": ["web_search"],
    "expected_confidence": 0.9
  }
]
```

### Test Agents

Pre-configured test agents:
- `chat_agent`: General-purpose with all tools
- `rag_agent`: RAG-focused with search and RAG tools
- `search_agent`: Search-only agent

### Test Tools

Pre-configured test tools:
- `search_tool`: Semantic search
- `ingest_tool`: Document ingestion
- `rag_tool`: RAG queries

## Performance Benchmarks

### Expected Test Times

- **Unit tests**: ~6.5 minutes total (includes timeout tests with real sleep)
- **Integration tests**: ~2-3 minutes
- **Full suite**: ~10 minutes

### Optimization Tips

1. **Run unit tests first**: Fast feedback loop
2. **Use pytest-xdist**: Parallel execution
3. **Skip slow tests**: Use markers for slow tests
4. **Mock external calls**: Avoid network latency
5. **Use in-memory DB**: SQLite for unit tests

## Related Documentation

- **Deployment**: `docs/deployment/agent-server-deployment.md`
- **Architecture**: `docs/architecture/agent-server-architecture.md`
- **API Reference**: `docs/reference/agent-server-api.md`
- **Troubleshooting**: `docs/troubleshooting/agent-server-issues.md`









