# Data Model: Production-Grade Agent Server

**Feature**: 005-i-want-to  
**Date**: 2025-01-08  
**Purpose**: Define database schema and entity relationships for agent server

## Entity Relationship Overview

```
┌─────────────────┐
│ AgentDefinition │──┐
└─────────────────┘  │
                     │ references
┌─────────────────┐  │
│ ToolDefinition  │  │
└─────────────────┘  │
                     │
┌─────────────────┐  │
│ RunRecord       │◄─┘
│  - agent_id     │
│  - workflow_id  │──┐
└─────────────────┘  │
                     │ references
┌─────────────────┐  │
│WorkflowDefinition│◄─┘
└─────────────────┘

┌─────────────────┐
│ TokenGrant      │
│  - subject      │
│  - scopes       │
└─────────────────┘

┌─────────────────┐
│ EvalDefinition  │
└─────────────────┘

┌─────────────────┐
│ RagDatabase     │──┐
└─────────────────┘  │
                     │ has many
┌─────────────────┐  │
│ RagDocument     │◄─┘
└─────────────────┘
```

## Core Entities

### AgentDefinition

Represents a configured AI agent with instructions, model, and allowed tools.

**Attributes**:
- `id` (UUID, PK): Unique identifier
- `name` (String, unique, indexed): Agent identifier (e.g., "chat-agent", "rfp-analyzer")
- `display_name` (String, optional): Human-readable name
- `description` (Text, optional): Purpose and capabilities
- `model` (String): Model identifier (e.g., "anthropic:claude-3-5-sonnet", "openai:gpt-4")
- `instructions` (Text): System prompt/instructions for agent behavior
- `tools` (JSONB): Tool configuration `{"names": ["search", "ingest", "rag"]}`
- `workflow` (JSONB, optional): Workflow reference or inline definition
- `scopes` (JSONB): Required scopes for execution `["agent.execute", "search.read"]`
- `is_active` (Boolean, default true): Whether agent is available for execution
- `version` (Integer, default 1): Incremented on updates for tracking
- `created_at` (Timestamp): Creation time
- `updated_at` (Timestamp): Last modification time

**Validation Rules**:
- `name` must be alphanumeric + hyphens, 1-120 characters
- `model` must reference a valid model identifier
- `tools.names` must reference entries in `TOOL_REGISTRY`
- `scopes` must be valid scope strings
- `instructions` limited to 10,000 characters (prevent abuse)

**State Transitions**:
- Created → Active (default)
- Active → Inactive (deactivation)
- Inactive → Active (reactivation)
- Version increments on update (immutable history)

**Indexes**:
- `name` (unique)
- `is_active` (for filtering active agents)

### ToolDefinition

Represents a registered tool that agents can call.

**Attributes**:
- `id` (UUID, PK): Unique identifier
- `name` (String, unique, indexed): Tool identifier (e.g., "search", "ingest", "rag")
- `description` (Text, optional): Tool purpose and usage
- `schema` (JSONB): JSON schema for tool arguments `{"query": {"type": "string"}, "top_k": {"type": "integer"}}`
- `entrypoint` (String): Registered adapter name in `TOOL_REGISTRY`
- `scopes` (JSONB): Required scopes for tool execution `["search.read"]`
- `is_active` (Boolean, default true): Whether tool is available
- `version` (Integer, default 1): Incremented on updates
- `created_at` (Timestamp): Creation time
- `updated_at` (Timestamp): Last modification time

**Validation Rules**:
- `name` must be alphanumeric + hyphens, 1-120 characters
- `entrypoint` must exist in `TOOL_REGISTRY` (code-level validation)
- `schema` must be valid JSON schema
- `scopes` must be valid scope strings

**Indexes**:
- `name` (unique)
- `is_active`

### WorkflowDefinition

Represents a multi-step workflow orchestrating agent operations.

**Attributes**:
- `id` (UUID, PK): Unique identifier
- `name` (String, unique, indexed): Workflow identifier
- `description` (Text, optional): Workflow purpose
- `steps` (JSONB): Array of step definitions with branching logic
  ```json
  [
    {"id": "ingest", "type": "tool", "tool": "ingest", "args": {"path": "$.input.path"}},
    {"id": "embed", "type": "tool", "tool": "embed", "args": {"doc_id": "$.ingest.document_id"}},
    {"id": "summarize", "type": "agent", "agent": "summarizer", "input": "$.embed.text"}
  ]
  ```
- `is_active` (Boolean, default true): Whether workflow is available
- `version` (Integer, default 1): Incremented on updates
- `created_at` (Timestamp): Creation time
- `updated_at` (Timestamp): Last modification time

