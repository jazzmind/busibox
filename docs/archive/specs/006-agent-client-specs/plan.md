# Implementation Plan: Agent-Server API Enhancements

**Branch**: `006-agent-manager-specs` | **Date**: 2025-12-11 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/006-agent-manager-specs/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Extend the Busibox agent-server with full CRUD operations for tools, workflows, and evaluators; implement personal agent filtering with ownership-based access control; create an intelligent dispatcher agent for query routing; add schedule management capabilities; and optionally support workflow resume from failure points. The implementation enhances the existing FastAPI/SQLAlchemy/PostgreSQL stack with snapshot-based version isolation, structured logging for dispatcher decisions, and support for 100-500 concurrent users processing 1000 queries/hour.

## Technical Context

**Language/Version**: Python 3.11+ (existing agent-server stack)  
**Primary Dependencies**: FastAPI, SQLAlchemy, Pydantic, LiteLLM, APScheduler (all already integrated)  
**Storage**: PostgreSQL 15+ with existing schema (agent_definitions, tool_definitions, workflow_definitions, eval_definitions, scheduled_runs, run_records)  
**Testing**: pytest with existing test infrastructure  
**Target Platform**: Linux LXC container (agent-lxc, CTID 207) on Proxmox host
**Project Type**: Backend API service (extends existing agent-server)  
**Performance Goals**: <2s dispatcher response time for 95% of queries at 1000 queries/hour; <500ms for CRUD operations  
**Constraints**: 100-500 concurrent users; up to 1000 total agents/tools/workflows; snapshot-based version isolation for running agents  
**Scale/Scope**: Medium scale deployment; 39 new functional requirements (FR-001 to FR-039); 3-phase implementation (P1: critical, P2: important, P3: optional)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Infrastructure as Code ✅ PASS

**Requirement**: All infrastructure defined as code, no manual configuration

**Status**: COMPLIANT
- Database schema changes documented in spec (ALTER TABLE statements)
- Will be implemented via Ansible role updates in `provision/ansible/roles/agent/`
- No manual container modifications required

### II. Service Isolation & Role-Based Security ✅ PASS

**Requirement**: Service isolation, RBAC enforcement, RLS for multi-tenant data

**Status**: COMPLIANT
- Personal agent filtering enforces ownership-based access control (FR-001, FR-002)
- Built-in resources immutable for all users including admins (FR-016)
- Dispatcher respects user permissions and enabled/disabled settings (FR-010)
- PostgreSQL RLS already in place for multi-tenant isolation

### III. Observability & Debuggability ✅ PASS

**Requirement**: Structured logs, health endpoints, traceable operations

**Status**: COMPLIANT
- Dispatcher decision logging with structured data (FR-012-OBS): query, selections, confidence, reasoning, timestamp, user_id
- Critical operations (CRUD, routing decisions) will be logged
- Existing health endpoints remain in place

### IV. Extensibility & Modularity ✅ PASS

**Requirement**: Easy addition of new services, loosely coupled design

**Status**: COMPLIANT
- Full CRUD operations enable extensibility (custom tools, workflows, evaluators)
- Dispatcher agent uses existing LiteLLM integration (no new dependencies)
- Snapshot-based version isolation decouples running agents from definition updates
- Idempotent Ansible roles for deployment

### V. Test-Driven Infrastructure ✅ PASS

**Requirement**: Infrastructure changes validated before deployment

**Status**: COMPLIANT
- Success criteria include specific test targets (SC-002: 95%+ routing accuracy)
- Smoke tests required for all new endpoints
- Integration tests for dispatcher routing accuracy
- Rollback procedures documented (soft deletes preserve audit trail)

### VI. Documentation as Contract ✅ PASS

**Requirement**: Documentation kept in sync with code

**Status**: COMPLIANT
- API contracts will be documented in OpenAPI format (Phase 1)
- Data model documented in data-model.md (Phase 1)
- Quickstart guide for development setup (Phase 1)
- Schema changes documented in spec and will be in migration scripts

