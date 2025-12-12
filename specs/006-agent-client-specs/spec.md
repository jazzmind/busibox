# Feature Specification: Agent-Server API Enhancements

**Feature Branch**: `006-agent-client-specs`  
**Created**: 2025-12-11  
**Status**: Draft  
**Input**: User description: "@agent-client/specs/001-agent-management-rebuild/agent-server-requirements.md we need to extend the agent server to meet these requirements. Let's create a new feature branch."

## Clarifications

### Session 2025-12-11

- Q: When the dispatcher cannot route to any enabled tool/agent (all down or none match), what should happen? → A: Return routing decision with confidence=0, empty selections, reasoning explaining no tools available, and suggest user check tool status or try again later
- Q: What observability requirements exist for dispatcher routing decisions? → A: Log each routing decision with query, selected tools/agents, confidence, reasoning, and timestamp for accuracy analysis and debugging
- Q: How should the system implement version isolation for tools/workflows used by running agents? → A: Running agents capture tool/workflow definitions at run start; updates only affect new runs (snapshot approach)
- Q: Can admin users modify or delete built-in tools/agents/workflows, or are they immutable for everyone? → A: Built-in resources are immutable for everyone including admins (must deploy code changes to modify)
- Q: What are the expected scale/capacity targets for the system? → A: Medium scale: 100-500 concurrent users, 1000 queries/hour, 1000 total agents/tools/workflows

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Personal Agent Management (Priority: P1)

As a user, I want to create and manage my own personal agents that only I can see and use, while still having access to built-in system agents that are available to everyone.

**Why this priority**: This is fundamental to multi-user agent management. Without proper filtering, users would see each other's personal agents, creating confusion and potential security issues.

**Independent Test**: Can be fully tested by creating a personal agent as User A, logging in as User B, and verifying User B cannot see User A's agent. Delivers immediate value by enabling secure personal agent creation.

**Acceptance Scenarios**:

1. **Given** I am logged in as User A, **When** I create a personal agent named "My Research Assistant", **Then** only I can see it in my agent list
2. **Given** I am logged in as any user, **When** I view the agent list, **Then** I see all built-in agents plus only my own personal agents
3. **Given** User B tries to access User A's personal agent via API, **When** they make the request, **Then** they receive a 404 Not Found error

---

### User Story 2 - Intelligent Query Routing (Priority: P1)

As a user, I want to ask questions in natural language and have the system automatically route my query to the most appropriate tools and agents based on my available resources and permissions.

**Why this priority**: This is the core user experience for the agent system. Without intelligent routing, users must manually select tools/agents for every query, creating friction and requiring technical knowledge.

**Independent Test**: Can be fully tested by submitting various query types (document search, web search, analysis) and verifying the dispatcher selects appropriate tools/agents. Delivers immediate value by simplifying the user experience.

**Acceptance Scenarios**:

1. **Given** I have doc_search enabled and ask "What does our Q4 report say about revenue?", **When** the dispatcher analyzes my query, **Then** it routes to doc_search with high confidence (>0.8)
2. **Given** I have both doc_search and web_search enabled and ask "What's the weather today?", **When** the dispatcher analyzes my query, **Then** it routes to web_search with reasoning explaining why
3. **Given** I have doc_search disabled in my settings and ask a document question, **When** the dispatcher analyzes my query, **Then** it does not route to doc_search and suggests alternatives
4. **Given** the dispatcher has low confidence (<0.7) about routing, **When** it returns the decision, **Then** it includes alternative suggestions and requires disambiguation

---

### User Story 3 - Tool and Workflow Management (Priority: P2)

As a user, I want to create, update, and delete custom tools, workflows, and evaluators to extend the system's capabilities for my specific needs.

**Why this priority**: This enables power users to customize the system but is not required for basic usage. Users can work with built-in tools/agents before creating custom ones.

**Independent Test**: Can be fully tested by creating a custom tool, updating its configuration, using it in an agent, and then deleting it. Delivers value by enabling system extensibility.

**Acceptance Scenarios**:

