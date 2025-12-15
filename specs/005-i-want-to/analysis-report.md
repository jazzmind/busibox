# Specification Analysis Report: Production-Grade Agent Server

**Feature**: 005-i-want-to  
**Date**: 2025-01-08  
**Analysis Type**: Cross-artifact consistency + existing codebase gap analysis

## Executive Summary

**Checklist Status**: ✅ PASSED (1/1 checklists complete)

| Checklist | Total | Completed | Incomplete | Status |
|-----------|-------|-----------|------------|--------|
| requirements.md | 12 | 12 | 0 | ✅ PASS |

**Implementation Status**: 🟡 PARTIAL - Core skeleton exists, needs production hardening

**Critical Findings**: 3  
**High Priority Findings**: 8  
**Medium Priority Findings**: 6  
**Low Priority Findings**: 4

**Overall Assessment**: The codebase has a solid foundation with basic agent execution, dynamic definitions, and auth scaffolding. However, significant gaps exist in error handling, testing, observability, workflow orchestration, and production-grade features. Approximately 60% of requirements have partial implementation; 40% need new development.

## Findings

| ID | Category | Severity | Location(s) | Summary | Recommendation |
|----|----------|----------|-------------|---------|----------------|
| C1 | Coverage | CRITICAL | FR-006, run_service.py | Tiered execution limits not enforced | Add timeout/memory enforcement with asyncio.wait_for() and resource monitoring |
| C2 | Coverage | CRITICAL | FR-005, run_service.py:39-44 | No error handling for tool call failures | Wrap agent.run() in try/except, persist partial results, log errors |
| C3 | Coverage | CRITICAL | FR-033-FR-035, tests/ | Only 1 smoke test exists; 90%+ coverage required | Add unit/integration/e2e test suites per tasks.md |
| H1 | Coverage | HIGH | FR-011, api/agents.py | No PUT endpoint for updating agent definitions | Add PUT /agents/definitions/{id} with version increment |
| H2 | Coverage | HIGH | FR-012-FR-013, api/agents.py | No deactivation endpoint or inactive agent check | Add PATCH /agents/definitions/{id}/deactivate and validate is_active in run_service |
| H3 | Coverage | HIGH | FR-017, auth/dependencies.py | Scope-based access control not enforced | Add scope validation in get_principal() and per-endpoint checks |
| H4 | Coverage | HIGH | FR-022, api/runs.py | No cancel endpoint for scheduled jobs | Add DELETE /runs/schedule/{id} to remove APScheduler jobs |
| H5 | Coverage | HIGH | FR-024-FR-028, workflows/ | Workflow execution engine missing | Build workflow executor with step orchestration, branching, retry logic |
| H6 | Coverage | HIGH | FR-029-FR-032, No scorer files | Scorer execution and aggregation not implemented | Add services/scorer_service.py and api/scorers.py |
| H7 | Coverage | HIGH | FR-036, FR-038, utils/logging.py | OTel tracing not wired | Add FastAPIInstrumentor, create spans for agent/tool calls |
| H8 | Coverage | HIGH | FR-004, agents/dynamic_loader.py:32-35 | Tool validation happens but errors not surfaced | Add validation in create_agent_definition endpoint before persist |
| M1 | Underspec | MEDIUM | FR-023, scheduler.py | Scheduler failure handling not implemented | Add try/except in _job(), log failures, implement retry policy |
| M2 | Underspec | MEDIUM | FR-027, No workflow state | Workflow resume/retry state not persisted | Extend RunRecord or create WorkflowState table |
| M3 | Underspec | MEDIUM | FR-018, scheduler.py | Token rotation before expiry not implemented | Add pre-execution token freshness check in scheduled jobs |
| M4 | Ambiguity | MEDIUM | FR-010, dynamic_loader.py:32 | Tool registry validation silently skips unknown tools | Should raise error or log warning for unknown tools |
| M5 | Coverage | MEDIUM | Edge case: SSE disconnect | No handling for client disconnect mid-stream | Add disconnect detection in streams.py event_generator |
| M6 | Coverage | MEDIUM | Edge case: Concurrent mods | No optimistic locking on agent updates | Add version check in PUT endpoint or use DB row locking |
| L1 | Inconsistency | LOW | settings.py:4 | Import uses BaseSettings (Pydantic v1) | Should be from pydantic_settings import BaseSettings (v2) |
| L2 | Inconsistency | LOW | domain.py:30 | Uses datetime.utcnow (deprecated) | Use datetime.now(timezone.utc) for timezone-aware timestamps |
| L3 | Documentation | LOW | README.md | Missing OTel configuration examples | Add OTEL_EXPORTER_OTLP_ENDPOINT to env docs |
| L4 | Documentation | LOW | api/runs.py:34 | /schedule endpoint not documented in OpenAPI | Add request/response models and OpenAPI annotations |

