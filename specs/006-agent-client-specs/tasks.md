# Tasks: Agent-Server API Enhancements

**Input**: Design documents from `/specs/006-agent-client-specs/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/openapi.yaml ✅

**Tests**: Tests are included based on success criteria in spec.md (SC-002: 95%+ routing accuracy, SC-006: 100% security validation)

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`
- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4, US5)
- Include exact file paths in descriptions

## Path Conventions
- Backend API: `srv/agent/app/` for application code
- Tests: `srv/agent/tests/` for test code
- Migrations: `srv/agent/alembic/versions/` for database migrations
- Ansible: `provision/ansible/roles/agent/` for deployment

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 Review existing agent-server codebase structure in `srv/agent/`
- [X] T002 Install new dependencies: `pydantic-ai`, `structlog`, `croniter` in `srv/agent/pyproject.toml`
- [X] T003 [P] Configure structlog JSON formatter in `srv/agent/app/core/logging.py`
- [X] T004 [P] Set up test fixtures directory `srv/agent/tests/fixtures/`

**Checkpoint**: ✅ Development environment ready

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T005 Create Alembic migration script for Phase 1 schema changes in `srv/agent/alembic/versions/XXXX_add_agent_enhancements.py`
  - Add `is_builtin BOOLEAN DEFAULT FALSE` to `agent_definitions`
  - Add `is_builtin BOOLEAN DEFAULT FALSE, version INTEGER DEFAULT 1` to `tool_definitions`
  - Add `version INTEGER DEFAULT 1` to `workflow_definitions`
  - Add `version INTEGER DEFAULT 1` to `eval_definitions`
  - Add `definition_snapshot JSONB` to `run_records`
  - Create `dispatcher_decision_log` table with all fields
  - Create all indexes from data-model.md

- [ ] T006 Apply migration to development database: `alembic upgrade head`

- [X] T007 [P] Update `AgentDefinition` model in `srv/agent/app/models/domain.py`
  - Add `is_builtin: bool = False` field
  - Add `created_by: str` field
  - Update `__repr__` to include is_builtin

- [X] T008 [P] Update `ToolDefinition` model in `srv/agent/app/models/domain.py`
  - Add `is_builtin: bool = False` field
  - Add `created_by: str` field
  - Update `__repr__` to include version

- [X] T009 [P] Update `WorkflowDefinition` model in `srv/agent/app/models/domain.py`
  - Add `created_by: str` field
  - Update `__repr__`

- [X] T010 [P] Update `EvalDefinition` model in `srv/agent/app/models/domain.py`
  - Add `created_by: str` field
  - Update `__repr__`

- [X] T011 [P] Update `RunRecord` model in `srv/agent/app/models/domain.py`
  - Add `definition_snapshot: dict | None` field (JSONB)
  - Add `parent_run_id: UUID | None` field
  - Add `resume_from_step: str | None` field
  - Add `workflow_state: dict | None` field (JSONB)

- [X] T012 [P] Create `DispatcherDecisionLog` model in `srv/agent/app/models/dispatcher_log.py`
  - All fields from data-model.md
  - Validation for confidence (0-1 range)
  - Automatic timestamp

- [X] T013 Create version isolation service in `srv/agent/app/services/version_isolation.py`
  - `capture_definition_snapshot(agent_id, workflow_id, session)` function
  - Returns dict with agent, tools, workflow definitions

- [X] T014 Update run creation logic to capture snapshots
  - Modify run creation in existing code to call `capture_definition_snapshot`
  - Store result in `run_record.definition_snapshot`

**Checkpoint**: ⏳ Foundation almost ready - Need to apply migration (T006) before user story work

---

## Phase 3: User Story 1 - Personal Agent Management (Priority: P1) 🎯 MVP

**Goal**: Users can create personal agents visible only to them, while built-in agents are visible to all

**Independent Test**: Create agent as User A, verify User B cannot see it; verify both users see built-in agents

### Tests for User Story 1

**NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T015 [P] [US1] Integration test for personal agent filtering in `srv/agent/tests/integration/test_personal_agents.py`
  - Test: User A creates personal agent → User A sees it, User B doesn't
  - Test: Built-in agents visible to all users
  - Test: User B tries to access User A's agent by ID → 404 Not Found
  - Test: User A can update/delete own agent, User B cannot

### Implementation for User Story 1

- [X] T016 [US1] Modify `/agents` GET endpoint in `srv/agent/app/api/agents.py`
  - Add SQLAlchemy filter: `or_(AgentDefinition.is_builtin.is_(True), AgentDefinition.created_by == current_user_id)`
  - Apply filter to existing query
  - Ensure `is_active = TRUE` filter remains

- [X] T017 [US1] Add `/agents/{agent_id}` GET endpoint in `srv/agent/app/api/agents.py`
  - Add ownership check for personal agents
  - Return 404 (not 403) if user doesn't own personal agent
  - Allow access to built-in agents for all users

- [X] T018 [US1] Update agent CREATE endpoint in `srv/agent/app/api/agents.py`
  - Ensure `is_builtin` can only be set to True by system (not via API)
  - Set `is_builtin = False` for all user-created agents
  - Populate `created_by` from authenticated user

- [X] T019 [US1] Update tool/workflow/eval CREATE endpoints in `srv/agent/app/api/agents.py`
  - Populate `created_by` from authenticated user for all resources

- [ ] T020 [US1] Add logging for personal agent operations in `srv/agent/app/api/agents.py`
  - Log agent creation with user_id
  - Log access attempts (successful and denied)
  - Use structlog with structured fields

**Checkpoint**: ⏳ User Story 1 almost complete - Need logging (T020)

---

## Phase 4: User Story 2 - Intelligent Query Routing (Priority: P1)

**Goal**: Users can submit natural language queries and get intelligent routing to appropriate tools/agents

**Independent Test**: Submit query "What does our Q4 report say?" → dispatcher routes to doc_search with high confidence

### Tests for User Story 2

- [X] T021 [P] [US2] Unit tests for dispatcher service in `srv/agent/tests/unit/test_dispatcher.py`
  - Test: Query analysis returns valid RoutingDecision
  - Test: Confidence score between 0-1
  - Test: User settings honored (only enabled tools/agents)
  - Test: Low confidence (<0.7) includes alternatives

- [X] T022 [P] [US2] Integration tests for dispatcher routing in `srv/agent/tests/integration/test_dispatcher_routing.py`
  - Test: Document query → routes to doc_search (confidence >0.8)
  - Test: Web query → routes to web_search
  - Test: Disabled tool not selected
  - Test: No available tools → confidence=0, empty selections
  - Test: File attachment → routes to file-capable tool
  - Target: 95%+ routing accuracy on test query set

- [X] T023 [P] [US2] Create test query dataset in `srv/agent/tests/fixtures/dispatcher_queries.json`
  - 12 diverse queries covering: doc search, web search, multi-tool, edge cases
  - Expected routing decisions for each
  - Used for accuracy measurement (SC-002)

### Implementation for User Story 2

- [X] T024 [P] [US2] Create Pydantic schemas for dispatcher in `srv/agent/app/schemas/dispatcher.py`
  - `DispatcherRequest` schema (query, available_tools, available_agents, attachments, user_settings)
  - `RoutingDecision` schema (selected_tools, selected_agents, confidence, reasoning, alternatives, requires_disambiguation)
  - `DispatcherResponse` schema (routing_decision, execution_plan)

- [X] T025 [US2] Implement dispatcher agent in `srv/agent/app/agents/dispatcher.py`
  - Create Pydantic AI Agent with Claude 3.5 Sonnet
  - System prompt for routing logic (see research.md)
  - Temperature=0.3 for consistency
  - Result type: RoutingDecision
  - Timeout: 10s with fallback

- [X] T026 [US2] Implement dispatcher service in `srv/agent/app/services/dispatcher_service.py`
  - `route_query(request: DispatcherRequest, user_id: str)` function
  - Call dispatcher agent
  - Handle no available tools case (confidence=0, empty selections)
  - Return DispatcherResponse