**Validation Rules**:
- `name` must be alphanumeric + hyphens, 1-120 characters
- `steps` must be valid JSON array with required fields (id, type)
- Step references (tools, agents) must exist
- No circular dependencies in step graph

**Indexes**:
- `name` (unique)
- `is_active`

### RunRecord

Represents an agent execution with input, output, and events.

**Attributes**:
- `id` (UUID, PK): Unique identifier
- `agent_id` (UUID, FK → AgentDefinition, not enforced): Agent that was executed
- `workflow_id` (UUID, FK → WorkflowDefinition, optional): Workflow if applicable
- `status` (String, indexed): Execution status (pending, running, succeeded, failed, timeout)
- `input` (JSONB): Run input `{"prompt": "search for documents", "context": {...}}`
- `output` (JSONB, optional): Run output `{"message": "...", "tool_results": [...]}`
- `events` (JSONB): Array of execution events (tool calls, errors, state changes)
  ```json
  [
    {"timestamp": "2025-01-08T12:00:00Z", "type": "tool_call", "tool": "search", "args": {...}, "result": {...}},
    {"timestamp": "2025-01-08T12:00:05Z", "type": "completion", "output": {...}}
  ]
  ```
- `created_by` (String, optional): User subject (from JWT) who initiated run
- `created_at` (Timestamp, indexed): Run start time
- `updated_at` (Timestamp): Last status update

**Validation Rules**:
- `status` must be one of: pending, running, succeeded, failed, timeout
- `input` must be valid JSON
- `events` must be JSON array

**State Transitions**:
- Created → Pending (initial)
- Pending → Running (execution starts)
- Running → Succeeded (completion)
- Running → Failed (error)
- Running → Timeout (exceeded limit)

**Indexes**:
- `agent_id` (for filtering by agent)
- `status` (for filtering by status)
- `created_at` (for time-based queries)
- `created_by` (for user-specific runs)

### TokenGrant

Represents a cached downstream token for service-to-service auth.

**Attributes**:
- `id` (UUID, PK): Unique identifier
- `subject` (String, indexed): User subject (from JWT) who owns token
- `scopes` (JSONB): Token scopes `["search.read", "ingest.write"]`
- `token` (Text): Encrypted downstream token value
- `expires_at` (Timestamp, indexed): Token expiry time
- `created_at` (Timestamp): Token creation time

**Validation Rules**:
- `subject` must be non-empty string
- `scopes` must be JSON array of strings
- `expires_at` must be future timestamp
- `token` must be encrypted before storage

**Indexes**:
- `subject` (for user-specific token lookup)
- `expires_at` (for expiry-based cleanup)
- Composite: `(subject, scopes, expires_at)` for cache lookup

**Cleanup**:
- Expired tokens removed via periodic job (daily)
- Tokens evicted on access if expired (lazy cleanup)

### EvalDefinition

Represents an evaluation scorer for agent performance.

**Attributes**:
- `id` (UUID, PK): Unique identifier
- `name` (String, unique, indexed): Scorer identifier (e.g., "response-quality", "latency")
- `description` (Text, optional): Scorer purpose
- `config` (JSONB): Scorer configuration (thresholds, weights, etc.)
  ```json
  {
    "type": "latency",
    "threshold_ms": 5000,
    "alert_on_failure": true
  }
  ```
- `is_active` (Boolean, default true): Whether scorer is enabled
- `version` (Integer, default 1): Incremented on updates
- `created_at` (Timestamp): Creation time
- `updated_at` (Timestamp): Last modification time

**Validation Rules**:
- `name` must be alphanumeric + hyphens, 1-120 characters
- `config` must be valid JSON

**Indexes**:
- `name` (unique)
- `is_active`

### RagDatabase

Represents a RAG vector database configuration.

**Attributes**:
- `id` (UUID, PK): Unique identifier
- `name` (String, unique, indexed): Database identifier
- `description` (Text, optional): Database purpose
- `config` (JSONB): Database configuration (collection name, embedding model, etc.)
- `is_active` (Boolean, default true): Whether database is available
- `created_at` (Timestamp): Creation time
- `updated_at` (Timestamp): Last modification time

**Validation Rules**:
- `name` must be alphanumeric + hyphens, 1-120 characters
- `config` must be valid JSON

**Indexes**:
- `name` (unique)
- `is_active`

### RagDocument

Represents a document in a RAG database.

**Attributes**:
- `id` (UUID, PK): Unique identifier
- `rag_database_id` (UUID, FK → RagDatabase, CASCADE): Parent database
- `path` (String): Document path or identifier
- `metadata` (JSONB): Document metadata (title, author, tags, etc.)
- `created_at` (Timestamp): Creation time
- `updated_at` (Timestamp): Last modification time

