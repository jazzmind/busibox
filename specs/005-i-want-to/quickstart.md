# Quickstart: Production-Grade Agent Server

**Feature**: 005-i-want-to  
**Date**: 2025-01-08  
**Purpose**: Get the agent server running locally and execute your first agent

## Prerequisites

- Python 3.11+
- PostgreSQL 15+ running and accessible
- Redis (for scheduler, optional for initial testing)
- Busibox auth service running (for token validation)
- Busibox search/ingest/RAG services running (for tool calls)

## Local Development Setup

### 1. Install Dependencies

```bash
cd /path/to/busibox/srv/agent

# Install uv (fast Python package installer)
pip install uv

# Install dependencies from pyproject.toml
uv sync

# Or use pip
pip install -e .
```

### 2. Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your configuration
nano .env
```

**Required environment variables**:

```bash
# Database
DATABASE_URL=postgresql+asyncpg://agent_server:agent_server@localhost:5432/agent_server

# Busibox Auth
AUTH_CLIENT_ID=agent-server-client
AUTH_CLIENT_SECRET=your-client-secret
AUTH_JWKS_URL=http://authz-lxc:8080/.well-known/jwks.json
AUTH_TOKEN_URL=http://authz-lxc:8080/oauth/token
AUTH_ISSUER=https://busibox.local
AUTH_AUDIENCE=https://busibox.local/agent

# Busibox Services
SEARCH_API_URL=http://milvus-lxc:8003
INGEST_API_URL=http://ingest-lxc:8002
RAG_API_URL=http://milvus-lxc:8003

# Redis (for scheduler)
REDIS_URL=redis://ingest-lxc:6379/0

# Application
APP_NAME=agent-server
ENVIRONMENT=development
DEBUG=true
DEFAULT_MODEL=anthropic:claude-3-5-sonnet
```

### 3. Initialize Database

```bash
# Create database
createdb agent_server

# Apply schema
psql agent_server < app/db/schema.sql

# Verify tables created
psql agent_server -c "\dt"
```

**Expected output**:
```
             List of relations
 Schema |        Name         | Type  |  Owner
--------+---------------------+-------+----------
 public | agent_definitions   | table | postgres
 public | eval_definitions    | table | postgres
 public | rag_databases       | table | postgres
 public | rag_documents       | table | postgres
 public | run_records         | table | postgres
 public | token_grants        | table | postgres
 public | tool_definitions    | table | postgres
 public | workflow_definitions| table | postgres
```

### 4. Run Development Server

```bash
# Activate virtual environment (if using venv)
source venv/bin/activate

# Run with uvicorn
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Or use the shortcut
python -m uvicorn app.main:app --reload
```

**Expected output**:
```
INFO:     Will watch for changes in these directories: ['/path/to/srv/agent']
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [12345] using StatReload
INFO:     Started server process [12346]
INFO:     Waiting for application startup.
INFO:     Agent registry initialized
INFO:     Application startup complete.
```

### 5. Verify Health

```bash
curl http://localhost:8000/health
```

**Expected response**:
```json
{"status": "ok"}
```

## Quick Test: Execute an Agent

### 1. Get an Auth Token

```bash
# Obtain JWT token from Busibox auth service
# (Replace with your actual auth flow)
export TOKEN="your-jwt-token-here"
```

### 2. List Available Agents

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/agents
```

**Expected response**:
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "chat-agent",
    "display_name": "Chat Agent",
    "model": "anthropic:claude-3-5-sonnet",
    "instructions": "You are a Busibox assistant...",
    "tools": {"names": ["search", "ingest", "rag"]},
    "is_active": true,
    "version": 1,
    "created_at": "2025-01-08T12:00:00Z",
    "updated_at": "2025-01-08T12:00:00Z"
  }
]
```

### 3. Execute Agent Run

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "550e8400-e29b-41d4-a716-446655440000",
    "input": {
      "prompt": "Search for documents about AI"
    }
  }' \
  http://localhost:8000/runs
```

**Expected response**:
```json
{
  "id": "660e8400-e29b-41d4-a716-446655440001",
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_id": null,
  "status": "pending",
  "input": {
    "prompt": "Search for documents about AI"
  },
  "output": null,
  "events": [],
  "created_by": "user@example.com",
  "created_at": "2025-01-08T12:05:00Z",
  "updated_at": "2025-01-08T12:05:00Z"
}
```

### 4. Monitor Run Progress (SSE Stream)

```bash
# Stream run updates in real-time
curl -N -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/streams/runs/660e8400-e29b-41d4-a716-446655440001
```

**Expected output**:
```
event: status
data: running

event: status
data: succeeded

event: output
data: {"message": "Found 5 documents about AI", "tool_results": [...]}
```