### VII. Simplicity & Pragmatism ✅ PASS

**Requirement**: Boring tech, avoid premature optimization

**Status**: COMPLIANT
- Uses existing tech stack (FastAPI, SQLAlchemy, PostgreSQL, LiteLLM, APScheduler)
- No new dependencies or services required
- Snapshot approach for version isolation (simple, no complex version history infrastructure)
- Sequential dispatcher execution (no premature parallelization)

**OVERALL**: ✅ ALL GATES PASS - Ready for Phase 0 research

## Project Structure

### Documentation (this feature)

```
specs/006-agent-manager-specs/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command) ✅
├── data-model.md        # Phase 1 output (/speckit.plan command) ✅
├── quickstart.md        # Phase 1 output (/speckit.plan command) ✅
├── contracts/           # Phase 1 output (/speckit.plan command) ✅
│   └── openapi.yaml    # OpenAPI 3.0 specification
├── checklists/
│   └── requirements.md  # Specification quality checklist ✅
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```
srv/agent/                           # Existing agent-server codebase
├── app/
│   ├── agents/
│   │   ├── dispatcher.py           # NEW: Dispatcher agent implementation
│   │   └── __init__.py
│   ├── api/
│   │   ├── routes/
│   │   │   ├── agents.py          # MODIFY: Add personal agent filtering
│   │   │   ├── tools.py           # NEW: Individual tool CRUD endpoints
│   │   │   ├── workflows.py       # NEW: Individual workflow CRUD endpoints
│   │   │   ├── evals.py           # NEW: Individual evaluator CRUD endpoints
│   │   │   ├── schedules.py       # MODIFY: Add schedule CRUD endpoints
│   │   │   └── runs.py            # NEW: Workflow resume endpoint
│   │   └── __init__.py
│   ├── models/
│   │   ├── agent.py               # MODIFY: Add is_builtin field
│   │   ├── tool.py                # MODIFY: Add is_builtin, version fields
│   │   ├── workflow.py            # MODIFY: Add version field
│   │   ├── evaluator.py           # MODIFY: Add version field
│   │   ├── run.py                 # MODIFY: Add parent_run_id, resume_from_step, workflow_state
│   │   ├── dispatcher_log.py      # NEW: Dispatcher decision logging model
│   │   └── __init__.py
│   ├── services/
│   │   ├── dispatcher_service.py  # NEW: Dispatcher routing logic
│   │   ├── version_isolation.py   # NEW: Snapshot capture for running agents
│   │   └── __init__.py
│   └── schemas/
│       ├── dispatcher.py           # NEW: Pydantic schemas for dispatcher I/O
│       ├── tool.py                 # MODIFY: Add update/delete schemas
│       ├── workflow.py             # MODIFY: Add update/delete schemas
│       ├── evaluator.py            # MODIFY: Add update/delete schemas
│       └── schedule.py             # MODIFY: Add update schemas
│
├── tests/
│   ├── unit/
│   │   ├── test_dispatcher.py     # NEW: Dispatcher unit tests
│   │   ├── test_version_isolation.py # NEW: Version isolation tests
│   │   └── test_crud_endpoints.py # NEW: CRUD endpoint tests
│   ├── integration/
│   │   ├── test_dispatcher_routing.py # NEW: End-to-end dispatcher tests
│   │   ├── test_personal_agents.py    # NEW: Multi-user access control tests
│   │   └── test_schedule_updates.py   # NEW: APScheduler integration tests
│   └── fixtures/
│       └── dispatcher_queries.json # NEW: Test query dataset for accuracy measurement
│
├── alembic/
│   └── versions/
│       └── XXXX_add_agent_enhancements.py # NEW: Database migration script
│
└── pyproject.toml                  # MODIFY: Add any new dev dependencies (if needed)