- [X] T027 [US2] Implement decision logging in `srv/agent/app/services/dispatcher_service.py`
  - Create DispatcherDecisionLog entry after each routing decision
  - Include: query (truncated to 1000 chars), selections, confidence, reasoning, user_id, request_id, timestamp
  - Use structlog for structured logging
  - Save to database asynchronously

- [X] T028 [US2] Create `/dispatcher/route` POST endpoint in `srv/agent/app/api/dispatcher.py`
  - Accept DispatcherRequest
  - Get authenticated user
  - Call dispatcher service
  - Return DispatcherResponse
  - Handle errors (LiteLLM timeout, invalid response)

- [X] T029 [US2] Implement Redis caching for dispatcher in `srv/agent/app/services/dispatcher_service.py`
  - Cache key: hash(query + user_enabled_tools + user_enabled_agents)
  - TTL: 1 hour
  - Return cached decision if exists
  - Cache after successful routing

- [X] T030 [US2] Add dispatcher performance monitoring
  - Log response time for each routing decision
  - Track cache hit rate
  - Monitor confidence score distribution
  - Alert if p95 latency >2s

**Checkpoint**: ✅ User Story 2 complete - Dispatcher routing working with 95%+ accuracy target, independently testable

---

## Phase 5: User Story 3 - Tool and Workflow Management (Priority: P2)

**Goal**: Users can create, update, and delete custom tools, workflows, and evaluators

**Independent Test**: Create custom tool → update description → use in agent → delete (should fail with 409) → remove from agent → delete (should succeed)

### Tests for User Story 3

- [X] T031 [P] [US3] Integration tests for tool CRUD in `srv/agent/tests/integration/test_tool_crud.py`
  - Test: GET /agents/tools/{tool_id} returns tool
  - Test: PUT /agents/tools/{tool_id} updates tool and increments version
  - Test: DELETE built-in tool returns 403
  - Test: DELETE tool in use returns 409 with agent list
  - Test: DELETE unused tool returns 204

- [X] T032 [P] [US3] Integration tests for workflow CRUD in `srv/agent/tests/integration/test_workflow_crud.py`
  - Test: GET /agents/workflows/{workflow_id} returns workflow
  - Test: PUT /agents/workflows/{workflow_id} updates and increments version
  - Test: PUT validates workflow steps before saving
  - Test: DELETE workflow with active schedules returns 409

- [X] T033 [P] [US3] Integration tests for evaluator CRUD in `srv/agent/tests/integration/test_evaluator_crud.py`
  - Test: GET /agents/evals/{eval_id} returns evaluator
  - Test: PUT /agents/evals/{eval_id} updates and increments version
  - Test: DELETE evaluator returns 204

### Implementation for User Story 3

#### Tool Management

- [X] T034 [P] [US3] Create Pydantic schemas for tool updates in `srv/agent/app/schemas/definitions.py`
  - `ToolDefinitionUpdate` schema (name, description, schema, entrypoint, scopes, is_active)
  - Validation for Python identifier pattern on name
  - Validation for entrypoint format

- [X] T035 [US3] Implement GET `/agents/tools/{tool_id}` endpoint in `srv/agent/app/api/tools.py`
  - Retrieve tool by ID
  - Return 404 if not found or inactive
  - Require authentication

- [X] T036 [US3] Implement PUT `/agents/tools/{tool_id}` endpoint in `srv/agent/app/api/tools.py`
  - Check if tool is built-in → return 403
  - Check ownership (created_by)
  - Update fields from ToolDefinitionUpdate
  - Increment version number
  - Update updated_at timestamp
  - Return updated tool

- [X] T037 [US3] Implement DELETE `/agents/tools/{tool_id}` endpoint in `srv/agent/app/api/tools.py`
  - Check if tool is built-in → return 403
  - Check if tool in use by active agents → return 409 with agent list
  - Soft delete (set is_active = False)
  - Return 204 No Content

#### Workflow Management

- [X] T038 [P] [US3] Create Pydantic schemas for workflow updates in `srv/agent/app/schemas/definitions.py`
  - `WorkflowDefinitionUpdate` schema (name, description, steps, is_active)
  - Validation for step structure