## Coverage Analysis

### Requirements Coverage Summary

| Requirement | Status | Task IDs | Implementation Files | Notes |
|-------------|--------|----------|---------------------|-------|
| FR-001 | 🟢 DONE | T010-T012 | agents/core.py, clients/busibox.py, run_service.py | Core execution works; needs error handling |
| FR-002 | 🟢 DONE | T012 | models/domain.py, run_service.py | Run persistence works; events array populated |
| FR-003 | 🟢 DONE | T014 | api/streams.py | SSE streaming implemented; needs disconnect handling |
| FR-004 | 🟡 PARTIAL | T010, T020 | dynamic_loader.py:32 | Validation exists but errors not surfaced to API |
| FR-005 | 🔴 MISSING | T015 | run_service.py | No try/except for tool failures; needs graceful handling |
| FR-006 | 🔴 MISSING | T015 | run_service.py | No timeout or memory enforcement |
| FR-007 | 🟢 DONE | T017 | api/agents.py:37-47 | Create agent endpoint works |
| FR-008 | 🟢 DONE | T017 | models/domain.py:16-33 | Agent persistence with versioning |
| FR-009 | 🟢 DONE | T018-T019 | services/agent_registry.py, main.py:30-36 | Registry loads on startup |
| FR-010 | 🟡 PARTIAL | T020 | dynamic_loader.py:32-35 | Tool validation exists but silent |
| FR-011 | 🔴 MISSING | T017 | api/agents.py | No PUT endpoint for updates |
| FR-012 | 🔴 MISSING | T017 | api/agents.py | No deactivation endpoint |
| FR-013 | 🔴 MISSING | T012 | run_service.py | No is_active check before execution |
| FR-014 | 🟢 DONE | T006 | auth/tokens.py:59-76 | JWT validation via JWKS |
| FR-015 | 🟢 DONE | T007 | auth/tokens.py:79-106 | OAuth2 token exchange |
| FR-016 | 🟢 DONE | T007 | services/token_service.py | Token caching in DB |
| FR-017 | 🔴 MISSING | T006 | auth/dependencies.py | No scope enforcement |
| FR-018 | 🔴 MISSING | T023 | scheduler.py | No token rotation logic |
| FR-019 | 🟢 DONE | T022 | api/runs.py:34-52, services/scheduler.py | Schedule endpoint exists |
| FR-020 | 🟡 PARTIAL | T023 | scheduler.py:32-40 | Scheduled runs execute but no token freshness check |
| FR-021 | 🟡 PARTIAL | T022 | api/runs.py | Schedule metadata not persisted (only in APScheduler) |
| FR-022 | 🔴 MISSING | T024 | api/runs.py | No cancel endpoint |
| FR-023 | 🔴 MISSING | T021 | scheduler.py | No error handling or retry in _job() |
| FR-024 | 🔴 MISSING | T025 | workflows/ | No workflow definition CRUD beyond baseline |
| FR-025 | 🔴 MISSING | T026 | workflows/ | No workflow execution engine |
| FR-026 | 🔴 MISSING | T026 | workflows/ | No branching logic |
| FR-027 | 🔴 MISSING | T026 | workflows/ | No workflow state persistence |
| FR-028 | 🔴 MISSING | T026 | workflows/ | No retry policy support |
| FR-029 | 🟡 PARTIAL | T028 | api/agents.py:96-116 | Eval CRUD exists but no execution |
| FR-030 | 🔴 MISSING | T029 | No scorer files | Scorer execution not implemented |
| FR-031 | 🔴 MISSING | T030 | No scorer files | Score aggregation not implemented |
| FR-032 | 🔴 MISSING | T030 | No scorer files | Threshold alerts not implemented |
| FR-033 | 🔴 MISSING | T006-T035 | tests/ | Only 1 test exists; need unit tests for all modules |
| FR-034 | 🔴 MISSING | T013-T014 | tests/ | No integration tests |
| FR-035 | 🔴 MISSING | T016 | tests/ | No e2e tests |
| FR-036 | 🟡 PARTIAL | T009, T016 | utils/logging.py | Basic logging setup; needs structured fields, tool call logging |
| FR-037 | 🟢 DONE | T004 | api/health.py | Health endpoint exists |
| FR-038 | 🔴 MISSING | T009, T016 | utils/logging.py | OTel tracing not configured |

**Coverage Metrics**:
- Total Requirements: 38
- Fully Implemented: 10 (26%)
- Partially Implemented: 7 (18%)
- Missing: 21 (55%)
- Requirements with Tasks: 38 (100%)

### Task Coverage Summary

All 35 tasks in tasks.md map to requirements. No orphaned tasks detected.

