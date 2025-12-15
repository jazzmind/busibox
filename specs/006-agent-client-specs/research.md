# Research: Agent-Server API Enhancements

**Feature**: 006-agent-client-specs  
**Date**: 2025-12-11  
**Status**: Complete

## Overview

This document consolidates research findings for implementing agent-server API enhancements including dispatcher agent, personal agent management, CRUD operations, and version isolation.

## Research Areas

### 1. Dispatcher Agent Implementation with LiteLLM

**Decision**: Use Pydantic AI Agent with LiteLLM (OpenAI-compatible) for query routing

**Rationale**:
- LiteLLM already integrated in agent-server stack (no external API dependencies)
- Pydantic AI provides structured output validation (routing decisions as Pydantic models)
- OpenAI-compatible interface works with any model in LiteLLM (Claude, GPT, local models)
- System prompt can encode routing logic without complex rule engines
- Confidence scoring via LLM reasoning is more flexible than heuristic-based approaches
- All inference stays within Busibox infrastructure (no external API calls)

**Alternatives Considered**:
- **Heuristic-based routing** (keyword matching, regex patterns): Rejected because it's brittle, requires constant maintenance, and can't handle natural language nuance
- **Fine-tuned classification model**: Rejected due to training data requirements, deployment complexity, and overkill for medium scale (1000 queries/hour)
- **Embedding-based similarity search**: Rejected because it requires pre-defined query templates and doesn't provide reasoning

**Implementation Pattern**:
```python
import os
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic import BaseModel

# Configure to use LiteLLM
os.environ["OPENAI_BASE_URL"] = str(settings.litellm_base_url)
os.environ["OPENAI_API_KEY"] = os.getenv("LITELLM_API_KEY", "sk-1234")

class RoutingDecision(BaseModel):
    selected_tools: list[str]
    selected_agents: list[str]
    confidence: float
    reasoning: str
    alternatives: list[str]

# Create OpenAI-compatible model
# Use task-based model purposes from model_registry.yml
model = OpenAIModel(
    model_name="fast",  # LiteLLM routes to fast model (phi-4)
    provider="openai",
)

dispatcher_agent = Agent[None, RoutingDecision](
    model=model,
    system_prompt="Analyze query and route to appropriate tools/agents...",
)
```

**Best Practices**:
- Use temperature=0.3 for consistent routing decisions
- Include few-shot examples in system prompt for common query patterns
- Implement caching for repeated queries (Redis with 1-hour TTL)
- Set timeout at 10s with fallback to default routing
- Log all decisions for accuracy analysis (FR-012-OBS)

---

### 2. Snapshot-Based Version Isolation

**Decision**: Capture tool/workflow definitions as JSONB snapshots in run_records at execution start

**Rationale**:
- Simple to implement (single JSONB column)
- No complex version history infrastructure required
- Running agents immune to definition updates
- Supports workflow resume (state preservation)
- PostgreSQL JSONB provides efficient storage and querying

**Alternatives Considered**:
- **Version history table with immutable versions**: Rejected due to complexity, storage overhead, and unnecessary for stated requirements
- **Copy-on-write with reference counting**: Rejected due to implementation complexity and race condition risks
- **No version isolation (live references)**: Rejected because mid-run updates could cause failures or inconsistent behavior

**Implementation Pattern**:
```python
# At run start
run_record = RunRecord(
    agent_id=agent_id,
    workflow_id=workflow_id,
    definition_snapshot={
        "agent": agent_def.model_dump(),
        "tools": [tool.model_dump() for tool in tools],
        "workflow": workflow_def.model_dump() if workflow_def else None
    },
    ...
)
```

**Schema Addition**:
```sql
ALTER TABLE run_records ADD COLUMN definition_snapshot JSONB;
CREATE INDEX idx_run_records_snapshot ON run_records USING GIN (definition_snapshot);
```

**Best Practices**:
- Capture snapshots in a single transaction with run creation
- Use GIN index for JSONB querying (workflow resume lookups)
- Compress snapshots for large workflows (PostgreSQL TOAST handles this automatically)
- Document snapshot format for future compatibility

---

### 3. Personal Agent Filtering with SQLAlchemy

**Decision**: Use SQLAlchemy ORM filters with OR clause for built-in + owned agents

**Rationale**:
- Leverages existing SQLAlchemy patterns in agent-server
- Server-side filtering prevents data leakage
- Simple to test and audit
- Efficient with proper indexing