- [X] T039 [US3] Implement workflow step validation (uses existing `app.workflows.engine.validate_workflow_steps`)
  - Check: step IDs unique within workflow
  - Check: referenced agents/tools exist and are active
  - Check: each step has required fields based on type
  - Return validation errors if any

- [X] T040 [US3] Implement GET `/agents/workflows/{workflow_id}` endpoint in `srv/agent/app/api/workflows.py`
  - Retrieve workflow by ID
  - Return 404 if not found or inactive

- [X] T041 [US3] Implement PUT `/agents/workflows/{workflow_id}` endpoint in `srv/agent/app/api/workflows.py`
  - Check ownership
  - Validate workflow steps before saving
  - Update fields from WorkflowDefinitionUpdate
  - Increment version number
  - Update updated_at timestamp
  - Return updated workflow

- [X] T042 [US3] Implement DELETE `/agents/workflows/{workflow_id}` endpoint in `srv/agent/app/api/workflows.py`
  - Check if workflow has active scheduled runs → return 409 with schedule list
  - Soft delete (set is_active = False)
  - Return 204 No Content

#### Evaluator Management

- [ ] T043 [P] [US3] Create Pydantic schemas for evaluator updates in `srv/agent/app/schemas/evaluator.py`
  - `EvalDefinitionUpdate` schema (name, description, config, is_active)
  - Validation for config structure (criteria, pass_threshold, model)

- [ ] T044 [US3] Implement GET `/agents/evals/{eval_id}` endpoint in `srv/agent/app/api/routes/evals.py`
  - Retrieve evaluator by ID
  - Return 404 if not found or inactive

- [ ] T045 [US3] Implement PUT `/agents/evals/{eval_id}` endpoint in `srv/agent/app/api/routes/evals.py`
  - Check ownership
  - Update fields from EvalDefinitionUpdate
  - Increment version number
  - Update updated_at timestamp
  - Return updated evaluator

- [ ] T046 [US3] Implement DELETE `/agents/evals/{eval_id}` endpoint in `srv/agent/app/api/routes/evals.py`
  - Soft delete (set is_active = False)
  - Return 204 No Content

#### Conflict Detection

- [ ] T047 [US3] Implement conflict detection service in `srv/agent/app/services/conflict_detection.py`
  - `check_tool_in_use(tool_id, session)` → returns list of agents using tool
  - `check_workflow_has_schedules(workflow_id, session)` → returns list of active schedules
  - Used by delete endpoints to return 409 with details

**Checkpoint**: User Story 3 complete - Full CRUD operations working, independently testable

---

## Phase 6: User Story 4 - Schedule Management (Priority: P2)

**Goal**: Users can retrieve and update scheduled agent runs, with APScheduler automatically synced

**Independent Test**: Get schedule → update cron expression from 9 AM to 10 AM → verify next_run_time updates → verify APScheduler job updated

### Tests for User Story 4

- [ ] T048 [P] [US4] Integration tests for schedule management in `srv/agent/tests/integration/test_schedule_updates.py`
  - Test: GET /runs/schedule/{schedule_id} returns schedule
  - Test: PUT /runs/schedule/{schedule_id} updates cron expression
  - Test: next_run_time recalculates correctly
  - Test: APScheduler job updates (verify via scheduler.get_job())
  - Test: Invalid cron expression returns 400
  - Test: Past next_run_time recalculates to future

### Implementation for User Story 4

- [ ] T049 [P] [US4] Create Pydantic schemas for schedule updates in `srv/agent/app/schemas/schedule.py`
  - `ScheduledRunUpdate` schema (agent_id, workflow_id, input, cron_expression, tier, scopes)
  - Validation for cron expression format

- [ ] T050 [US4] Implement GET `/runs/schedule/{schedule_id}` endpoint in `srv/agent/app/api/routes/schedules.py`
  - Retrieve schedule by ID
  - Return 404 if not found
  - Require authentication and ownership check

