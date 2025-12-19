# Data Model: Agent-Server API Enhancements

**Feature**: 006-agent-client-specs  
**Date**: 2025-12-11  
**Status**: Complete

## Overview

This document defines the data model changes required for agent-server API enhancements, including new fields, relationships, and validation rules.

## Entity Changes

### 1. AgentDefinition (MODIFIED)

**Purpose**: Represents an AI agent with instructions, tools, and configuration

**New Fields**:
```python
is_builtin: bool = False  # Distinguishes system agents from personal agents
```

**Existing Fields** (for reference):
```python
id: UUID
name: str
description: str
instructions: str
tools: dict  # JSON containing tool names and configuration
model: str
created_by: str  # User ID of creator
created_at: datetime
updated_at: datetime
is_active: bool = True
version: int = 1
```

**Validation Rules**:
- `is_builtin` can only be set to `True` by system (not via API)
- `is_builtin=True` resources cannot be modified or deleted (return 403)
- `created_by` must be populated for all agents
- Personal agents (`is_builtin=False`) only visible to creator

**Indexes**:
```sql
CREATE INDEX idx_agent_definitions_builtin_created 
ON agent_definitions (is_builtin, created_by) 
WHERE is_active = TRUE;
```

---

### 2. ToolDefinition (MODIFIED)

**Purpose**: Represents a callable tool with input/output schema

**New Fields**:
```python
is_builtin: bool = False  # Distinguishes system tools from custom tools
version: int = 1          # Increments on each update
```

**Existing Fields** (for reference):
```python
id: UUID
name: str
description: str
schema: dict  # JSON containing input/output schemas
entrypoint: str  # Python module path
scopes: list[str]  # Required permissions
created_by: str
created_at: datetime
updated_at: datetime
is_active: bool = True
```

**Validation Rules**:
- `is_builtin=True` tools cannot be modified or deleted (return 403)
- `version` increments on each PUT request
- Tools in use by active agents cannot be deleted (return 409)
- `name` must be unique within user's tools (built-in + personal)

**Indexes**:
```sql
CREATE INDEX idx_tool_definitions_builtin_created 
ON tool_definitions (is_builtin, created_by) 
WHERE is_active = TRUE;

CREATE INDEX idx_tool_definitions_name 
ON tool_definitions (name) 
WHERE is_active = TRUE;
```

---

### 3. WorkflowDefinition (MODIFIED)

**Purpose**: Represents a multi-step process with ordered steps

**New Fields**:
```python
version: int = 1  # Increments on each update
```

**Existing Fields** (for reference):
```python
id: UUID
name: str
description: str
steps: dict  # JSON containing ordered workflow steps
created_by: str
created_at: datetime
updated_at: datetime
is_active: bool = True
```

**Validation Rules**:
- `version` increments on each PUT request
- Workflows with active scheduled runs cannot be deleted (return 409)
- `steps` must be validated before saving (valid step structure)
- Each step must reference valid agent or tool

**Indexes**:
```sql
CREATE INDEX idx_workflow_definitions_created_by 
ON workflow_definitions (created_by) 
WHERE is_active = TRUE;
```

---

### 4. EvalDefinition (MODIFIED)

**Purpose**: Represents a scoring mechanism for agent runs

**New Fields**:
```python
version: int = 1  # Increments on each update
```

**Existing Fields** (for reference):
```python
id: UUID
name: str
description: str
config: dict  # JSON containing evaluation criteria, thresholds, LLM config
created_by: str
created_at: datetime
updated_at: datetime
is_active: bool = True
```

**Validation Rules**:
- `version` increments on each PUT request
- `config` must contain valid evaluation configuration
- Evaluators can be deleted even if referenced in past runs (soft delete)

**Indexes**:
```sql
CREATE INDEX idx_eval_definitions_created_by 
ON eval_definitions (created_by) 
WHERE is_active = TRUE;
```

---

### 5. ScheduledRun (MODIFIED)

**Purpose**: Represents a recurring agent execution

**Modified Fields**:
```python
# Note: Parameter name standardization from job_id to schedule_id in API
# Database field name remains unchanged
```