**Alternatives Considered**:
- **PostgreSQL Row-Level Security (RLS)**: Rejected because RLS already used for file-level access; adding agent-level RLS increases policy complexity
- **Application-level post-query filtering**: Rejected due to performance overhead and data leakage risk
- **Separate tables for built-in vs personal agents**: Rejected due to schema complexity and query inefficiency

**Implementation Pattern**:
```python
from sqlalchemy import select, or_

stmt = select(AgentDefinition).where(
    AgentDefinition.is_active.is_(True),
    or_(
        AgentDefinition.is_builtin.is_(True),
        AgentDefinition.created_by == current_user_id
    )
)
```

**Schema Addition**:
```sql
ALTER TABLE agent_definitions ADD COLUMN is_builtin BOOLEAN DEFAULT FALSE;
CREATE INDEX idx_agent_definitions_builtin_created ON agent_definitions (is_builtin, created_by);
```

**Best Practices**:
- Apply filtering in all GET endpoints (list, retrieve)
- Return 404 (not 403) for unauthorized access to hide existence
- Add compound index on (is_builtin, created_by) for query performance
- Validate is_builtin flag on create (only system can set to true)

---

### 4. APScheduler Integration for Schedule Updates

**Decision**: Use APScheduler's `reschedule_job()` method with CronTrigger for schedule updates

**Rationale**:
- APScheduler already integrated in agent-server
- `reschedule_job()` is atomic and thread-safe
- CronTrigger handles complex cron expressions
- next_run_time automatically recalculated

**Alternatives Considered**:
- **Delete and recreate job**: Rejected due to race conditions and job history loss
- **Manual cron parsing and next_run_time calculation**: Rejected because APScheduler handles this correctly
- **External scheduler (cron, systemd timers)**: Rejected due to dynamic schedule requirements and deployment complexity

**Implementation Pattern**:
```python
from apscheduler.triggers.cron import CronTrigger

scheduler.reschedule_job(
    str(schedule_id),
    trigger=CronTrigger.from_crontab(new_cron_expression)
)

# Get updated next_run_time
job = scheduler.get_job(str(schedule_id))
schedule.next_run_time = job.next_run_time
```

**Best Practices**:
- Wrap reschedule in database transaction (rollback on failure)
- Validate cron expression before rescheduling (use croniter library)
- Handle past next_run_time by recalculating to next future occurrence
- Log schedule changes for audit trail

---

### 5. Structured Logging for Dispatcher Decisions

**Decision**: Use Python structlog with JSON formatter for dispatcher decision logs

**Rationale**:
- Structured logs enable automated analysis and metrics
- JSON format easily parsed by log aggregators
- Existing agent-server likely uses Python logging (compatible with structlog)
- Supports correlation IDs for request tracing

**Alternatives Considered**:
- **Plain text logs**: Rejected due to difficulty parsing for accuracy measurement
- **Separate database table for decisions**: Rejected due to write overhead and storage costs (logs are cheaper)
- **Metrics only (no logs)**: Rejected because debugging requires full decision context

**Implementation Pattern**:
```python
import structlog

logger = structlog.get_logger()

logger.info(
    "dispatcher_routing_decision",
    query=query_text,
    selected_tools=decision.selected_tools,
    selected_agents=decision.selected_agents,
    confidence=decision.confidence,
    reasoning=decision.reasoning,
    user_id=user_id,
    request_id=request_id,
    timestamp=datetime.utcnow().isoformat()
)
```

**Best Practices**:
- Use consistent log event name ("dispatcher_routing_decision")
- Include request_id for correlation across services
- Truncate query text to 1000 chars (prevent log bloat)
- Set log level to INFO (not DEBUG) for production
- Configure log rotation (daily, 30-day retention)

---

### 6. Soft Delete Pattern for Resources

**Decision**: Use `is_active` boolean flag for soft deletes across all resources

**Rationale**:
- Already established pattern in agent-server (per spec assumptions)
- Preserves audit trail and enables undelete
- Prevents cascading deletes that could break references
- Simple to implement and query

**Alternatives Considered**:
- **Hard delete with cascade**: Rejected due to data loss and referential integrity risks
- **Deleted_at timestamp**: Rejected because boolean is simpler and sufficient
- **Archive table**: Rejected due to schema duplication and query complexity

**Implementation Pattern**:
```python
# Soft delete
resource.is_active = False
session.commit()

# Query active resources
stmt = select(Resource).where(Resource.is_active.is_(True))
```

**Best Practices**:
- Check `is_active` in all queries (add to base query filters)
- Prevent delete if resource in use (check foreign key references)
- Return 404 for inactive resources (treat as not found)
- Consider periodic hard delete job for old inactive resources (>1 year)