1. **Given** I create a custom tool, **When** I retrieve the tool list, **Then** I see my custom tool alongside built-in tools
2. **Given** I have a custom tool, **When** I update its description and schema, **Then** the changes are saved and the version increments
3. **Given** I try to delete a built-in tool, **When** I make the delete request, **Then** I receive a 403 Forbidden error
4. **Given** my custom tool is used by an active agent, **When** I try to delete it, **Then** I receive a 409 Conflict error with details about which agents use it
5. **Given** I have a custom tool not in use, **When** I delete it, **Then** it is soft-deleted (is_active = false) and no longer appears in my tool list

---

### User Story 4 - Schedule Management (Priority: P2)

As a user, I want to schedule agents to run automatically at specific times and be able to update or cancel those schedules as my needs change.

**Why this priority**: Scheduling is important for automation but not required for manual agent execution. Users can run agents on-demand before setting up schedules.

**Independent Test**: Can be fully tested by creating a schedule, updating its cron expression, verifying the next run time updates, and canceling the schedule. Delivers value by enabling automation.

**Acceptance Scenarios**:

1. **Given** I have a scheduled agent run, **When** I retrieve the schedule by ID, **Then** I see the schedule details including next run time
2. **Given** I want to change a schedule from 9 AM to 10 AM daily, **When** I update the cron expression, **Then** the APScheduler job updates and next_run_time recalculates
3. **Given** I update a schedule, **When** the next scheduled time arrives, **Then** the agent runs with the updated configuration
4. **Given** I try to delete a workflow with active schedules, **When** I make the delete request, **Then** I receive a 409 Conflict error

---

### User Story 5 - Workflow Resume (Priority: P3)

As a user, when a multi-step workflow fails partway through, I want to resume it from the point of failure instead of restarting from the beginning, preserving the work already completed.

**Why this priority**: This is a nice-to-have optimization that saves time and resources but is not critical. Users can re-run failed workflows from the start initially.

**Independent Test**: Can be fully tested by creating a workflow that fails at step 3, resuming from step 3 with corrected input, and verifying steps 1-2 are not re-executed. Delivers value by improving efficiency.

**Acceptance Scenarios**:

1. **Given** a workflow failed at step 3 of 5, **When** I request to resume from step 3, **Then** a new run is created that inherits state from steps 1-2 and starts at step 3
2. **Given** I resume a failed workflow, **When** the new run completes, **Then** I can see it references the original run via parent_run_id
3. **Given** a workflow run succeeded, **When** I try to resume it, **Then** I receive an error indicating only failed runs can be resumed

---

### Edge Cases

- What happens when a user tries to access another user's personal agent via direct API call with the agent ID? → Returns 404 Not Found (per FR-002)
- How does the system handle dispatcher routing when all of a user's enabled tools are unavailable or down? → Returns routing decision with confidence=0, empty selections, reasoning explaining unavailability, suggests checking tool status or retrying (per FR-012-DISP)
- What happens when a user updates a tool that is currently being used by a running agent? → Update succeeds; running agent continues with version it started with (version isolation)
- How does the system handle schedule updates when the next run time is in the past? → Recalculates next_run_time to next valid future occurrence based on cron expression
- What happens when a user tries to resume a workflow but the workflow definition has been updated since the original run? → Resume uses workflow definition version from original run (version isolation)
- How does the dispatcher handle queries with file attachments when no tools support file processing? → Returns low confidence decision with reasoning explaining no file-capable tools available, suggests alternatives
- What happens when a user deletes an agent that has scheduled runs? → Returns 409 Conflict with list of active schedules that must be deleted first

## Requirements *(mandatory)*

### Functional Requirements

#### Personal Agent Management

- **FR-001**: System MUST filter agent lists to show only built-in agents (is_builtin = true) and personal agents created by the authenticated user
- **FR-002**: System MUST prevent users from accessing, updating, or deleting personal agents they did not create
- **FR-003**: System MUST include an is_builtin flag on agent definitions to distinguish system agents from personal agents
- **FR-004**: System MUST populate created_by field for all agent definitions to track ownership

#### Dispatcher Agent