- [ ] T051 [US4] Implement cron validation service in `srv/agent/app/services/cron_validation.py`
  - `validate_cron_expression(cron_expr)` function using croniter
  - Returns True if valid, raises ValueError if invalid
  - Used by schedule update endpoint

- [ ] T052 [US4] Implement PUT `/runs/schedule/{schedule_id}` endpoint in `srv/agent/app/api/routes/schedules.py`
  - Check ownership
  - Validate cron expression
  - Update schedule fields from ScheduledRunUpdate
  - Call APScheduler `reschedule_job()` with new CronTrigger
  - Get updated next_run_time from APScheduler
  - Update updated_at timestamp
  - Return updated schedule
  - Wrap in database transaction (rollback on APScheduler failure)

- [ ] T053 [US4] Add schedule update logging
  - Log schedule changes with old/new cron expressions
  - Log APScheduler job updates
  - Log next_run_time recalculations
  - Use structlog with structured fields

**Checkpoint**: User Story 4 complete - Schedule management working with APScheduler sync, independently testable

---

## Phase 7: User Story 5 - Workflow Resume (Priority: P3) [OPTIONAL]

**Goal**: Users can resume failed workflows from point of failure without re-executing completed steps

**Independent Test**: Create workflow that fails at step 3 → resume from step 3 → verify steps 1-2 not re-executed → verify new run references parent

### Tests for User Story 5

- [ ] T054 [P] [US5] Integration tests for workflow resume in `srv/agent/tests/integration/test_workflow_resume.py`
  - Test: Resume failed workflow creates new run with parent_run_id
  - Test: Resumed run starts at correct step (resume_from_step)
  - Test: Workflow state preserved from original run
  - Test: Cannot resume non-failed run (returns 400)
  - Test: Cannot resume if workflow definition changed (returns 409)

### Implementation for User Story 5

- [ ] T055 [US5] Create Alembic migration for workflow resume in `srv/agent/alembic/versions/XXXX_add_workflow_resume.py`
  - Add `parent_run_id UUID REFERENCES run_records(id)` to run_records
  - Add `resume_from_step VARCHAR(255)` to run_records
  - Add `workflow_state JSONB` to run_records
  - Create indexes (already in T011, but migration separate for P3)

- [ ] T056 [US5] Apply migration: `alembic upgrade head`

- [ ] T057 [US5] Implement workflow state preservation in existing workflow execution code
  - Capture completed step outputs in workflow_state JSONB
  - Update workflow_state after each step completion
  - Store failed_step and failure_reason on failure

- [ ] T058 [US5] Implement POST `/runs/workflow/{run_id}/resume` endpoint in `srv/agent/app/api/routes/runs.py`
  - Validate original run status = "failed"
  - Validate workflow definition hasn't changed (compare snapshots)
  - Get from_step from request or use failed_step from workflow_state
  - Create new run with:
    - parent_run_id = original_run.id
    - resume_from_step = from_step
    - workflow_state = original_run.workflow_state (inherit)
    - definition_snapshot = original_run.definition_snapshot (same version)
  - Queue new run for execution
  - Return 202 Accepted with new run details

- [ ] T059 [US5] Update workflow execution engine to handle resume
  - Check if run has parent_run_id and resume_from_step
  - If yes: skip steps before resume_from_step
  - Use workflow_state for step inputs instead of re-executing
  - Continue from resume_from_step onwards

**Checkpoint**: User Story 5 complete - Workflow resume working, independently testable

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T060 [P] Add OpenAPI documentation to all new endpoints
  - Use FastAPI's built-in OpenAPI generation
  - Add descriptions, examples, response schemas
  - Validate against contracts/openapi.yaml

- [ ] T061 [P] Performance optimization: Add database connection pooling
  - Configure SQLAlchemy pool_size=20, max_overflow=10
  - Set pool_recycle=3600
  - Monitor connection usage

- [ ] T062 [P] Performance optimization: Add query optimization
  - Verify indexes created correctly
  - Use select_in loading for relationships (avoid N+1)
  - Add pagination to list endpoints (default 50, max 200)