**Completed Tasks** (from existing code):
- ✅ T001-T004: Setup (Python env, deps, .env, FastAPI boots)
- ✅ T005: DB schema (schema.sql exists)
- ✅ T006: Auth middleware (tokens.py, dependencies.py)
- ✅ T007: Token exchange + caching (token_service.py)
- ✅ T008: Busibox client (clients/busibox.py)
- ✅ T010: Core agents (agents/core.py with chat_agent, rag_agent)
- ✅ T011: Tool adapters (search_tool, ingest_tool, rag_tool)
- ✅ T012: Run service (run_service.py basic execution)
- ✅ T013: /runs endpoints (runs.py POST and GET via run_service)
- ✅ T014: SSE stream (streams.py)
- ✅ T017: Agent CRUD partial (create + list; missing update)
- ✅ T018: Dynamic loader (dynamic_loader.py, agent_registry.py)
- ✅ T019: Registry refresh (main.py startup)
- ✅ T021: Scheduler service (scheduler.py with APScheduler)
- ✅ T022: Schedule endpoint (runs.py:34-52)

**Incomplete/Missing Tasks**:
- 🔴 T009: OTel initialization (logging.py exists but no OTel)
- 🔴 T015: Tiered execution limits (no timeout enforcement)
- 🔴 T016: Logging + tracing for runs (basic logging only)
- 🔴 T020: Tool validation on create (validation exists but errors not surfaced)
- 🔴 T023: Token pre-refresh for scheduled runs
- 🔴 T024: Cancel scheduled jobs
- 🔴 T025-T027: Workflow execution engine (only baseline stub)
- 🔴 T028-T030: Scorer implementation (CRUD exists, execution missing)
- 🔴 T031-T035: Polish tasks (error handling, rate limiting, docs)

## Constitution Alignment

### ✅ PASSING

- **I. Infrastructure as Code**: Code in `/srv/agent`, deployed via Ansible local_src
- **II. Service Isolation**: Runs in agent-lxc, JWT auth, token forwarding
- **III. Observability**: Health endpoint exists, logging setup present
- **IV. Extensibility**: Dynamic definitions, tool registry pattern
- **VI. Documentation**: README exists, needs enhancement
- **VII. Simplicity**: Standard stack (FastAPI, SQLAlchemy, APScheduler)

### ⚠️ VIOLATIONS

- **V. Test-Driven Infrastructure**: Only 1 test exists; 90%+ coverage required (CRITICAL)
  - **Impact**: Cannot validate production readiness
  - **Remediation**: Implement T006-T035 test tasks

## Gap Analysis: Existing Code vs Specification

### What's Already Implemented ✅

**Core Infrastructure (60% complete)**:
- FastAPI app with CORS, startup hooks, routers
- Pydantic Settings with env-based config
- SQLAlchemy async session + models (Agent, Tool, Workflow, Eval, Run, Token, RAG)
- DB schema DDL with indexes
- JWT validation via JWKS with caching
- OAuth2 token exchange with DB caching
- Busibox HTTP client with token forwarding
- Core Pydantic AI agents (chat, RAG) with tools (search, ingest, rag)
- Dynamic agent loader + in-memory registry
- Agent CRUD (create + list) and tool/workflow/eval CRUD
- Run execution service (basic)
- SSE streaming for run status
- APScheduler for cron jobs
- Schedule endpoint

### What Needs to Be Built 🔴

**Error Handling & Resilience (0% complete)**:
- Tool call failure handling with partial results
- Execution timeout enforcement (tiered limits)
- Database retry logic for transient failures
- SSE disconnect handling
- Scheduler error handling and retry
- Optimistic locking for concurrent updates

**Missing Endpoints**:
- PUT /agents/definitions/{id} (update agent)
- PATCH /agents/definitions/{id}/deactivate (deactivate agent)
- DELETE /runs/schedule/{id} (cancel schedule)
- PUT /agents/tools/{id} (update tool)
- PUT /agents/workflows/{id} (update workflow)
- GET /runs/{id} (missing from runs.py, only POST exists)

**Workflow Engine (0% complete)**:
- Sequential step execution with output passing
- Conditional branching logic
- Retry policies per step
- Workflow state persistence for resume
- Workflow execution API integration

**Scorer System (10% complete)**:
- Scorer execution against runs (CRUD exists, execution missing)
- Score persistence (extend RunRecord or new table)
- Aggregation queries (avg/min/max/percentiles)
- Threshold-based alerting

**Testing (5% complete)**:
- Unit tests for auth, agents, loaders, run service (0/20+ tests)
- Integration tests for API endpoints (0/10+ tests)
- E2E tests for user journeys (0/5+ tests)
- Contract tests for Busibox service mocks (0/3+ tests)
- Coverage reporting and CI integration