### 5. Get Final Run Results

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/runs/660e8400-e29b-41d4-a716-446655440001
```

**Expected response**:
```json
{
  "id": "660e8400-e29b-41d4-a716-446655440001",
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "succeeded",
  "input": {
    "prompt": "Search for documents about AI"
  },
  "output": {
    "message": "Found 5 documents about AI",
    "tool_results": [
      {
        "tool": "search",
        "args": {"query": "AI", "top_k": 5},
        "result": {"hits": [...]}
      }
    ]
  },
  "events": [
    {
      "timestamp": "2025-01-08T12:05:01Z",
      "type": "tool_call",
      "data": {"tool": "search", "args": {...}}
    },
    {
      "timestamp": "2025-01-08T12:05:03Z",
      "type": "completion",
      "data": {"output": {...}}
    }
  ],
  "created_by": "user@example.com",
  "created_at": "2025-01-08T12:05:00Z",
  "updated_at": "2025-01-08T12:05:03Z"
}
```

## Create a Custom Agent

### 1. Define Agent

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "document-analyzer",
    "display_name": "Document Analyzer",
    "description": "Analyzes documents and extracts key information",
    "model": "anthropic:claude-3-5-sonnet",
    "instructions": "You are a document analyzer. Use search and RAG tools to find and analyze documents. Provide structured summaries.",
    "tools": {
      "names": ["search", "rag"]
    },
    "scopes": ["agent.execute", "search.read", "rag.query"],
    "is_active": true
  }' \
  http://localhost:8000/agents/definitions
```

### 2. Refresh Agent Registry

The agent registry auto-refreshes on startup, but you can manually refresh:

```bash
# Restart the server to load new agents
# Or implement a refresh endpoint (future enhancement)
```

### 3. Execute Custom Agent

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "<new-agent-id>",
    "input": {
      "prompt": "Analyze the latest quarterly report"
    }
  }' \
  http://localhost:8000/runs
```

## Running Tests

### Unit Tests

```bash
# Run all unit tests
pytest tests/unit/ -v

# Run with coverage
pytest tests/unit/ --cov=app --cov-report=html

# Run specific test file
pytest tests/unit/test_auth.py -v
```

### Integration Tests

```bash
# Requires test database
createdb agent_server_test

# Run integration tests
pytest tests/integration/ -v

# Run with test database URL
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agent_server_test \
  pytest tests/integration/ -v
```

### E2E Tests

```bash
# Requires all services running (search, ingest, RAG)
pytest tests/e2e/ -v

# Run with full logging
pytest tests/e2e/ -v -s --log-cli-level=INFO
```

## Deployment to Production

### 1. Build and Deploy

```bash
# From Busibox Ansible directory
cd /path/to/busibox/provision/ansible

# Deploy to production
make agent

# Or deploy to test environment
make agent INV=inventory/test
```

### 2. Verify Deployment

```bash
# Check service status
ssh root@agent-lxc systemctl status agent-server

# Check logs
ssh root@agent-lxc journalctl -u agent-server -n 50 --no-pager

# Test health endpoint
curl http://agent-lxc:8000/health
```

### 3. Run Production Tests

```bash
# From Busibox Ansible directory
make test-agent

# Or with specific inventory
make test-agent INV=inventory/test
```

## Troubleshooting

### Database Connection Issues

```bash
# Test connection
psql $DATABASE_URL -c "SELECT 1;"

# Check if tables exist
psql $DATABASE_URL -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public';"
```

### Auth Token Issues

```bash
# Verify JWKS endpoint is accessible
curl http://authz-lxc:8080/.well-known/jwks.json

# Check token validity
# (Decode JWT at jwt.io or use jwt-cli)
```

### Agent Execution Failures

```bash
# Check logs
tail -f /var/log/agent-server/app.log

# Or with journalctl
journalctl -u agent-server -f

# Check run events for errors
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/runs/<run-id> | jq '.events'
```

### Tool Call Failures

```bash
# Verify Busibox services are running
curl http://milvus-lxc:8003/health
curl http://ingest-lxc:8002/health

# Check token exchange
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "scopes": ["search.read"],
    "purpose": "test"
  }' \
  http://localhost:8000/auth/exchange
```

## Next Steps

- **Read the API docs**: Open `contracts/openapi.yaml` in Swagger Editor
- **Explore workflows**: See `data-model.md` for workflow definitions
- **Add custom tools**: Register new tools in `app/agents/dynamic_loader.py`
- **Set up monitoring**: Configure OpenTelemetry exporter in `.env`
- **Schedule jobs**: Use `/runs/schedule` endpoint for cron tasks

## Additional Resources

- **Specification**: `spec.md` - Feature requirements and success criteria
- **Research**: `research.md` - Technology decisions and best practices
- **Data Model**: `data-model.md` - Database schema and entities
- **API Contracts**: `contracts/openapi.yaml` - Full API specification
- **Implementation Plan**: `plan.md` - Technical architecture and structure