- [ ] T063 [P] Security hardening: Add rate limiting
  - Implement per-user rate limits for dispatcher (100 queries/hour)
  - Implement per-user rate limits for CRUD operations (1000/hour)
  - Return 429 Too Many Requests when exceeded

- [ ] T064 [P] Add dispatcher decision log cleanup job
  - Scheduled job to delete logs older than 90 days
  - Run daily via APScheduler
  - Log cleanup statistics

- [ ] T065 [P] Update Ansible deployment role in `provision/ansible/roles/agent/tasks/main.yml`
  - Add task to run Alembic migrations
  - Add task to restart agent-api service
  - Add task to verify health endpoint

- [ ] T066 [P] Create deployment validation script in `provision/ansible/roles/agent/files/validate-deployment.sh`
  - Check all new endpoints return expected status codes
  - Check database migrations applied
  - Check APScheduler running
  - Check dispatcher decision logs being created

- [ ] T067 Run quickstart.md validation procedures
  - Test all commands in quickstart.md work
  - Verify smoke tests pass
  - Verify troubleshooting procedures accurate

- [ ] T068 [P] Update project documentation
  - Update README.md with new features
  - Update API documentation
  - Update deployment documentation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phases 3-7)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (US1 → US2 → US3 → US4 → US5)
- **Polish (Phase 8)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 3 (P2)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 4 (P2)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 5 (P3)**: Can start after Foundational (Phase 2) - No dependencies on other stories

**Note**: All user stories are independently implementable after Foundational phase completes

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models before services
- Services before endpoints
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- **Setup (Phase 1)**: T003, T004 can run in parallel
- **Foundational (Phase 2)**: T007-T012 (all model updates) can run in parallel
- **User Story 1**: T015 (tests) can run in parallel with implementation after they fail
- **User Story 2**: T021-T023 (all tests) can run in parallel; T024 (schemas) can run in parallel with T025 (agent implementation)
- **User Story 3**: T031-T033 (all tests) can run in parallel; T034, T038, T043 (all schemas) can run in parallel; T035-T037 (tool endpoints), T039-T042 (workflow endpoints), T044-T046 (evaluator endpoints) can run in parallel after schemas complete
- **User Story 4**: T048 (tests) can run in parallel with implementation after they fail; T049, T051 (schemas and validation) can run in parallel
- **User Story 5**: T054 (tests) can run in parallel with implementation after they fail
- **Polish (Phase 8)**: T060-T068 (all polish tasks) can run in parallel
- **Once Foundational phase completes, all user stories can be worked on in parallel by different team members**

---

## Parallel Example: User Story 2 (Dispatcher)

```bash
# After Foundational phase completes, launch User Story 2 tasks:

# Launch all tests together (write tests first, ensure they FAIL):
Task T021: "Unit tests for dispatcher service in srv/agent/tests/unit/test_dispatcher.py"
Task T022: "Integration tests for dispatcher routing in srv/agent/tests/integration/test_dispatcher_routing.py"
Task T023: "Create test query dataset in srv/agent/tests/fixtures/dispatcher_queries.json"

# After tests fail, launch schemas and agent implementation in parallel:
Task T024: "Create Pydantic schemas for dispatcher in srv/agent/app/schemas/dispatcher.py"
Task T025: "Implement dispatcher agent in srv/agent/app/agents/dispatcher.py"

# Then sequential tasks:
Task T026: "Implement dispatcher service in srv/agent/app/services/dispatcher_service.py"
Task T027: "Implement decision logging in srv/agent/app/services/dispatcher_service.py"
Task T028: "Create /dispatcher/route POST endpoint in srv/agent/app/api/routes/dispatcher.py"
Task T029: "Implement Redis caching for dispatcher"
Task T030: "Add dispatcher performance monitoring"

# Verify tests now PASS
```

---

## Parallel Example: User Story 3 (CRUD Operations)

