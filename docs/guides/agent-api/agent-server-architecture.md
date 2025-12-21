---
title: Agent Server Architecture
category: architecture
created: 2025-12-12
updated: 2025-12-12
status: active
tags: [agent-server, architecture, pydantic-ai, fastapi]
---

# Agent Server Architecture

## Overview

The agent server is a Python/FastAPI application that provides AI agent execution, tool orchestration, workflow management, and intelligent query routing. It uses Pydantic AI for agent management and integrates with LiteLLM for LLM access.

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Agent Client (Next.js)                  │
│                    Browser / React UI                        │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP/REST + SSE
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Agent Server (FastAPI)                    │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              API Layer (FastAPI)                      │  │
│  │  /runs, /agents, /dispatcher, /workflows, /scores    │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Service Layer                            │  │
│  │  RunService, AgentRegistry, DispatcherService,       │  │
│  │  TokenService, Scheduler, ScorerService              │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Agent Layer (Pydantic AI)                │  │
│  │  chat_agent, rag_agent, search_agent, dispatcher     │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Tool Layer                               │  │
│  │  search_tool, ingest_tool, rag_tool, weather_tool    │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  PostgreSQL │  │   LiteLLM   │  │    Redis    │
│  (pg-lxc)   │  │(litellm-lxc)│  │(ingest-lxc) │
└─────────────┘  └─────────────┘  └─────────────┘
                         │
                         ▼
                 ┌─────────────┐
                 │ Local LLMs  │
                 │ (qwen3-30b) │
                 └─────────────┘
```

## Component Architecture

### API Layer

**Location**: `app/api/`

**Endpoints**:
- `runs.py`: Agent execution (`POST /runs`, `GET /runs/{id}`, `GET /runs`)
- `streams.py`: SSE streaming (`GET /streams/runs/{id}`)
- `agents.py`: Agent management (`GET /agents`, `POST /agents/definitions`)
- `dispatcher.py`: Query routing (`POST /dispatcher/route`)
- `tools.py`: Tool CRUD (`GET/POST/PUT/DELETE /agents/tools`)
- `workflows.py`: Workflow CRUD (`GET/POST/PUT/DELETE /agents/workflows`)
- `evals.py`: Evaluator CRUD (`GET/POST/PUT/DELETE /agents/evals`)
- `schedule.py`: Schedule management (`GET/POST/DELETE /runs/schedule`)
- `scores.py`: Performance evaluation (`POST /scores/execute`, `GET /scores/aggregates`)

**Responsibilities**:
- Request validation (Pydantic schemas)
- Authentication (JWT bearer tokens)
- Authorization (role-based, ownership-based)
- Response formatting
- Error handling

### Service Layer

**Location**: `app/services/`

**Services**:

1. **RunService** (`run_service.py`):
   - Agent execution orchestration
   - Token exchange for downstream services
   - Tiered execution limits (timeout, memory)
   - Event tracking and persistence
   - Status management

2. **AgentRegistry** (`agent_registry.py`):
   - In-memory agent cache
   - Dynamic agent loading from database
   - Thread-safe refresh mechanism
   - Agent lookup by ID/name

3. **DispatcherService** (`dispatcher_service.py`):
   - Query analysis and routing
   - Tool/agent selection
   - Confidence scoring
   - Decision logging
   - Redis caching (optional)

4. **TokenService** (`token_service.py`):
   - OAuth2 token exchange
   - Token caching with expiry
   - Proactive refresh (60s buffer)
   - Scope normalization

5. **Scheduler** (`scheduler.py`):
   - APScheduler integration
   - Cron-based scheduling
   - Token pre-refresh
   - Job metadata tracking

6. **ScorerService** (`scorer_service.py`):
   - Performance evaluation
   - Multiple scorer types (latency, success, tool usage)
   - Score aggregation
   - Statistical analysis

7. **DynamicLoader** (`dynamic_loader.py`):
   - Load agents from database
   - Tool registration
   - Validation
   - Error handling

8. **VersionIsolation** (`version_isolation.py`):
   - Snapshot capture at run start
   - Definition preservation
   - Resume support

### Agent Layer

**Location**: `app/agents/`

**Agents** (Pydantic AI):

1. **chat_agent** (`chat_agent.py`):
   - General-purpose conversational agent
   - All tools available
   - Model: `agent` purpose (qwen3-30b)

2. **rag_agent** (`rag_agent.py`):
   - RAG-focused agent
   - Search and RAG tools
   - Model: `research` purpose (qwen3-30b)

3. **search_agent** (`search_agent.py`):
   - Search-only agent
   - Search tool only
   - Model: `agent` purpose

4. **dispatcher** (`dispatcher.py`):
   - Query routing agent
   - No tools (pure reasoning)
   - Model: `chat` purpose (Claude 3.5 Sonnet)

5. **weather_agent** (`weather_agent.py`):
   - Weather query agent
   - Weather tool
   - Model: `research` purpose

**Dynamic Agents**:
- Loaded from `agent_definitions` table
- Tools registered from `tool_definitions`
- Workflows from `workflow_definitions`

### Tool Layer

**Location**: `app/tools/`

**Tools** (Pydantic AI):

1. **search_tool** (`search_tool.py`):
   - Semantic search via Busibox Search API
   - Input: query, top_k, filters
   - Output: documents with scores

2. **ingest_tool** (`ingest_tool.py`):
   - Document ingestion via Busibox Ingest API
   - Input: file_path, metadata
   - Output: document_id, status

3. **rag_tool** (`rag_tool.py`):
   - RAG queries via Busibox RAG API
   - Input: query, top_k, database_id
   - Output: answer, sources, confidence

4. **weather_tool** (`weather_tool.py`):
   - Weather data via Open-Meteo API
   - Input: location (city name or coordinates)
   - Output: temperature, humidity, wind, conditions

## Data Architecture

### Database Schema

**PostgreSQL** (`agent_server` database):

```sql
-- Agent definitions
agent_definitions (
  id UUID PRIMARY KEY,
  name VARCHAR UNIQUE,
  display_name VARCHAR,
  instructions TEXT,
  model VARCHAR,
  tools JSONB,
  workflows JSONB,
  scopes JSONB,
  is_builtin BOOLEAN,
  created_by VARCHAR,
  version INTEGER,
  is_active BOOLEAN,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)