---

### 7. Conflict Detection for In-Use Resources

**Decision**: Query foreign key relationships before delete; return 409 with details

**Rationale**:
- Prevents orphaned references
- User-friendly error messages guide resolution
- Follows REST conventions (409 Conflict)

**Alternatives Considered**:
- **Database foreign key constraints**: Rejected because soft delete doesn't trigger constraints
- **Cascade soft delete**: Rejected because it could unintentionally deactivate many resources
- **Allow delete and handle at runtime**: Rejected due to runtime errors and poor UX

**Implementation Pattern**:
```python
# Check if tool in use
stmt = select(AgentDefinition).where(
    AgentDefinition.is_active.is_(True),
    AgentDefinition.tools.contains({"names": [tool.name]})
)
agents_using_tool = session.execute(stmt).scalars().all()

if agents_using_tool:
    raise HTTPException(
        status_code=409,
        detail={
            "error": "resource_in_use",
            "message": "Tool is in use by active agents",
            "agents": [{"id": str(a.id), "name": a.name} for a in agents_using_tool]
        }
    )
```

**Best Practices**:
- Include list of dependent resources in error response
- Check all foreign key relationships (agents, workflows, schedules)
- Use database transactions to prevent race conditions
- Document conflict resolution in API docs

---

### 8. FastAPI REST Conventions

**Decision**: Follow standard REST patterns for CRUD endpoints with proper HTTP status codes

**Rationale**:
- Consistency with existing agent-server API
- Industry standard conventions
- Client-friendly and predictable

**Endpoint Patterns**:
```
GET    /agents/tools/{tool_id}        → 200 OK, 404 Not Found
PUT    /agents/tools/{tool_id}        → 200 OK, 400 Bad Request, 403 Forbidden, 404 Not Found
DELETE /agents/tools/{tool_id}        → 204 No Content, 403 Forbidden, 404 Not Found, 409 Conflict

GET    /agents/workflows/{workflow_id} → 200 OK, 404 Not Found
PUT    /agents/workflows/{workflow_id} → 200 OK, 400 Bad Request, 404 Not Found, 409 Conflict
DELETE /agents/workflows/{workflow_id} → 204 No Content, 404 Not Found, 409 Conflict

GET    /agents/evals/{eval_id}        → 200 OK, 404 Not Found
PUT    /agents/evals/{eval_id}        → 200 OK, 400 Bad Request, 404 Not Found
DELETE /agents/evals/{eval_id}        → 204 No Content, 404 Not Found

GET    /runs/schedule/{schedule_id}   → 200 OK, 404 Not Found
PUT    /runs/schedule/{schedule_id}   → 200 OK, 400 Bad Request, 404 Not Found

POST   /runs/workflow/{run_id}/resume → 202 Accepted, 400 Bad Request, 404 Not Found, 409 Conflict
```

**Status Code Guidelines**:
- **200 OK**: Successful GET/PUT with response body
- **204 No Content**: Successful DELETE with no response body
- **400 Bad Request**: Invalid request payload (validation errors)
- **403 Forbidden**: Authenticated but not authorized (built-in resource modification)
- **404 Not Found**: Resource doesn't exist or user doesn't have access
- **409 Conflict**: Resource state conflict (in use, invalid state transition)

**Best Practices**:
- Use Pydantic models for request/response validation
- Return detailed error messages in response body
- Include resource ID in success responses
- Use 404 (not 403) for unauthorized access to hide resource existence

---

### 9. Workflow Resume State Preservation

**Decision**: Store workflow state as JSONB in run_records with step outputs

**Rationale**:
- Enables resume from failure point without re-execution
- JSONB provides flexible schema for different workflow types
- Same pattern as definition snapshots (consistent approach)

**Alternatives Considered**:
- **Separate workflow_state table**: Rejected due to 1:1 relationship with run_records
- **Reconstruct state from logs**: Rejected due to unreliability and complexity
- **No state preservation (re-run from start)**: Acceptable fallback but reduces user value

**Implementation Pattern**:
```python
# During workflow execution
workflow_state = {
    "completed_steps": [
        {"step_id": "step1", "output": {...}, "completed_at": "..."},
        {"step_id": "step2", "output": {...}, "completed_at": "..."}
    ],
    "failed_step": "step3",
    "failure_reason": "..."
}

run_record.workflow_state = workflow_state

# On resume
new_run = RunRecord(
    parent_run_id=original_run.id,
    resume_from_step="step3",
    workflow_state=original_run.workflow_state,  # Inherit state
    ...
)
```