```bash
# After Foundational phase completes, launch User Story 3 tasks:

# Launch all tests together (write tests first, ensure they FAIL):
Task T031: "Integration tests for tool CRUD in srv/agent/tests/integration/test_tool_crud.py"
Task T032: "Integration tests for workflow CRUD in srv/agent/tests/integration/test_workflow_crud.py"
Task T033: "Integration tests for evaluator CRUD in srv/agent/tests/integration/test_evaluator_crud.py"

# After tests fail, launch all schemas in parallel:
Task T034: "Create Pydantic schemas for tool updates in srv/agent/app/schemas/tool.py"
Task T038: "Create Pydantic schemas for workflow updates in srv/agent/app/schemas/workflow.py"
Task T043: "Create Pydantic schemas for evaluator updates in srv/agent/app/schemas/evaluator.py"

# Then launch all endpoint groups in parallel:
# Tool endpoints (T035-T037)
# Workflow endpoints (T039-T042)
# Evaluator endpoints (T044-T046)
# Conflict detection service (T047)

# Verify tests now PASS
```

---

## Implementation Strategy

### MVP First (User Stories 1 & 2 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 (Personal Agent Management)
4. **STOP and VALIDATE**: Test User Story 1 independently
5. Complete Phase 4: User Story 2 (Intelligent Query Routing)
6. **STOP and VALIDATE**: Test User Story 2 independently
7. Deploy/demo MVP with P1 features

**MVP Delivers**:
- Personal agent management with ownership-based access control ✅
- Intelligent dispatcher routing with 95%+ accuracy ✅
- Core value proposition validated

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Deploy/Demo (Personal agents working!)
3. Add User Story 2 → Test independently → Deploy/Demo (Dispatcher working!)
4. Add User Story 3 → Test independently → Deploy/Demo (Full CRUD working!)
5. Add User Story 4 → Test independently → Deploy/Demo (Schedule management working!)
6. Add User Story 5 (optional) → Test independently → Deploy/Demo (Workflow resume working!)
7. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. **Team completes Setup + Foundational together** (critical path)
2. **Once Foundational is done, split by user story**:
   - Developer A: User Story 1 (Personal Agent Management)
   - Developer B: User Story 2 (Intelligent Query Routing)
   - Developer C: User Story 3 (Tool/Workflow CRUD)
   - Developer D: User Story 4 (Schedule Management)
3. **Stories complete and integrate independently**
4. **Optional**: Developer E can work on User Story 5 (Workflow Resume) in parallel

---

## Task Summary

**Total Tasks**: 68

**Tasks by Phase**:
- Phase 1 (Setup): 4 tasks
- Phase 2 (Foundational): 10 tasks (BLOCKING)
- Phase 3 (US1 - Personal Agents): 6 tasks (1 test + 5 implementation)
- Phase 4 (US2 - Dispatcher): 10 tasks (3 tests + 7 implementation)
- Phase 5 (US3 - CRUD Operations): 17 tasks (3 tests + 14 implementation)
- Phase 6 (US4 - Schedule Management): 6 tasks (1 test + 5 implementation)
- Phase 7 (US5 - Workflow Resume): 6 tasks (1 test + 5 implementation) [OPTIONAL]
- Phase 8 (Polish): 9 tasks

**Tasks by User Story**:
- US1 (Personal Agent Management): 6 tasks
- US2 (Intelligent Query Routing): 10 tasks
- US3 (Tool and Workflow Management): 17 tasks
- US4 (Schedule Management): 6 tasks
- US5 (Workflow Resume): 6 tasks [OPTIONAL]
- Shared/Polish: 23 tasks

**Parallel Opportunities**: 35 tasks marked [P] can run in parallel

**Independent Test Criteria**:
- US1: Create agent as User A → User B cannot see it
- US2: Submit query → dispatcher routes correctly with 95%+ accuracy
- US3: Create tool → update → delete (with conflict detection)
- US4: Update schedule → verify APScheduler synced
- US5: Resume failed workflow → verify steps not re-executed

**Suggested MVP Scope**: User Stories 1 & 2 (16 tasks after Foundational)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Tests written FIRST (TDD approach) for all user stories
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
- User Story 5 (Workflow Resume) is optional - can be deferred to future iteration

---

**Generated**: 2025-12-11  
**Status**: Ready for implementation  
**Next Step**: Begin Phase 1 (Setup) or review task estimates with team