-- Tool definitions
tool_definitions (
  id UUID PRIMARY KEY,
  name VARCHAR UNIQUE,
  description TEXT,
  schema JSONB,
  entrypoint VARCHAR,
  scopes JSONB,
  is_builtin BOOLEAN,
  created_by VARCHAR,
  version INTEGER,
  is_active BOOLEAN,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)

-- Workflow definitions
workflow_definitions (
  id UUID PRIMARY KEY,
  name VARCHAR UNIQUE,
  description TEXT,
  steps JSONB,
  created_by VARCHAR,
  version INTEGER,
  is_active BOOLEAN,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)

-- Evaluator definitions
eval_definitions (
  id UUID PRIMARY KEY,
  name VARCHAR UNIQUE,
  description TEXT,
  criteria JSONB,
  llm_config JSONB,
  created_by VARCHAR,
  version INTEGER,
  is_active BOOLEAN,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)

-- Run records
run_records (
  id UUID PRIMARY KEY,
  agent_id UUID REFERENCES agent_definitions(id),
  workflow_id UUID REFERENCES workflow_definitions(id),
  status VARCHAR,
  input JSONB,
  output JSONB,
  events JSONB,
  definition_snapshot JSONB,
  parent_run_id UUID REFERENCES run_records(id),
  resume_from_step VARCHAR,
  workflow_state JSONB,
  created_by VARCHAR,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  completed_at TIMESTAMP
)

-- Token grants (cache)
token_grants (
  id UUID PRIMARY KEY,
  user_sub VARCHAR,
  scopes JSONB,
  token TEXT,
  expires_at TIMESTAMP,
  created_at TIMESTAMP
)

-- Dispatcher decision log
dispatcher_decision_log (
  id UUID PRIMARY KEY,
  user_id VARCHAR,
  query_text TEXT,
  selected_tools JSONB,
  selected_agents JSONB,
  confidence FLOAT,
  reasoning TEXT,
  alternatives JSONB,
  timestamp TIMESTAMP
)
```

### Indexes

```sql
-- Performance indexes
CREATE INDEX idx_agent_definitions_builtin_created ON agent_definitions(is_builtin, created_by);
CREATE INDEX idx_tool_definitions_builtin_created ON tool_definitions(is_builtin, created_by);
CREATE INDEX idx_run_records_agent_id ON run_records(agent_id);
CREATE INDEX idx_run_records_status ON run_records(status);
CREATE INDEX idx_run_records_created_by ON run_records(created_by);
CREATE INDEX idx_token_grants_user_sub ON token_grants(user_sub);
CREATE INDEX idx_token_grants_expires_at ON token_grants(expires_at);
CREATE INDEX idx_dispatcher_log_user_id ON dispatcher_decision_log(user_id);
CREATE INDEX idx_dispatcher_log_timestamp ON dispatcher_decision_log(timestamp);
CREATE INDEX idx_dispatcher_log_confidence ON dispatcher_decision_log(confidence);
```

## Authentication & Authorization

### Authentication Flow

```
1. Client sends request with JWT Bearer token
   ↓