- **FR-005**: System MUST provide a dispatcher agent that analyzes natural language queries and selects appropriate tools and agents
- **FR-006**: Dispatcher MUST consider user's available tools, available agents, user permissions, and enabled/disabled settings when routing
- **FR-007**: Dispatcher MUST provide a confidence score (0-1) for routing decisions
- **FR-008**: Dispatcher MUST provide reasoning explaining why specific tools/agents were selected
- **FR-009**: Dispatcher MUST suggest alternative routing options when confidence is below 0.7
- **FR-010**: Dispatcher MUST strictly honor user settings and only route to enabled tools/agents
- **FR-011**: Dispatcher MUST handle file attachments by considering which tools support file processing
- **FR-012-DISP**: When no enabled tools/agents are available or all are down, dispatcher MUST return routing decision with confidence=0, empty selected_tools and selected_agents arrays, reasoning explaining unavailability, and alternatives suggesting user check tool status or retry later
- **FR-012-OBS**: System MUST log each dispatcher routing decision including query text, selected tools/agents, confidence score, reasoning, timestamp, and user ID for accuracy measurement and debugging

#### Tool Management

- **FR-013**: System MUST provide endpoint to retrieve individual tool by ID (GET /agents/tools/{tool_id})
- **FR-014**: System MUST provide endpoint to update custom tools (PUT /agents/tools/{tool_id})
- **FR-015**: System MUST provide endpoint to soft-delete custom tools (DELETE /agents/tools/{tool_id})
- **FR-016**: System MUST prevent modification or deletion of built-in tools for all users including admins (return 403 Forbidden); built-in resources can only be modified via code deployment
- **FR-017**: System MUST prevent deletion of tools that are in use by active agents (return 409 Conflict)
- **FR-018**: System MUST increment version number when tools are updated
- **FR-019**: System MUST update updated_at timestamp when tools are modified
- **FR-019-ISO**: System MUST capture tool/workflow definitions at agent run start time; running agents use captured definitions even if tools/workflows are updated during execution (snapshot isolation)

#### Workflow Management

- **FR-020**: System MUST provide endpoint to retrieve individual workflow by ID (GET /agents/workflows/{workflow_id})
- **FR-021**: System MUST provide endpoint to update workflows (PUT /agents/workflows/{workflow_id})
- **FR-022**: System MUST provide endpoint to soft-delete workflows (DELETE /agents/workflows/{workflow_id})
- **FR-023**: System MUST validate workflow steps before saving updates
- **FR-024**: System MUST prevent deletion of workflows with active scheduled runs (return 409 Conflict)
- **FR-025**: System MUST increment version number when workflows are updated

#### Evaluator Management

- **FR-026**: System MUST provide endpoint to retrieve individual evaluator by ID (GET /agents/evals/{eval_id})
- **FR-027**: System MUST provide endpoint to update evaluators (PUT /agents/evals/{eval_id})
- **FR-028**: System MUST provide endpoint to soft-delete evaluators (DELETE /agents/evals/{eval_id})
- **FR-029**: System MUST increment version number when evaluators are updated

#### Schedule Management

- **FR-030**: System MUST provide endpoint to retrieve individual schedule by ID (GET /runs/schedule/{schedule_id})
- **FR-031**: System MUST provide endpoint to update schedules (PUT /runs/schedule/{schedule_id})
- **FR-032**: System MUST update APScheduler job when schedule cron expression is modified
- **FR-033**: System MUST recalculate next_run_time when schedule is updated
- **FR-034**: System MUST update updated_at timestamp when schedules are modified

#### Workflow Resume (Optional - P3)

- **FR-035**: System SHOULD provide endpoint to resume failed workflows from point of failure (POST /runs/workflow/{run_id}/resume)
- **FR-036**: System SHOULD preserve workflow state (outputs from completed steps) to enable resume
- **FR-037**: System SHOULD create new run with parent_run_id reference when resuming
- **FR-038**: System SHOULD only allow resume for runs with status = "failed"
- **FR-039**: System SHOULD track resume_from_step to indicate where resume started

### Key Entities

- **Agent Definition**: Represents an AI agent with instructions, tools, and configuration. Has is_builtin flag to distinguish system agents from personal agents, and created_by to track ownership.

