# Tasks: Production-Grade Agent Server with Pydantic AI

**Branch**: 005-i-want-to  
**Spec**: [spec.md](./spec.md)  
**Plan**: [plan.md](./plan.md)

## Phase 1 — Setup

- **T001**: Create/validate Python env for agent server (Python 3.11+, uv/venv) [P]
- **T002**: Install project deps from `srv/agent/pyproject.toml` (FastAPI, Pydantic AI, SQLAlchemy async, APScheduler, OTel, sse-starlette) [P]
- **T003**: Ensure `.env` template exists/updated with auth, DB, service URLs, Redis, OTel [P]
- **T004**: Verify base FastAPI app boots locally (`uvicorn app.main:app --reload`) with `/health` returning 200

## Phase 2 — Foundational (blocking)

- **T005**: Apply initial DB schema from `srv/agent/app/db/schema.sql` to dev DB (agents, tools, workflows, evals, rag, runs, token_grants)
- **T006**: Implement auth middleware/deps for JWT validation (Busibox JWKS) in `app/auth/tokens.py` and `dependencies.py`; add unit tests
- **T007**: Implement OAuth2 token exchange + caching in `token_service.py` and `token_grants` usage; add unit tests
- **T008**: Implement Busibox HTTP client with token forwarding (`clients/busibox.py`); add unit tests (mock HTTPX)
- **T009**: Wire structured logging + OTel initialization (tracing hooks for requests and agent executions)

## Phase 3 — US1 (P1) Execute Core Agent with Tool Calls

**Goal**: Execute an agent with search/ingest/RAG tools; stream status/output; persist run state.

**Independent Test**: POST `/runs` with prompt → SSE shows status transitions → final GET `/runs/{id}` returns output + events; downstream tool calls hit mocked Busibox services.

- **T010**: Define/validate core Pydantic AI agents and outputs (`agents/core.py`) — chat & rag agents with tools
- **T011**: Implement tool adapters (search, ingest, rag) using Busibox client; add unit tests (mock HTTP)
- **T012**: Implement run service execution flow (`services/run_service.py`): create run, token exchange, execute agent, persist output/events/status
- **T013**: Implement `/runs` POST endpoint (accept input, return RunRead) and `/runs/{id}` GET; add integration tests
- **T014**: Implement SSE stream `/streams/runs/{id}`; add integration test with polling DB changes
- **T015**: Enforce tiered execution limits (Simple 30s/512MB, Complex 5m/2GB, Batch 30m/4GB) in run execution; add timeout tests
- **T016**: Add logging + tracing for run lifecycle and tool calls; verify spans/log fields in tests (smoke)
- **Checkpoint**: US1 executable end-to-end with mocks (runs API + SSE + persistence)

## Phase 4 — US2 (P2) Create and Manage Dynamic Agents

**Goal**: Admins can create/update/list active agents stored in DB and loaded into registry.

**Independent Test**: POST `/agents/definitions` → persists; registry refresh loads agent; `/agents` lists active; executing the new agent works.

- **T017**: Implement agent definition CRUD (create/update/list) in `api/agents.py` with validation; add integration tests
- **T018**: Implement dynamic loader + registry refresh (`agents/dynamic_loader.py`, `services/agent_registry.py`); add unit tests
- **T019**: Add registry refresh on startup and manual trigger (e.g., after create); ensure thread/async safety
- **T020**: Validate tool references against registry on create/update; add negative tests
- **Checkpoint**: Dynamic agent created, loaded, and executable via `/runs`

## Phase 5 — US3 (P3) Schedule Long-Running Agent Tasks

**Goal**: Admins schedule cron-based agent runs with token refresh and persisted results.

**Independent Test**: POST `/runs/schedule` with cron → job registered → run executes at schedule with fresh token → run result stored.

- **T021**: Implement scheduler service (`services/scheduler.py`) using APScheduler with async jobs
- **T022**: Implement `/runs/schedule` endpoint and persist schedule metadata (reuse run records for results); add integration test (short cron, e.g., every minute)
- **T023**: Add token pre-refresh before scheduled execution; add unit test
- **T024**: Add cancel API or config for schedules (if supported in scope) and verify disable behavior
- **Checkpoint**: Scheduled job executes and stores run output

## Phase 6 — US4 (P3) Define and Execute Workflows

**Goal**: Define multi-step workflows (sequential/branching) and execute with state persistence.

**Independent Test**: Create workflow with 2–3 steps; execute; verify steps run in order, outputs chained, state saved in run events.

- **T025**: Extend workflow model handling in `WorkflowDefinition` and validation of steps/branches
- **T026**: Implement workflow execution engine (sequential + simple branching) leveraging agents/tools; persist step events
- **T027**: Add workflow endpoints (create/list if needed) or reuse existing agents API if in scope; add integration tests
- **Checkpoint**: Workflow run with multiple steps completes with persisted events

## Phase 7 — US5 (P4) Evaluate Agent Performance with Scorers

**Goal**: Configure scorers and compute/store scores for completed runs with aggregations.

**Independent Test**: Define scorer; run scorer on completed run; scores persisted; aggregated metrics returned.

- **T028**: Implement scorer definitions CRUD (if required) and validation
- **T029**: Implement scorer execution against RunRecords; persist scores (extend RunRecord or new table if needed)
- **T030**: Add aggregation endpoint or query for scores (avg/min/max/percentiles); add integration tests
- **Checkpoint**: Scorer run produces stored scores and aggregates

## Phase 8 — Polish & Cross-Cutting

- **T031**: Harden error handling: tool call failures, DB retries, timeout messaging
- **T032**: Add rate limiting/config for runs and streams (if required by ops policy)
- **T033**: OTel exporter configuration + sampling controls; ensure trace/span IDs in logs
- **T034**: Documentation sweep: update README, quickstart, OpenAPI annotations; ensure spec/plan alignment
- **T035**: Deployment validation: Ansible role for local_src deploy, systemd service, health checks (test `make agent` for test/prod)

## Dependencies & Order

1) Foundational (Phase 1–2) → required before any user story
2) US1 (P1) → unlocks execution flows
3) US2 (P2) → depends on US1 (execution) for validating dynamic agents
4) US3 (P3) → depends on US1 (execution) and token refresh
5) US4 (P3) → depends on US1 (execution) and registry
6) US5 (P4) → depends on US1 (runs) and optional workflows
7) Polish after all stories

## Parallelization Examples

- Phase 1: T001–T003 in parallel
- Phase 2: T006 (auth) and T008 (Busibox client) can run in parallel after T005
- US1: T010/T011 (agents + tools) in parallel; T012–T015 sequential
- US2: T017 (CRUD) and T018 (loader) in parallel; T019–T020 after
- US3: T021 and T023 in parallel; T022 after scheduler ready
- US4: T025 and T027 in parallel; T026 after model validation
- US5: T028 and T030 in parallel; T029 after scorer definitions

## MVP Scope (Recommended)

- Deliver US1 (agent execution + SSE + persistence) as first deployable increment
- Includes auth, token exchange, core agents/tools, runs API, streaming, and observability

