# Research: Production-Grade Agent Server with Pydantic AI

**Feature**: 005-i-want-to  
**Date**: 2025-01-08  
**Purpose**: Document technology decisions, best practices, and patterns for implementing the agent server

## Technology Decisions

### 1. Pydantic AI Framework

**Decision**: Use Pydantic AI 0.0.20+ as the core agent framework

**Rationale**:
- Type-safe agent definitions with Pydantic validation
- Built-in tool registration and dependency injection via `RunContext`
- Structured output validation (agents return typed Pydantic models)
- Model-agnostic design (supports OpenAI, Anthropic, local models via adapters)
- Streaming support for real-time responses
- Integrates naturally with FastAPI (both use Pydantic)

**Alternatives Considered**:
- **LangChain**: More mature but heavier, less type-safe, more complex abstractions
- **LlamaIndex**: Focused on RAG, less suitable for general agent orchestration
- **Custom implementation**: Would require rebuilding tool calling, streaming, validation

**Implementation Notes**:
- Agents defined with `Agent[DepsType, OutputType]` generic pattern
- Tools registered via `@agent.tool` decorator with async functions
- Dynamic instructions via `@agent.instructions` decorator
- Dependency injection through `RunContext[BusiboxDeps]` for auth/clients

### 2. FastAPI for API Layer

**Decision**: Use FastAPI 0.115+ with async/await throughout

**Rationale**:
- Native async support matches Pydantic AI's async execution model
- Automatic OpenAPI schema generation for API contracts
- Pydantic integration for request/response validation
- Built-in dependency injection for auth, DB sessions
- SSE support via `sse-starlette` for streaming run updates
- High performance (Starlette ASGI server)

**Alternatives Considered**:
- **Flask**: Synchronous by default, less type-safe, older ecosystem
- **Django**: Too heavy for API-only service, ORM conflicts with SQLAlchemy

**Implementation Notes**:
- Use `APIRouter` for modular endpoint organization
- Async route handlers with `async def`
- Dependency injection via `Depends()` for auth, DB sessions
- Middleware for CORS, logging, auth validation

### 3. SQLAlchemy 2.0 Async for Database

**Decision**: Use SQLAlchemy 2.0+ with asyncpg driver

**Rationale**:
- Async ORM matches FastAPI's async patterns (no thread pool blocking)
- Type-safe queries with 2.0 style (`select()`, `Mapped` annotations)
- Supports PostgreSQL-specific features (JSONB, UUID)
- Mature migration story (Alembic) though we use raw DDL initially
- Connection pooling and session management built-in

**Alternatives Considered**:
- **Raw asyncpg**: Lower-level, more boilerplate, no ORM benefits
- **Tortoise ORM**: Less mature, smaller ecosystem
- **Prisma (Python)**: Still experimental, less PostgreSQL feature support

**Implementation Notes**:
- Use `DeclarativeBase` for model definitions
- `Mapped[T]` type hints for columns
- `async_sessionmaker` for session factory
- JSONB columns for flexible schema (tools, workflow steps, events)
- UUID primary keys for distributed-friendly IDs

### 4. APScheduler for Cron Scheduling

**Decision**: Use APScheduler 3.10+ with AsyncIOScheduler

**Rationale**:
- Native async support for agent execution
- Cron expression parsing built-in
- In-process scheduling (no external dependencies)
- Persistent job store optional (can use PostgreSQL if needed)
- Integrates with FastAPI startup/shutdown lifecycle

**Alternatives Considered**:
- **Celery**: Requires Redis/RabbitMQ broker, heavier, more complex
- **Dramatiq**: Similar to Celery, adds external dependency
- **Custom cron**: Would require parsing, scheduling logic, state management

**Implementation Notes**:
- Use `AsyncIOScheduler` for async job execution
- Parse cron expressions via `CronTrigger`
- Store schedule metadata in PostgreSQL (separate from APScheduler)
- Handle job failures with retry policies
- Refresh tokens before scheduled execution

### 5. OpenTelemetry for Observability

**Decision**: Use OpenTelemetry SDK 1.27+ with OTLP exporter