**Schema Additions** (Phase 2/3):
```sql
ALTER TABLE run_records ADD COLUMN parent_run_id UUID REFERENCES run_records(id);
ALTER TABLE run_records ADD COLUMN resume_from_step VARCHAR(255);
ALTER TABLE run_records ADD COLUMN workflow_state JSONB;
CREATE INDEX idx_run_records_parent ON run_records (parent_run_id);
```

**Best Practices**:
- Validate workflow_state structure before resume
- Check workflow definition hasn't changed (use snapshot)
- Limit resume to failed runs only (status = "failed")
- Document state format for each workflow type

---

### 10. Performance Optimization Strategies

**Decision**: Implement caching, connection pooling, and query optimization for scale targets

**Rationale**:
- 100-500 concurrent users requires efficient resource usage
- 1000 queries/hour dispatcher load benefits from caching
- <2s dispatcher response time requires optimization

**Optimization Strategies**:

1. **Dispatcher Query Caching** (Redis):
   - Cache routing decisions for identical queries (1-hour TTL)
   - Key: hash(query + user_enabled_tools + user_enabled_agents)
   - Reduces LLM calls by 30-50% for common queries

2. **Database Connection Pooling** (SQLAlchemy):
   - Pool size: 20 connections (2x CPU cores)
   - Max overflow: 10 connections
   - Pool timeout: 30 seconds
   - Recycle connections every 3600 seconds

3. **Query Optimization**:
   - Add indexes: (is_builtin, created_by), (is_active), (parent_run_id)
   - Use `select_in` loading for relationships (avoid N+1 queries)
   - Paginate list endpoints (default 50, max 200)

4. **Async Processing**:
   - Use FastAPI async endpoints for I/O-bound operations
   - Async LiteLLM calls for dispatcher
   - Async database queries with asyncpg

**Best Practices**:
- Monitor query performance with pg_stat_statements
- Set up connection pool metrics (Prometheus)
- Implement circuit breaker for LLM calls (fail fast on timeout)
- Use database read replicas for list queries (if needed at scale)

---

## Technology Stack Summary

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Web Framework | FastAPI | 0.104+ | API endpoints, async support |
| ORM | SQLAlchemy | 2.0+ | Database access, query building |
| Database | PostgreSQL | 15+ | Data persistence, JSONB support |
| LLM Gateway | LiteLLM | Latest | Unified LLM interface |
| Dispatcher Model | Claude 3.5 Sonnet | Via LiteLLM | Query routing decisions |
| AI Framework | Pydantic AI | Latest | Structured LLM outputs |
| Scheduler | APScheduler | 3.10+ | Cron-based scheduling |
| Validation | Pydantic | 2.0+ | Request/response schemas |
| Logging | structlog | Latest | Structured JSON logging |
| Caching | Redis | 7.0+ | Dispatcher query cache |
| Testing | pytest | 7.0+ | Unit/integration tests |
| Migrations | Alembic | 1.12+ | Database schema changes |

---

## Implementation Phases

### Phase 1 (P1 - Critical)
- Personal agent filtering (FR-001 to FR-004)
- Dispatcher agent (FR-005 to FR-012-OBS)
- Individual retrieval endpoints (FR-013, FR-020, FR-026, FR-030)
- Database migration: is_builtin flag, definition_snapshot column

### Phase 2 (P2 - Important)
- Full CRUD endpoints (FR-014 to FR-019, FR-021 to FR-025, FR-027 to FR-029, FR-031 to FR-034)
- Version isolation implementation (FR-019-ISO)
- Schedule management (FR-031 to FR-034)
- Conflict detection and soft delete

### Phase 3 (P3 - Optional)
- Workflow resume (FR-035 to FR-039)
- Database migration: parent_run_id, resume_from_step, workflow_state columns
- Bulk operations (out of scope for initial implementation)

---

## Open Questions

None - all technical decisions resolved during research phase.

---

## References

- FastAPI Documentation: https://fastapi.tiangolo.com/
- SQLAlchemy 2.0 Documentation: https://docs.sqlalchemy.org/en/20/
- Pydantic AI Documentation: https://ai.pydantic.dev/
- APScheduler Documentation: https://apscheduler.readthedocs.io/
- PostgreSQL JSONB: https://www.postgresql.org/docs/current/datatype-json.html
- structlog Documentation: https://www.structlog.org/

---

**Status**: Research complete, ready for Phase 1 (Design & Contracts)