**Existing Fields** (for reference):
```python
id: UUID  # Referenced as schedule_id in API
agent_id: UUID
workflow_id: UUID | None
input: dict
cron_expression: str
tier: str
scopes: list[str]
next_run_time: datetime
is_active: bool = True
created_by: str
created_at: datetime
updated_at: datetime
```

**Validation Rules**:
- `cron_expression` must be valid (validated with croniter)
- `next_run_time` recalculated when cron_expression updated
- APScheduler job must be updated when schedule modified
- Schedules for deleted agents/workflows must be cleaned up

**Indexes**:
```sql
CREATE INDEX idx_scheduled_runs_next_run_time 
ON scheduled_runs (next_run_time) 
WHERE is_active = TRUE;

CREATE INDEX idx_scheduled_runs_agent_workflow 
ON scheduled_runs (agent_id, workflow_id) 
WHERE is_active = TRUE;
```

---

### 6. RunRecord (MODIFIED)

**Purpose**: Represents a single execution of an agent or workflow

**New Fields**:
```python
definition_snapshot: dict | None  # JSONB snapshot of agent/tool/workflow definitions at run start
parent_run_id: UUID | None        # Reference to original run if this is a resume
resume_from_step: str | None      # Step ID where resume started
workflow_state: dict | None       # JSONB containing completed step outputs for resume
```

**Existing Fields** (for reference):
```python
id: UUID
agent_id: UUID
workflow_id: UUID | None
status: str  # pending, running, completed, failed
input: dict
output: dict | None
error: str | None
started_at: datetime | None
completed_at: datetime | None
created_by: str
created_at: datetime
```

**Validation Rules**:
- `definition_snapshot` captured at run start (version isolation)
- `parent_run_id` only set for resumed runs
- `resume_from_step` only valid if `parent_run_id` is set
- `workflow_state` only valid for workflow runs
- Resume only allowed for runs with `status = "failed"`

**Indexes**:
```sql
CREATE INDEX idx_run_records_parent 
ON run_records (parent_run_id);

CREATE INDEX idx_run_records_snapshot 
ON run_records USING GIN (definition_snapshot);

CREATE INDEX idx_run_records_workflow_state 
ON run_records USING GIN (workflow_state);
```

---

### 7. DispatcherDecisionLog (NEW)

**Purpose**: Records each dispatcher routing decision for accuracy measurement and debugging

**Fields**:
```python
id: UUID
query_text: str  # Original user query (max 1000 chars)
selected_tools: list[str]  # Tool names selected by dispatcher
selected_agents: list[str]  # Agent IDs selected by dispatcher
confidence: float  # 0-1 confidence score
reasoning: str  # Explanation of routing decision
alternatives: list[str]  # Alternative tools/agents suggested
user_id: str  # User who made the query
request_id: str  # Correlation ID for request tracing
timestamp: datetime
```

**Validation Rules**:
- `confidence` must be between 0.0 and 1.0
- `query_text` truncated to 1000 characters
- `timestamp` automatically set to current time
- Log retention: 90 days (automated cleanup job)

**Indexes**:
```sql
CREATE INDEX idx_dispatcher_log_user_timestamp 
ON dispatcher_decision_log (user_id, timestamp DESC);

CREATE INDEX idx_dispatcher_log_confidence 
ON dispatcher_decision_log (confidence);

CREATE INDEX idx_dispatcher_log_timestamp 
ON dispatcher_decision_log (timestamp DESC);
```