**Rationale**:
- Industry-standard observability (traces, metrics, logs)
- Vendor-neutral (works with any OTLP-compatible backend)
- FastAPI instrumentation available (`opentelemetry-instrumentation-fastapi`)
- Distributed tracing for multi-service agent executions
- Structured logging with trace context injection

**Alternatives Considered**:
- **Prometheus client only**: Metrics-only, no tracing
- **Custom logging**: Would miss distributed tracing, no standard format
- **Datadog/New Relic SDKs**: Vendor lock-in

**Implementation Notes**:
- Auto-instrument FastAPI with `FastAPIInstrumentor`
- Create custom spans for agent execution, tool calls
- Export to OTLP endpoint (configurable via env)
- Structured logging with trace/span IDs injected

### 6. Token Exchange Pattern

**Decision**: OAuth2 client-credentials flow with token caching

**Rationale**:
- Standard OAuth2 pattern for service-to-service auth
- Scoped tokens minimize blast radius (search.read, ingest.write, rag.query)
- Caching reduces auth service load
- Automatic expiry and rotation
- Supports long-running/scheduled jobs

**Alternatives Considered**:
- **Pass-through user token**: Expires too quickly for scheduled jobs
- **Service account with static key**: No scoping, higher security risk
- **JWT refresh tokens**: More complex, not standard for service auth

**Implementation Notes**:
- Exchange user JWT → downstream token via Busibox authz OAuth2 endpoint
- Cache tokens in `token_grants` table with expiry tracking
- Check cache before exchange (avoid unnecessary requests)
- Rotate tokens 60 seconds before expiry for scheduled jobs
- Include `requested_subject` and `requested_purpose` in exchange payload

## Best Practices

### Agent Design Patterns

**Tool Registry Pattern**:
- Maintain whitelist of allowed tools in `TOOL_REGISTRY` dict
- Dynamic agents reference tools by name, not code
- Prevents arbitrary code execution from DB-stored definitions
- Example: `TOOL_REGISTRY = {"search": search_tool, "ingest": ingest_tool}`

**Dependency Injection**:
- Use `RunContext[BusiboxDeps]` to pass auth, clients to tools
- Enables testing with mocked dependencies
- Keeps tool functions pure (no global state)
- Example: `async def search_tool(ctx: RunContext[BusiboxDeps], query: str)`

**Structured Outputs**:
- Define Pydantic models for agent outputs (not free-form text)
- Enables validation, type safety, downstream processing
- Example: `Agent[BusiboxDeps, ChatOutput]` where `ChatOutput` is a Pydantic model

### Error Handling

**Tool Call Failures**:
- Catch exceptions in tool functions, return error messages
- Log tool failures with context (agent ID, run ID, tool name, args)
- Return partial results when possible (e.g., search returns 0 hits instead of crashing)
- Don't expose internal errors to LLM (sanitize messages)

**Execution Timeouts**:
- Use `asyncio.wait_for()` with tiered limits
- Cancel tasks on timeout, persist partial results
- Log timeout events for analysis
- Return user-friendly timeout messages

**Database Failures**:
- Retry transient errors (connection failures) with exponential backoff
- Fail fast on schema errors (missing columns, constraint violations)
- Use transactions for multi-step operations (create agent + refresh registry)
- Log all DB errors with query context

### Testing Strategy

**Unit Tests** (fast, isolated):
- Test business logic without external dependencies
- Mock DB sessions, HTTP clients, auth services
- Focus on: token validation, agent loading, run state transitions
- Use `pytest` fixtures for common mocks

**Integration Tests** (medium speed, real DB):
- Test API endpoints with real database (test DB instance)
- Mock external services (Busibox search/ingest/RAG)
- Focus on: CRUD operations, auth flows, SSE streams
- Use `httpx.AsyncClient` for API calls

**E2E Tests** (slow, full stack):
- Test complete user journeys with mocked Busibox services
- Focus on: agent execution with tools, scheduled runs, workflows
- Verify observability (logs, traces) are generated
- Run against staging environment before production

**Coverage Goals**:
- 90%+ overall coverage (FR-033, SC-005)
- 100% coverage for auth, token exchange (security-critical)
- 80%+ for agent execution, tool calls (complex logic)
- Exclude generated code (migrations, schemas) from coverage

