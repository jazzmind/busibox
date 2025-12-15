# Busibox Agent Server

Production-grade AI agent server built with FastAPI and Pydantic AI, providing dynamic agent management, tool orchestration, workflow execution, and scheduled runs with comprehensive observability.

## Features

### Core Capabilities
- **Dynamic Agent Management**: Create, update, and manage AI agents without code changes
- **Tool Orchestration**: Search, ingest, and RAG tools with Busibox service integration
- **Workflow Execution**: Multi-step workflows with sequential processing and output chaining
- **Scheduled Runs**: Cron-based scheduling with automatic token refresh
- **Performance Evaluation**: Scorer system for latency, success rate, and tool usage metrics
- **Real-time Streaming**: Server-Sent Events (SSE) for run status updates
- **Tiered Execution**: Simple (30s/512MB), Complex (5min/2GB), Batch (30min/4GB) limits

### Security & Auth
- **JWT Validation**: Validates Busibox JWTs via JWKS
- **Token Exchange**: OAuth2 token exchange with caching
- **Token Forwarding**: Automatic token forwarding to downstream services
- **Role-Based Access**: Admin and user role enforcement

### Observability
- **Structured Logging**: JSON-formatted logs with trace context
- **OpenTelemetry**: Distributed tracing for all operations
- **Health Checks**: `/health` endpoint for monitoring
- **Event Tracking**: Comprehensive event logging for run lifecycle

## Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL 15+
- Redis (for scheduler)
- Busibox services (search, ingest, RAG)

### Installation

```bash
# Clone repository
cd /srv/agent

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your configuration
```

### Configuration

Key environment variables (see `.env.example`):

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/agent_server

# Authentication
AUTH_CLIENT_ID=agent-server
AUTH_CLIENT_SECRET=your-secret
AUTH_JWKS_URL=http://authz:8080/jwks
AUTH_TOKEN_URL=http://authz:8080/token
AUTH_ISSUER=https://busibox.local
AUTH_AUDIENCE=busibox-api

# Busibox Services
SEARCH_API_URL=http://search-api:8001
INGEST_API_URL=http://ingest-api:8002
RAG_API_URL=http://rag-api:8003

# LiteLLM
LITELLM_BASE_URL=http://localhost:4000

# OpenTelemetry (optional)
OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=agent-server

# Redis (for scheduler)
REDIS_URL=redis://localhost:6379/0
```

### Running Locally

```bash
# Activate virtual environment
source .venv/bin/activate

# Run development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Or use make
make dev
```

### Running Tests

```bash
# All tests
make test

# Unit tests only
make test-unit

# Integration tests only
make test-integration