**Validation Rules**:
- `rag_database_id` must reference existing database
- `path` must be non-empty string

**Indexes**:
- `rag_database_id` (for filtering by database)

## Database Schema (SQL DDL)

```sql
-- Agent definitions
CREATE TABLE agent_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(120) UNIQUE NOT NULL,
    display_name VARCHAR(255),
    description TEXT,
    model VARCHAR(255) NOT NULL,
    instructions TEXT NOT NULL,
    tools JSONB DEFAULT '{}'::jsonb,
    workflow JSONB,
    scopes JSONB DEFAULT '[]'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_agent_definitions_name ON agent_definitions(name);
CREATE INDEX idx_agent_definitions_active ON agent_definitions(is_active);

-- Tool definitions
CREATE TABLE tool_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(120) UNIQUE NOT NULL,
    description TEXT,
    schema JSONB DEFAULT '{}'::jsonb,
    entrypoint VARCHAR(255) NOT NULL,
    scopes JSONB DEFAULT '[]'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_tool_definitions_name ON tool_definitions(name);
CREATE INDEX idx_tool_definitions_active ON tool_definitions(is_active);

-- Workflow definitions
CREATE TABLE workflow_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(120) UNIQUE NOT NULL,
    description TEXT,
    steps JSONB DEFAULT '[]'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_workflow_definitions_name ON workflow_definitions(name);
CREATE INDEX idx_workflow_definitions_active ON workflow_definitions(is_active);

-- Eval definitions
CREATE TABLE eval_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(120) UNIQUE NOT NULL,
    description TEXT,
    config JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_eval_definitions_name ON eval_definitions(name);
CREATE INDEX idx_eval_definitions_active ON eval_definitions(is_active);

-- RAG databases
CREATE TABLE rag_databases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(120) UNIQUE NOT NULL,
    description TEXT,
    config JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_rag_databases_name ON rag_databases(name);
CREATE INDEX idx_rag_databases_active ON rag_databases(is_active);

-- RAG documents
CREATE TABLE rag_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rag_database_id UUID REFERENCES rag_databases(id) ON DELETE CASCADE,
    path VARCHAR(255) NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_rag_documents_db ON rag_documents(rag_database_id);

-- Run records
CREATE TABLE run_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL,
    workflow_id UUID,
    status VARCHAR(50) DEFAULT 'pending',
    input JSONB DEFAULT '{}'::jsonb,
    output JSONB,
    events JSONB DEFAULT '[]'::jsonb,
    created_by VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_run_records_agent ON run_records(agent_id);
CREATE INDEX idx_run_records_status ON run_records(status);
CREATE INDEX idx_run_records_created_at ON run_records(created_at);
CREATE INDEX idx_run_records_created_by ON run_records(created_by);

-- Token grants
CREATE TABLE token_grants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject VARCHAR(255) NOT NULL,
    scopes JSONB DEFAULT '[]'::jsonb,
    token TEXT NOT NULL,
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_token_grants_subject ON token_grants(subject);
CREATE INDEX idx_token_grants_expires_at ON token_grants(expires_at);
CREATE INDEX idx_token_grants_lookup ON token_grants(subject, expires_at);
```

## Data Access Patterns

### Agent Execution Flow

1. **Validate request**: Check auth token, extract principal
2. **Load agent**: Query `agent_definitions` by ID, check `is_active`
3. **Exchange token**: Check `token_grants` cache, exchange if needed
4. **Create run**: Insert `run_records` with status=pending
5. **Execute agent**: Run Pydantic AI agent with tools
6. **Update run**: Update `run_records` with status=succeeded/failed, output, events
7. **Stream updates**: Poll `run_records` for status changes, emit SSE events

### Dynamic Agent Management

1. **Create agent**: Insert `agent_definitions`, validate tools exist
2. **Refresh registry**: Load active agents from DB into memory
3. **Update agent**: Update `agent_definitions`, increment version
4. **Deactivate agent**: Set `is_active=false`, prevent new runs

### Token Caching

1. **Check cache**: Query `token_grants` WHERE subject AND scopes AND expires_at > NOW()
2. **Exchange if miss**: Call Busibox authz OAuth2 endpoint
3. **Store token**: Insert `token_grants` with expiry
4. **Cleanup expired**: DELETE FROM `token_grants` WHERE expires_at < NOW() (daily job)

## Migration Strategy

**Initial Schema**: Apply DDL from `app/db/schema.sql` manually before first deployment

**Future Migrations**: Use Alembic or raw SQL migrations with version tracking

**Rollback**: Each migration must have documented rollback SQL

**Testing**: Validate schema changes on test database before production