### Performance Optimization

**Connection Pooling**:
- Use SQLAlchemy connection pool (default 5-10 connections)
- Configure pool size based on expected concurrency
- Monitor pool exhaustion (log warnings when pool full)

**Token Caching**:
- Cache downstream tokens in DB with expiry
- Check cache before exchange (avoid redundant OAuth2 calls)
- Evict expired tokens on access (lazy cleanup)
- Consider in-memory cache (Redis) if DB becomes bottleneck

**Agent Registry**:
- Load agents into memory on startup (avoid DB query per execution)
- Refresh registry on-demand (admin API endpoint)
- Use async locks to prevent concurrent refresh conflicts
- Consider TTL-based auto-refresh if definitions change frequently

**Streaming Optimization**:
- Use SSE for run status updates (lower overhead than WebSockets)
- Poll DB every 1-2 seconds for status changes (balance freshness vs load)
- Close streams when run completes (avoid resource leaks)
- Limit concurrent streams per user (prevent abuse)

## Integration Patterns

### Busibox Service Integration

**Search API** (`http://milvus-lxc:8003`):
- POST `/search` with `{query, top_k}` → returns `{hits: [{id, score, content}]}`
- Requires downstream token with `search.read` scope
- Timeout: 30 seconds (configurable)
- Error handling: Return empty results on failure, log error

**Ingest API** (`http://ingest-lxc:8002`):
- POST `/documents` with `{path, metadata}` → returns `{document_id, status}`
- Requires downstream token with `ingest.write` scope
- Timeout: 60 seconds (long-running)
- Error handling: Retry transient failures (429, 503), fail on 4xx

**RAG API** (`http://milvus-lxc:8003`):
- POST `/databases/{db}/query` with `{query, top_k}` → returns `{results: [{text, metadata}]}`
- Requires downstream token with `rag.query` scope
- Timeout: 30 seconds
- Error handling: Return empty results on failure, log error

### Workflow Orchestration

**Sequential Steps**:
- Execute steps in order, pass outputs to next step
- Store step results in run events array
- Fail workflow on step failure (unless retry policy defined)
- Example: `[ingest_step, embed_step, summarize_step]`

**Conditional Branching**:
- Evaluate conditions based on step outputs
- Use simple JSON path expressions (e.g., `$.status == "success"`)
- Support if/else branches (not complex DAGs initially)
- Example: `if $.ingest.status == "success" then embed_step else error_step`

**Retry Policies**:
- Define max retries and backoff strategy per step
- Exponential backoff: 1s, 2s, 4s, 8s, ...
- Persist retry attempts in run events
- Fail workflow after max retries exceeded

## Security Considerations

### Input Validation

**Agent Instructions**:
- Sanitize instructions to prevent prompt injection
- Limit instruction length (e.g., 10,000 characters)
- Validate against known malicious patterns (SQL injection attempts, etc.)
- Log all instruction changes for audit

**Tool Arguments**:
- Validate tool arguments against Pydantic schemas
- Reject invalid types, missing required fields
- Sanitize string inputs (escape special characters)
- Rate-limit tool calls per agent (prevent abuse)

### Token Security

**Storage**:
- Store downstream tokens encrypted in DB (use `pgcrypto` or application-level encryption)
- Never log token values (mask in logs)
- Rotate tokens before expiry (60s buffer)
- Revoke tokens on user logout or permission change

**Transmission**:
- Use HTTPS for all external API calls (search/ingest/RAG)
- Include tokens in `Authorization: Bearer` header (not query params)
- Set short timeouts to prevent token leakage via long-running requests

### Execution Isolation

**Resource Limits**:
- Enforce tiered memory limits via process monitoring
- Kill processes exceeding memory limit (prevent OOM)
- Log resource violations for analysis
- Consider containerization per agent execution (future)

**Sandboxing**:
- No arbitrary code execution from DB-stored definitions
- Tool registry whitelist prevents unknown tool calls
- Validate all dynamic content (instructions, tool args) before execution

## Open Questions

*None - all technical decisions resolved during research phase.*