**Table Definition**:
```sql
CREATE TABLE dispatcher_decision_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text VARCHAR(1000) NOT NULL,
    selected_tools TEXT[] NOT NULL,
    selected_agents TEXT[] NOT NULL,
    confidence FLOAT NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    reasoning TEXT NOT NULL,
    alternatives TEXT[] NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    request_id VARCHAR(255) NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## Relationships

### AgentDefinition Relationships
- **created_by** → User (external, not in agent-server schema)
- **tools** → ToolDefinition (via JSON array of tool names)
- Referenced by:
  - RunRecord.agent_id
  - ScheduledRun.agent_id

### ToolDefinition Relationships
- **created_by** → User (external)
- Referenced by:
  - AgentDefinition.tools (JSON array)
  - WorkflowDefinition.steps (JSON structure)

### WorkflowDefinition Relationships
- **created_by** → User (external)
- **steps** → AgentDefinition, ToolDefinition (via JSON structure)
- Referenced by:
  - RunRecord.workflow_id
  - ScheduledRun.workflow_id

### EvalDefinition Relationships
- **created_by** → User (external)
- Referenced by:
  - RunRecord (via evaluation results, not direct FK)

### ScheduledRun Relationships
- **agent_id** → AgentDefinition (FK)
- **workflow_id** → WorkflowDefinition (FK, nullable)
- **created_by** → User (external)

### RunRecord Relationships
- **agent_id** → AgentDefinition (FK)
- **workflow_id** → WorkflowDefinition (FK, nullable)
- **parent_run_id** → RunRecord (FK, self-reference for resume)
- **created_by** → User (external)

### DispatcherDecisionLog Relationships
- **user_id** → User (external, no FK)
- **selected_agents** → AgentDefinition (via array of IDs, no FK)

---

## State Transitions

### RunRecord Status
```
pending → running → completed
              ↓
            failed → [resume] → pending (new run with parent_run_id)
```

**Rules**:
- Only `failed` runs can be resumed
- Resume creates new run with `parent_run_id` set
- Original run status remains `failed`

### Resource Lifecycle (AgentDefinition, ToolDefinition, WorkflowDefinition, EvalDefinition)
```
active (is_active=True) → soft deleted (is_active=False)
```

**Rules**:
- Soft delete only (no hard deletes)
- Inactive resources return 404 in API
- Version increments on each update while active

---

## Migration Scripts

### Phase 1 Migration

```sql
-- Add is_builtin flag to agent_definitions
ALTER TABLE agent_definitions 
ADD COLUMN IF NOT EXISTS is_builtin BOOLEAN DEFAULT FALSE;

-- Add is_builtin and version to tool_definitions
ALTER TABLE tool_definitions 
ADD COLUMN IF NOT EXISTS is_builtin BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;

-- Add version to workflow_definitions
ALTER TABLE workflow_definitions 
ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;

-- Add version to eval_definitions
ALTER TABLE eval_definitions 
ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;

-- Add definition_snapshot to run_records
ALTER TABLE run_records 
ADD COLUMN IF NOT EXISTS definition_snapshot JSONB;

-- Create dispatcher_decision_log table
CREATE TABLE IF NOT EXISTS dispatcher_decision_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text VARCHAR(1000) NOT NULL,
    selected_tools TEXT[] NOT NULL,
    selected_agents TEXT[] NOT NULL,
    confidence FLOAT NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    reasoning TEXT NOT NULL,
    alternatives TEXT[] NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    request_id VARCHAR(255) NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_agent_definitions_builtin_created 
ON agent_definitions (is_builtin, created_by) WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_tool_definitions_builtin_created 
ON tool_definitions (is_builtin, created_by) WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_tool_definitions_name 
ON tool_definitions (name) WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_workflow_definitions_created_by 
ON workflow_definitions (created_by) WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_eval_definitions_created_by 
ON eval_definitions (created_by) WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_scheduled_runs_next_run_time 
ON scheduled_runs (next_run_time) WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_scheduled_runs_agent_workflow 
ON scheduled_runs (agent_id, workflow_id) WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_run_records_snapshot 
ON run_records USING GIN (definition_snapshot);