**Observability (20% complete)**:
- OpenTelemetry instrumentation (FastAPI, agent execution, tool calls)
- Structured logging with trace context
- Span creation for agent runs and tool calls
- Log aggregation and search
- Metrics (execution time, success rate, tool call latency)

**Production Hardening (10% complete)**:
- Input validation and sanitization (agent instructions, tool args)
- Rate limiting per user/endpoint
- Resource monitoring and enforcement
- Token encryption at rest
- Admin-only endpoint protection
- Comprehensive error messages

### What Needs to Change 🔧

**Existing Code Issues**:

1. **settings.py:4** - Wrong import: `from pydantic import BaseSettings` → should be `from pydantic_settings import BaseSettings`

2. **domain.py:30, 48, 63, 77, 92, 107, 126, 139** - Deprecated `datetime.utcnow()` → use `datetime.now(timezone.utc)`

3. **run_service.py:39-44** - No error handling:
   ```python
   # Current (unsafe):
   result = await agent.run(payload.get("prompt"), deps=deps)
   run_record.status = "succeeded"
   
   # Needs (safe):
   try:
       result = await asyncio.wait_for(
           agent.run(payload.get("prompt"), deps=deps),
           timeout=get_timeout(agent_tier)
       )
       run_record.status = "succeeded"
   except asyncio.TimeoutError:
       run_record.status = "timeout"
   except Exception as e:
       run_record.status = "failed"
       run_record.output = {"error": str(e)}
   ```

4. **agents/core.py:30-33** - Agents have `model=None` → should use settings.default_model or per-agent model

5. **api/agents.py** - Missing endpoints:
   - PUT /definitions/{id} for updates
   - PATCH /definitions/{id}/deactivate for soft delete
   - Same for tools, workflows, evals

6. **api/runs.py** - Missing GET /runs/{id} endpoint (only POST exists)

7. **services/scheduler.py:32-40** - No error handling in `_job()` function

8. **auth/dependencies.py:7-15** - No scope validation, only token validation

9. **main.py:30-36** - No error handling if registry refresh fails on startup

10. **No .gitignore** - Python project needs .gitignore for `__pycache__/`, `.venv/`, `*.pyc`, etc.

## Metrics

- **Total Requirements**: 38
- **Total Tasks**: 35
- **Coverage**: 100% (all requirements have tasks)
- **Implementation**: 60% partial, 40% missing
- **Critical Issues**: 3 (testing, error handling, execution limits)
- **High Issues**: 8 (missing endpoints, workflow engine, scorers, OTel)
- **Medium Issues**: 6 (edge cases, token rotation, validation)
- **Low Issues**: 4 (imports, deprecations, docs)

## Next Actions

### Immediate (Required for MVP/US1)

1. **Fix critical imports and deprecations** (L1, L2) - 5 minutes
2. **Add .gitignore for Python** - 2 minutes
3. **Implement error handling in run_service** (C2) - 30 minutes
4. **Add tiered execution timeouts** (C1) - 45 minutes
5. **Add GET /runs/{id} endpoint** (missing from spec but needed) - 15 minutes
6. **Add unit tests for auth, agents, run service** (C3) - 2-3 hours
7. **Add integration tests for /runs, /agents, /streams** (C3) - 2-3 hours

### High Priority (US2-US3)

8. **Add PUT /agents/definitions/{id}** (H1) - 30 minutes
9. **Add deactivation endpoint + check** (H2) - 45 minutes
10. **Implement scope-based access control** (H3) - 1 hour
11. **Add cancel schedule endpoint** (H4) - 30 minutes
12. **Add token rotation for scheduled runs** (M3) - 45 minutes
13. **Implement OTel instrumentation** (H7) - 2 hours

### Medium Priority (US4-US5)

14. **Build workflow execution engine** (H5) - 4-6 hours
15. **Implement scorer execution** (H6) - 2-3 hours
16. **Add e2e tests** (C3) - 2-3 hours

### Low Priority (Polish)

17. **Add rate limiting** - 1 hour
18. **Enhance documentation** (L3, L4) - 1 hour
19. **Add optimistic locking** (M6) - 1 hour

## Remediation Plan

Would you like me to:

1. **Fix immediate issues** (imports, .gitignore, error handling, timeouts) - ~2 hours
2. **Complete US1 (P1)** with full testing - ~6 hours total
3. **Implement US2-US3** (dynamic agents + scheduling) - ~4 hours
4. **Build US4-US5** (workflows + scorers) - ~8 hours
5. **Full production hardening** (all tasks) - ~20 hours

**Recommended Approach**: Start with #1 (immediate fixes) + #2 (US1 completion) to get a production-ready MVP, then incrementally add US2-US5 based on priority.