# With coverage
make test-coverage
```

## Architecture

### Project Structure

```
srv/agent/
├── app/
│   ├── main.py                 # FastAPI application
│   ├── config/
│   │   └── settings.py         # Environment configuration
│   ├── db/
│   │   ├── session.py          # SQLAlchemy async session
│   │   └── schema.sql          # Database schema DDL
│   ├── models/
│   │   ├── base.py             # SQLAlchemy Base
│   │   └── domain.py           # ORM models
│   ├── schemas/
│   │   ├── auth.py             # Auth schemas
│   │   ├── definitions.py      # Agent/Tool/Workflow schemas
│   │   └── run.py              # Run and schedule schemas
│   ├── auth/
│   │   ├── tokens.py           # JWT validation
│   │   └── dependencies.py     # FastAPI dependencies
│   ├── clients/
│   │   └── busibox.py          # HTTP client for Busibox services
│   ├── agents/
│   │   ├── core.py             # Core Pydantic AI agents
│   │   └── dynamic_loader.py   # Dynamic agent loading
│   ├── workflows/
│   │   ├── baseline.py         # Baseline workflows
│   │   └── engine.py           # Workflow execution engine
│   ├── services/
│   │   ├── agent_registry.py   # In-memory agent registry
│   │   ├── run_service.py      # Run execution service
│   │   ├── scheduler.py        # APScheduler service
│   │   ├── token_service.py    # Token caching/exchange
│   │   └── scorer_service.py   # Performance evaluation
│   ├── api/
│   │   ├── health.py           # Health check
│   │   ├── auth.py             # Token exchange endpoint
│   │   ├── agents.py           # Agent/Tool/Workflow CRUD
│   │   ├── runs.py             # Run execution and scheduling
│   │   ├── streams.py          # SSE streaming
│   │   └── scores.py           # Scoring and evaluation
│   └── utils/
│       └── logging.py          # Structured logging + OTel
├── tests/
│   ├── conftest.py             # Pytest fixtures
│   ├── unit/                   # Unit tests (90+ tests)
│   └── integration/            # Integration tests (40+ tests)
├── pyproject.toml              # Dependencies
├── requirements.txt            # Production dependencies
├── requirements.test.txt       # Test dependencies
├── Makefile                    # Common commands
└── README.md                   # This file
```

### Technology Stack

- **Framework**: FastAPI 0.115+ (async/await)
- **AI**: Pydantic AI 0.0.20+ (agent framework)
- **Database**: PostgreSQL 15+ with SQLAlchemy 2.0 (async)
- **Scheduler**: APScheduler 3.10+ (cron jobs)
- **Observability**: OpenTelemetry SDK 1.27+
- **Testing**: pytest 8.3+ with pytest-asyncio

## API Documentation

### Agent Management

#### Create Agent
```bash
POST /agents/definitions
{
  "name": "my-agent",
  "model": "anthropic:claude-3-5-sonnet",
  "instructions": "You are a helpful assistant",
  "tools": {"names": ["search", "rag"]},
  "scopes": ["agent.execute", "search.read"]
}
```

#### List Agents
```bash
GET /agents
```

### Run Execution

#### Execute Agent
```bash
POST /runs
{
  "agent_id": "uuid",
  "input": {"prompt": "What is the weather?"},
  "agent_tier": "simple"
}
```

#### Get Run Status
```bash
GET /runs/{run_id}
```

#### Stream Run Updates (SSE)
```bash
GET /streams/runs/{run_id}
```

### Scheduling

#### Schedule Cron Run
```bash
POST /runs/schedule
{
  "agent_id": "uuid",
  "input": {"prompt": "Daily summary"},
  "cron": "0 9 * * *",
  "agent_tier": "complex"
}
```

#### List Schedules
```bash
GET /runs/schedule
```

#### Cancel Schedule
```bash
DELETE /runs/schedule/{job_id}
```

### Workflows

#### Create Workflow
```bash
POST /agents/workflows
{
  "name": "ingest-and-analyze",
  "steps": [
    {
      "id": "ingest",
      "type": "tool",
      "tool": "ingest",
      "args": {"path": "$.input.path"}
    },
    {
      "id": "analyze",
      "type": "agent",
      "agent": "analyzer",
      "input": "$.ingest.document_id"
    }
  ]
}
```

#### Execute Workflow
```bash
POST /runs/workflow?workflow_id=uuid
{
  "path": "/documents/report.pdf"
}
```

### Scoring

#### Execute Scorer
```bash
POST /scores/execute
{
  "scorer_id": "uuid",
  "run_id": "uuid"
}
```

#### Get Aggregates
```bash
GET /scores/aggregates?agent_id=uuid
```

## Development

### Adding a New Tool

1. Define tool function in `app/agents/core.py`:
```python
@tool
async def my_tool(ctx: RunContext[BusiboxDeps], arg1: str) -> dict:
    """Tool description."""
    # Implementation
    return {"result": "data"}
```

2. Register in `app/agents/dynamic_loader.py`:
```python
TOOL_REGISTRY = {
    "search": search_tool,
    "ingest": ingest_tool,
    "rag": rag_tool,
    "my_tool": my_tool,  # Add here
}
```

3. Add tests in `tests/unit/test_agents_core.py`

### Adding a New Scorer Type

1. Implement scorer function in `app/services/scorer_service.py`:
```python
def score_my_metric(run_record: RunRecord) -> ScorerResult:
    # Calculate score
    return ScorerResult(...)
```

2. Add to `execute_scorer()` type dispatch

3. Add tests in `tests/unit/test_scorer_service.py`

## Testing

### Test Structure

- **Unit Tests** (`tests/unit/`): Test individual components in isolation
- **Integration Tests** (`tests/integration/`): Test API endpoints with database
- **Coverage Target**: 90%+

### Running Tests

```bash
# All tests
pytest tests/

# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# Specific test file
pytest tests/unit/test_run_service.py