CREATE INDEX IF NOT EXISTS idx_dispatcher_log_user_timestamp 
ON dispatcher_decision_log (user_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_dispatcher_log_confidence 
ON dispatcher_decision_log (confidence);

CREATE INDEX IF NOT EXISTS idx_dispatcher_log_timestamp 
ON dispatcher_decision_log (timestamp DESC);
```

### Phase 2/3 Migration (Workflow Resume)

```sql
-- Add workflow resume fields to run_records
ALTER TABLE run_records 
ADD COLUMN IF NOT EXISTS parent_run_id UUID REFERENCES run_records(id),
ADD COLUMN IF NOT EXISTS resume_from_step VARCHAR(255),
ADD COLUMN IF NOT EXISTS workflow_state JSONB;

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_run_records_parent 
ON run_records (parent_run_id);

CREATE INDEX IF NOT EXISTS idx_run_records_workflow_state 
ON run_records USING GIN (workflow_state);
```

---

## Data Validation

### AgentDefinition
- `name`: 1-100 characters, alphanumeric + spaces/hyphens
- `instructions`: 1-10000 characters
- `tools`: Valid JSON object with tool names array
- `model`: Valid LiteLLM model identifier

### ToolDefinition
- `name`: 1-100 characters, alphanumeric + underscores (Python identifier)
- `schema`: Valid JSON Schema for input/output
- `entrypoint`: Valid Python module path (module.submodule:function)
- `scopes`: Array of valid scope strings

### WorkflowDefinition
- `steps`: Valid JSON array with step objects
- Each step: `{id, type: "agent"|"tool", agent_id|tool_id, input_mapping, output_mapping}`
- Step IDs unique within workflow
- Referenced agents/tools must exist and be active

### EvalDefinition
- `config`: Valid JSON with required fields: `criteria`, `pass_threshold`, `model`
- `pass_threshold`: 0.0-1.0

### ScheduledRun
- `cron_expression`: Valid cron syntax (validated with croniter)
- `tier`: One of allowed tier values
- `scopes`: Array of valid scope strings

### RunRecord
- `status`: One of: "pending", "running", "completed", "failed"
- `definition_snapshot`: Valid JSON matching expected structure
- `workflow_state`: Valid JSON with completed_steps array

### DispatcherDecisionLog
- `confidence`: 0.0-1.0
- `query_text`: Max 1000 characters
- `selected_tools`, `selected_agents`, `alternatives`: Arrays of strings

---

## Performance Considerations

### Query Optimization
- Compound indexes on (is_builtin, created_by) for personal agent filtering
- GIN indexes on JSONB columns for efficient querying
- Partial indexes with WHERE is_active = TRUE to reduce index size

### Storage Optimization
- JSONB compression via PostgreSQL TOAST (automatic for large values)
- definition_snapshot compressed automatically (typically 1-10KB per run)
- workflow_state compressed automatically (size depends on step outputs)

### Scaling Considerations
- dispatcher_decision_log can grow large (1000 queries/hour = 24K/day = 8.7M/year)
- Implement automated cleanup job (delete logs older than 90 days)
- Consider partitioning dispatcher_decision_log by timestamp if volume increases
- Monitor index bloat and run REINDEX periodically

---

## Rollback Procedures

### Phase 1 Rollback
```sql
-- Remove new columns (data loss)
ALTER TABLE agent_definitions DROP COLUMN IF EXISTS is_builtin;
ALTER TABLE tool_definitions DROP COLUMN IF EXISTS is_builtin, DROP COLUMN IF EXISTS version;
ALTER TABLE workflow_definitions DROP COLUMN IF EXISTS version;
ALTER TABLE eval_definitions DROP COLUMN IF EXISTS version;
ALTER TABLE run_records DROP COLUMN IF EXISTS definition_snapshot;

-- Drop new table
DROP TABLE IF EXISTS dispatcher_decision_log;

-- Drop indexes
DROP INDEX IF EXISTS idx_agent_definitions_builtin_created;
DROP INDEX IF EXISTS idx_tool_definitions_builtin_created;
DROP INDEX IF EXISTS idx_tool_definitions_name;
DROP INDEX IF EXISTS idx_workflow_definitions_created_by;
DROP INDEX IF EXISTS idx_eval_definitions_created_by;
DROP INDEX IF EXISTS idx_scheduled_runs_next_run_time;
DROP INDEX IF EXISTS idx_scheduled_runs_agent_workflow;
DROP INDEX IF EXISTS idx_run_records_snapshot;
DROP INDEX IF EXISTS idx_dispatcher_log_user_timestamp;
DROP INDEX IF EXISTS idx_dispatcher_log_confidence;
DROP INDEX IF EXISTS idx_dispatcher_log_timestamp;
```

### Phase 2/3 Rollback
```sql
-- Remove workflow resume columns (data loss)
ALTER TABLE run_records 
DROP COLUMN IF EXISTS parent_run_id,
DROP COLUMN IF EXISTS resume_from_step,
DROP COLUMN IF EXISTS workflow_state;

-- Drop indexes
DROP INDEX IF EXISTS idx_run_records_parent;
DROP INDEX IF EXISTS idx_run_records_workflow_state;
```

---

**Status**: Data model complete, ready for contract generation