- **Tool Definition**: Represents a callable tool with input/output schema, entrypoint, and scopes. Has is_builtin flag and version tracking. Can be built-in (immutable) or custom (user-created).

- **Workflow Definition**: Represents a multi-step process with ordered steps, each calling agents or tools. Has version tracking and created_by ownership. Can have scheduled runs.

- **Evaluator Definition**: Represents a scoring mechanism for agent runs with criteria, thresholds, and LLM configuration. Has version tracking and created_by ownership.

- **Scheduled Run**: Represents a recurring agent execution with cron expression, next_run_time, and APScheduler job reference. Linked to agent or workflow.

- **Run Record**: Represents a single execution of an agent or workflow with status, input, output, and timing. May have parent_run_id and resume_from_step for resumed workflows. Captures snapshot of tool/workflow definitions at run start for version isolation.

- **Dispatcher Agent**: Built-in agent that analyzes queries and returns routing decisions with selected tools/agents, confidence scores, reasoning, and alternatives. Each routing decision is logged with query, selections, confidence, reasoning, timestamp, and user ID for observability.

- **Dispatcher Decision Log**: Record of each dispatcher routing decision containing query text, selected_tools, selected_agents, confidence score, reasoning, timestamp, and user_id. Used for accuracy measurement, debugging, and system improvement.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can create personal agents that are only visible to them, verified by cross-user testing showing 0% visibility of other users' personal agents
- **SC-002**: Dispatcher agent achieves 95%+ routing accuracy on test query set covering document search, web search, and multi-tool scenarios
- **SC-003**: Dispatcher agent response time is under 2 seconds for 95% of queries at expected load (1000 queries/hour, 100-500 concurrent users)
- **SC-004**: Users can complete full CRUD operations (create, read, update, delete) on custom tools, workflows, and evaluators through the API
- **SC-005**: Schedule updates correctly modify APScheduler jobs with next_run_time recalculating within 1 second
- **SC-006**: System prevents 100% of unauthorized access attempts to personal agents (verified by security testing)
- **SC-007**: System prevents 100% of attempts to modify or delete built-in tools/agents for all users including admins (returns appropriate 403 errors)
- **SC-008**: System prevents 100% of attempts to delete resources in use by active agents/workflows (returns appropriate 409 errors)
- **SC-009**: Workflow resume (if implemented) successfully resumes from failure point without re-executing completed steps in 100% of test cases
- **SC-010**: All new endpoints respond with appropriate HTTP status codes (200, 204, 400, 403, 404, 409) according to REST conventions

## Assumptions

1. **Authentication**: All endpoints require JWT authentication via Bearer token, provided by existing auth system
2. **Authorization**: Role-based access control (RBAC) is already implemented and can be extended for resource ownership checks
3. **Database**: PostgreSQL with existing schema for agent_definitions, tool_definitions, workflow_definitions, eval_definitions, scheduled_runs, and run_records
4. **LLM Access**: LiteLLM is already integrated and available for dispatcher agent implementation
5. **Scheduler**: APScheduler is already integrated and managing scheduled runs
6. **Soft Delete**: All delete operations are soft deletes (is_active = false) to preserve audit trail
7. **Version Tracking**: Version numbers are simple integers that increment on each update. Running agents use snapshot of tool/workflow definitions from run start time for version isolation
8. **Error Format**: API uses FastAPI standard error format with {"detail": "message"} structure
9. **Dispatcher Model**: Dispatcher agent uses Claude 3.5 Sonnet via LiteLLM for query analysis
10. **Performance**: Target response times are <2s for dispatcher, <500ms for CRUD operations at expected scale (100-500 concurrent users, 1000 queries/hour)
11. **Concurrency**: System handles concurrent updates to schedules and definitions without race conditions
12. **Scale**: System designed for medium scale deployment: 100-500 concurrent users, 1000 queries/hour, up to 1000 total agents/tools/workflows
13. **Backward Compatibility**: New endpoints do not break existing API contracts

## Dependencies

### External Dependencies

- **LiteLLM**: Required for dispatcher agent LLM calls (already integrated)
- **APScheduler**: Required for schedule management (already integrated)
- **PostgreSQL**: Required for all data persistence (already integrated)
- **FastAPI**: Web framework for API endpoints (already integrated)
- **SQLAlchemy**: ORM for database access (already integrated)