# With coverage
pytest tests/ --cov=app --cov-report=html

# Verbose output
pytest tests/ -v

# Stop on first failure
pytest tests/ -x
```

### Test Database

Tests use SQLite in-memory database by default. To use PostgreSQL:

```bash
export TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/agent_test
pytest tests/
```

## Deployment

### Via Ansible (Recommended)

From the Busibox repository:

```bash
cd provision/ansible

# Deploy to test environment
make agent INV=inventory/test

# Deploy to production
make agent
```

### Manual Deployment

On the agent-lxc container:

```bash
cd /srv/agent

# Pull latest code
git pull origin main

# Install dependencies
source .venv/bin/activate
pip install -r requirements.txt

# Run migrations (if any)
alembic upgrade head

# Restart service
systemctl restart agent-api
```

### Health Check

```bash
curl http://localhost:8000/health

# Expected response:
{
  "status": "healthy",
  "service": "agent-server",
  "timestamp": "2025-12-11T20:00:00Z"
}
```

## Monitoring

### Logs

```bash
# View logs
journalctl -u agent-api -f

# Filter by level
journalctl -u agent-api -p err

# View recent logs
journalctl -u agent-api -n 100 --no-pager
```

### Metrics

OpenTelemetry traces are exported to configured OTLP endpoint. Key spans:
- `agent_run`: Full agent execution
- `token_exchange`: Token exchange operations
- `workflow_execution`: Workflow step execution

### Database Queries

```sql
-- Recent runs
SELECT id, agent_id, status, created_at, updated_at
FROM run_records
ORDER BY created_at DESC
LIMIT 10;

-- Success rate by agent
SELECT agent_id, 
       COUNT(*) as total,
       SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) as succeeded
FROM run_records
GROUP BY agent_id;

-- Active agents
SELECT id, name, model, is_active
FROM agent_definitions
WHERE is_active = true;
```

## Troubleshooting

### Agent Not Found
- Check agent is active: `SELECT * FROM agent_definitions WHERE id = 'uuid';`
- Refresh registry: Restart service or call refresh endpoint

### Token Exchange Fails
- Verify auth service is reachable
- Check `AUTH_CLIENT_ID` and `AUTH_CLIENT_SECRET`
- Verify scopes are valid

### Timeout Issues
- Check agent tier matches workload (simple/complex/batch)
- Review logs for execution time
- Consider increasing tier limits if needed

### Tool Call Failures
- Verify Busibox services are running
- Check token has required scopes
- Review tool validation in logs

## Performance

### Execution Limits

| Tier | Timeout | Memory | Use Case |
|------|---------|--------|----------|
| Simple | 30s | 512MB | Quick queries, simple tasks |
| Complex | 5min | 2GB | Multi-step reasoning, complex analysis |
| Batch | 30min | 4GB | Long-running workflows, bulk processing |

### Optimization Tips

1. **Token Caching**: Tokens are cached and reused until near expiry
2. **Agent Registry**: Agents loaded once at startup, refreshed on demand
3. **Connection Pooling**: SQLAlchemy connection pool for database efficiency
4. **Async Operations**: Full async/await for concurrent execution

## Contributing

### Code Style

- Follow PEP 8 style guide
- Use type hints for all functions
- Add docstrings for public APIs
- Keep functions focused and testable

### Testing Requirements

- Add unit tests for new business logic
- Add integration tests for new API endpoints
- Maintain 90%+ test coverage
- All tests must pass before merging

### Commit Messages

Follow conventional commits:
- `feat(agent): add new feature`
- `fix(scheduler): fix bug`
- `docs(readme): update documentation`
- `test(scorer): add tests`

## License

Proprietary - Busibox Project

## Support

For issues or questions:
1. Check logs: `journalctl -u agent-api -f`
2. Review health endpoint: `curl http://localhost:8000/health`
3. Check database connectivity
4. Verify Busibox services are running
5. Review OpenTelemetry traces

## Related Documentation

- **Architecture**: `/docs/architecture/agent-server.md`
- **API Specification**: `/specs/005-i-want-to/contracts/openapi.yaml`
- **Data Model**: `/specs/005-i-want-to/data-model.md`
- **Quickstart**: `/specs/005-i-want-to/quickstart.md`
- **Testing Guide**: `TESTING.md`