provision/ansible/roles/agent/      # Ansible role for agent-lxc deployment
├── tasks/
│   └── main.yml                    # MODIFY: Add migration execution task
├── templates/
│   └── agent.env.j2                # MODIFY: Add any new env vars (if needed)
└── files/
    └── migrations/                 # NEW: Migration scripts for deployment
```

**Structure Decision**: This is a backend API extension to the existing agent-server in `srv/agent/`. The structure follows the established FastAPI pattern with models, schemas, routes, and services. New functionality is added as new modules (dispatcher, version isolation) and modifications to existing modules (agent filtering, CRUD endpoints). Database changes are managed via Alembic migrations deployed through Ansible.

## Complexity Tracking

*No constitution violations - this section intentionally left empty.*

---

## Implementation Phases Summary

### Phase 0: Research ✅ COMPLETE

**Deliverables**:
- ✅ `research.md` - Technology decisions, implementation patterns, best practices
- ✅ All NEEDS CLARIFICATION items resolved

**Key Decisions**:
1. Pydantic AI Agent with Claude 3.5 Sonnet for dispatcher routing
2. Snapshot-based version isolation using JSONB in run_records
3. SQLAlchemy ORM filters for personal agent filtering
4. APScheduler `reschedule_job()` for schedule updates
5. structlog with JSON formatter for dispatcher decision logging
6. Soft delete pattern with `is_active` boolean
7. Conflict detection via foreign key relationship queries
8. Standard REST conventions for all CRUD endpoints
9. JSONB workflow_state for resume capability
10. Redis caching, connection pooling, query optimization for performance

### Phase 1: Design & Contracts ✅ COMPLETE

**Deliverables**:
- ✅ `data-model.md` - Entity changes, relationships, validation rules, migration scripts
- ✅ `contracts/openapi.yaml` - OpenAPI 3.0 specification for all new endpoints
- ✅ `quickstart.md` - Developer setup, testing, deployment, troubleshooting guide
- ✅ Agent context updated (`.cursor/rules/specify-rules.mdc`)

**Artifacts Generated**:
1. **Data Model**: 7 entity changes (AgentDefinition, ToolDefinition, WorkflowDefinition, EvalDefinition, ScheduledRun, RunRecord, DispatcherDecisionLog)
2. **API Contracts**: 13 new endpoints across 6 resource types
3. **Database Migrations**: Phase 1 (13 schema changes) and Phase 2/3 (3 schema changes for workflow resume)
4. **Quickstart Guide**: Complete development, testing, deployment, and troubleshooting procedures

### Phase 2: Task Breakdown ⏳ PENDING

**Next Step**: Run `/speckit.tasks` to generate implementation tasks

**Expected Deliverables**:
- `tasks.md` - Detailed implementation tasks with dependencies, estimates, and acceptance criteria
- Task breakdown by phase (P1: critical, P2: important, P3: optional)
- Testing strategy and validation procedures

---

## Planning Status

**Status**: ✅ **PLANNING COMPLETE** (Phases 0-1)

**Ready For**:
- `/speckit.tasks` - Generate implementation task breakdown
- Development team handoff
- Sprint planning and estimation

**Not Ready For**:
- Implementation (requires task breakdown first)

---

## Next Steps

1. **Run `/speckit.tasks`** to generate detailed implementation tasks
2. **Review generated artifacts**:
   - Validate data model against existing schema
   - Review OpenAPI spec for completeness
   - Test quickstart procedures in development environment
3. **Sprint Planning**:
   - Estimate tasks from tasks.md
   - Assign to development team
   - Set up test environment
4. **Implementation**:
   - Follow task breakdown in tasks.md
   - Use quickstart.md for development setup
   - Reference research.md for implementation patterns
   - Validate against contracts/openapi.yaml

---

**Planning Completed**: 2025-12-11  
**Total Artifacts**: 5 documents (plan, research, data-model, quickstart, openapi)  
**Constitution Gates**: All passed ✅  
**Ready for Implementation**: After task breakdown