2. FastAPI dependency extracts token
   ↓
3. validate_bearer() verifies JWT signature via JWKS
   ↓
4. Claims validated (exp, nbf, iat, aud, iss)
   ↓
5. Scopes extracted from 'scope' or 'scp' claim
   ↓
6. Principal object created with user info
   ↓
7. Principal injected into endpoint handler
```

### Authorization Patterns

**Role-Based**:
- Admin: Full access to all resources
- User: Access to own resources + built-in resources

**Ownership-Based**:
- Personal agents: Only creator can access
- Built-in agents: All users can access
- Custom tools: Only creator can modify/delete

**Scope-Based**:
- Tool execution requires matching scopes
- Agent execution requires agent.execute scope
- Admin operations require admin.write scope

### Token Exchange

For downstream service calls:

```
1. Extract user's JWT from request
   ↓
2. Check token cache for downstream token
   ↓
3. If expired/missing, exchange via OAuth2
   ↓
4. Cache new token with expiry
   ↓
5. Attach to downstream request as Bearer token
```

## Execution Architecture

### Agent Execution Flow

```
1. POST /runs with agent_id and input
   ↓
2. Validate agent exists and user has access
   ↓
3. Capture definition snapshot (version isolation)
   ↓
4. Exchange token for downstream services
   ↓
5. Load agent from registry
   ↓
6. Execute agent with timeout/memory limits
   ↓
7. Track events (started, tool_calls, completed)
   ↓
8. Persist output and status
   ↓
9. Return run record
```

### Tiered Execution Limits

**Simple Tier**:
- Timeout: 30 seconds
- Memory: 512 MB
- Use case: Quick queries, simple tasks

**Complex Tier**:
- Timeout: 5 minutes
- Memory: 2 GB
- Use case: Multi-step reasoning, tool calls

**Batch Tier**:
- Timeout: 30 minutes
- Memory: 4 GB
- Use case: Long-running workflows, batch processing

### Workflow Execution

```
1. POST /runs/workflow with workflow_id and input
   ↓
2. Load workflow definition
   ↓
3. Validate steps and dependencies
   ↓
4. Capture definition snapshot
   ↓
5. For each step:
   a. Resolve input values (JSONPath)
   b. Execute tool or agent
   c. Track step events
   d. Store step output
   ↓
6. Chain outputs between steps
   ↓
7. Return final workflow output
```

### SSE Streaming

```
Client connects to GET /streams/runs/{id}
   ↓
Server polls database every 500ms
   ↓
On status change: emit status event
On new events: emit event data
On completion: emit output and close
On timeout (5min): emit timeout and close
```

## Integration Architecture

### LiteLLM Integration

**Configuration**:
```python
os.environ["OPENAI_BASE_URL"] = "http://litellm-lxc:4000/v1"
os.environ["OPENAI_API_KEY"] = litellm_api_key

model = OpenAIModel(
    model_name="research",  # Model purpose
    provider="openai",
)
```

**Model Registry** (`model_registry.yml`):
```yaml
model_purposes:
  chat: "anthropic:claude-3-5-sonnet"
  research: "qwen3-30b"
  agent: "qwen3-30b"