### Internal Dependencies

- **Authentication Service**: Provides JWT token validation and user principal
- **Authorization Service**: Provides role and permission checking
- **Agent Execution Engine**: Executes agents and workflows
- **Tool Registry**: Manages tool loading and execution

### Schema Changes Required

**Phase 1**:
```sql
-- Add is_builtin flag if not exists
ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS is_builtin BOOLEAN DEFAULT FALSE;
```

**Phase 2 (if workflow resume implemented)**:
```sql
-- Add workflow resume support
ALTER TABLE run_records ADD COLUMN parent_run_id UUID REFERENCES run_records(id);
ALTER TABLE run_records ADD COLUMN resume_from_step VARCHAR(255);
ALTER TABLE run_records ADD COLUMN workflow_state JSONB;
```

## Out of Scope

The following are explicitly **not** included in this feature:

1. **Hard Delete**: Physical deletion of records from database (all deletes are soft deletes)
2. **Bulk Operations**: Bulk create/update/delete endpoints (deferred to Phase 3)
3. **Version History UI**: User interface for viewing and rolling back to previous versions (deferred to Phase 3)
4. **Agent Sharing**: Ability to share personal agents with other users or teams
5. **Tool Marketplace**: Public repository of community-contributed tools
6. **Workflow Branching**: Conditional logic and branching in workflow steps
7. **Real-time Notifications**: Push notifications for schedule runs or workflow completion
8. **Advanced Dispatcher**: Multi-agent orchestration with parallel execution (initial version is sequential)
9. **Audit Logging**: Comprehensive audit trail of all changes (basic timestamps only)
10. **API Rate Limiting**: Per-user or per-endpoint rate limits
11. **Webhook Support**: Callbacks for workflow completion or schedule triggers
12. **Import/Export**: Bulk import/export of agent definitions, tools, workflows

## Risks

### Technical Risks

1. **Dispatcher Accuracy**: Risk that dispatcher agent routing accuracy falls below 95% target
   - *Mitigation*: Extensive testing with diverse query set, iterative prompt engineering, confidence threshold tuning

2. **Performance**: Risk that dispatcher response time exceeds 2s target due to LLM latency
   - *Mitigation*: Caching for common queries, async processing, timeout handling, fallback to default routing

3. **Workflow State Complexity**: Risk that workflow resume implementation is more complex than estimated
   - *Mitigation*: Defer to Phase 3 if complexity is high, implement basic version first

4. **Concurrency Issues**: Risk of race conditions when multiple users update schedules or definitions simultaneously
   - *Mitigation*: Database-level locking, optimistic concurrency control with version numbers

### Security Risks

1. **Authorization Bypass**: Risk that users could access other users' personal agents via direct API calls
   - *Mitigation*: Server-side filtering on all endpoints, comprehensive authorization testing

2. **Built-in Tool Modification**: Risk that users could modify or delete built-in tools through API vulnerabilities
   - *Mitigation*: Explicit is_builtin checks in all update/delete endpoints (enforced for all users including admins), integration testing

### Operational Risks

1. **Migration Complexity**: Risk that adding is_builtin flag requires complex data migration
   - *Mitigation*: Default value of FALSE, manual flagging of built-in agents, validation scripts

2. **APScheduler State**: Risk that schedule updates could corrupt APScheduler state
   - *Mitigation*: Transactional updates, rollback on failure, APScheduler health checks

## Notes

- This specification is based on the detailed requirements document from the agent-client rebuild project
- Implementation is planned in 3 phases: Phase 1 (P1 requirements), Phase 2 (P2 requirements), Phase 3 (P3 requirements)
- The dispatcher agent is the most complex component and may require iteration to achieve target accuracy
- Workflow resume capability (FR-034 to FR-038) is marked as SHOULD rather than MUST due to implementation complexity
- All endpoints follow REST conventions and FastAPI patterns consistent with existing agent-server codebase
- API consistency improvements (standardizing parameter names like job_id → schedule_id) should be considered as part of this work