```

**Tool Calling**:
- Pydantic AI handles tool registration
- LLM decides when to call tools
- Results passed back to LLM
- LLM formats final response

### Busibox Service Integration

**Search API**:
```python
response = await busibox_client.search(
    query="search terms",
    top_k=10,
    bearer_token=downstream_token
)
```

**Ingest API**:
```python
response = await busibox_client.ingest_document(
    file_path="/path/to/doc.pdf",
    metadata={"source": "upload"},
    bearer_token=downstream_token
)
```

**RAG API**:
```python
response = await busibox_client.rag_query(
    query="question",
    database_id="db-uuid",
    top_k=5,
    bearer_token=downstream_token
)
```

## Observability Architecture

### Structured Logging

**Configuration** (`app/core/logging.py`):
```python
import structlog

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ]
)
```

**Log Fields**:
- `timestamp`: ISO 8601
- `level`: DEBUG, INFO, WARNING, ERROR
- `event`: Log message
- `trace_id`: OpenTelemetry trace ID
- `span_id`: OpenTelemetry span ID
- `run_id`: Run record ID
- `agent_id`: Agent definition ID
- `user_sub`: User subject
- `status`: Run status

### OpenTelemetry Tracing

**Instrumentation**:
- FastAPI: HTTP requests
- HTTPX: External API calls
- SQLAlchemy: Database queries

**Spans**:
- `agent_run`: Full agent execution
- `tool_call`: Individual tool execution
- `token_exchange`: OAuth token exchange
- `database_query`: SQL queries

**Attributes**:
- `run.id`: Run record ID
- `agent.id`: Agent definition ID
- `tier`: Execution tier
- `user.sub`: User subject
- `timeout`: Timeout value
- `memory_limit`: Memory limit

### Event Tracking

**Run Events** (stored in `run_records.events`):
```json
[
  {
    "type": "created",
    "timestamp": "2025-12-12T10:00:00Z",
    "data": {}
  },
  {
    "type": "token_exchange",
    "timestamp": "2025-12-12T10:00:01Z",
    "data": {"scopes": ["search.read"]}
  },
  {
    "type": "agent_loaded",
    "timestamp": "2025-12-12T10:00:02Z",
    "data": {"agent_name": "chat_agent"}
  },
  {
    "type": "execution_started",
    "timestamp": "2025-12-12T10:00:03Z",
    "data": {"tier": "complex"}
  },
  {
    "type": "tool_call",
    "timestamp": "2025-12-12T10:00:05Z",
    "data": {"tool": "search_tool", "query": "..."}
  },
  {
    "type": "execution_completed",
    "timestamp": "2025-12-12T10:00:10Z",
    "data": {"duration_ms": 7000}
  }
]
```

## Scalability Architecture

### Performance Characteristics

**Expected Performance**:
- Simple runs: <5s response time
- Complex runs: <5min execution time
- Token exchange: <1s latency
- Concurrent runs: 100+ supported
- Success rate: 95%+ target

**Scalability Targets**:
- Users: 10-100 concurrent users
- Agents: 50+ agent definitions
- Runs: 1000s per day
- Schedules: 10+ concurrent jobs
- Workflows: 20+ workflow definitions

### Optimization Strategies

**Token Caching**:
- Reduces auth overhead
- 60s refresh buffer
- Scope-based cache keys

**Connection Pooling**:
- SQLAlchemy async pool
- Default: 20 connections
- Configurable via DATABASE_URL

**Agent Registry**:
- In-memory cache
- Thread-safe refresh
- Reduces database queries

**Redis Caching** (optional):
- Dispatcher decisions (1 hour TTL)
- Common query patterns
- Reduces LLM calls

## Deployment Architecture

### Container: agent-lxc

**IP**: 10.96.201.202 (test), varies (production)  
**Port**: 4111  
**Service**: systemd (agent-api.service)  
**User**: agent  
**Working Dir**: /srv/agent

### Dependencies

**Internal**:
- PostgreSQL (pg-lxc:5432)
- Redis (ingest-lxc:6379)
- LiteLLM (litellm-lxc:4000)
- Search API (search-lxc:8003)
- Ingest API (ingest-lxc:8001)
- RAG API (milvus-lxc:8004)

**External**:
- Open-Meteo API (weather tool)

### High Availability

**Not Yet Implemented**:
- Load balancing
- Horizontal scaling
- Database replication
- Circuit breakers

**Current Limitations**:
- Single instance
- No failover
- No request queuing

## Security Architecture

### Data Protection

**Secrets Management**:
- Ansible vault for deployment secrets
- Environment variables for runtime config
- No secrets in code or logs

**Token Security**:
- JWT signature verification via JWKS
- Token expiry enforcement
- Secure token exchange
- Token cache with expiry

**Input Validation**:
- Pydantic schema validation
- SQL injection prevention (SQLAlchemy ORM)
- XSS prevention (FastAPI escaping)

### Access Control

**Resource Isolation**:
- Personal agents: ownership-based
- Built-in resources: immutable
- Custom tools: creator-only modification

**Authorization Checks**:
- Every endpoint validates principal
- Ownership checks on all CRUD operations
- Scope checks on tool execution

**Audit Trail**:
- Soft deletes preserve history
- Structured logging for all operations
- Dispatcher decision logging

## Related Documentation

- **Deployment**: `docs/deployment/agent-server-deployment.md`
- **Testing**: `docs/guides/agent-server-testing.md`
- **API Reference**: `docs/reference/agent-server-api.md`
- **Integration**: `docs/architecture/agent-client-integration.md`









