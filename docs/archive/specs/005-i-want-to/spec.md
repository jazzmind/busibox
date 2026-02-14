# Feature Specification: Production-Grade Agent Server with Pydantic AI

**Feature Branch**: `005-i-want-to`  
**Created**: 2025-01-08  
**Status**: Draft  
**Input**: User description: "I want to create a new spec focused on making the code in @agent production grade and fully implement the agents, workflows, scorerers, etc. found here @mastra . We also need to implement the API endpoints to interact with these agents/workflows/etc, including creating/editing DB agents/workflows/etc. We need extensive tests so that we know that the APIs work, the agents work as intended, etc."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Execute Core Agent with Tool Calls (Priority: P1)

A developer or application sends a request to execute an agent (e.g., chat agent with search/ingest/RAG tools) and receives structured responses with tool call results and final output.

**Why this priority**: This is the foundational capability - without agent execution, the system provides no value. This validates the core Pydantic AI integration, tool registration, and Busibox service forwarding.

**Independent Test**: Can be fully tested by sending a POST request to `/runs` with an agent ID and prompt, verifying the agent calls appropriate Busibox tools (search/ingest/RAG) and returns structured output. Delivers immediate value by enabling AI-powered operations on Busibox data.

**Acceptance Scenarios**:

1. **Given** an active agent with registered tools, **When** a user submits a run request with a prompt, **Then** the agent executes, calls appropriate tools with forwarded auth tokens, and returns structured output with tool results
2. **Given** a run in progress, **When** the user requests run status via SSE stream, **Then** they receive real-time status updates and tool call events
3. **Given** a completed run, **When** the user retrieves run details, **Then** they see full execution history including input, output, tool calls, and timestamps

---

### User Story 2 - Create and Manage Dynamic Agents (Priority: P2)

An administrator creates, updates, and activates custom agent definitions stored in the database, specifying instructions, model, and allowed tools without code changes.

**Why this priority**: Enables non-developers to configure agents for specific use cases (e.g., RFP analysis, document summarization) without redeployment. Critical for operational flexibility but depends on P1 execution capability.

**Independent Test**: Can be fully tested by POSTing agent definitions to `/agents/definitions`, verifying persistence in DB, confirming the agent registry loads them on refresh, and executing the newly created agent. Delivers value by allowing rapid agent customization.

**Acceptance Scenarios**:

1. **Given** valid agent definition payload (name, instructions, model, tools), **When** administrator POSTs to `/agents/definitions`, **Then** the agent is persisted, assigned a unique ID, and marked active
2. **Given** an active dynamic agent, **When** the agent registry refreshes, **Then** the agent is loaded with specified tools from the registry and available for execution
3. **Given** an existing agent definition, **When** administrator updates instructions or tools via PUT, **Then** the changes are persisted and reflected after registry refresh
4. **Given** multiple agent definitions, **When** administrator lists agents via GET `/agents`, **Then** all active agents are returned with metadata (name, model, version, scopes)

---

### User Story 3 - Schedule Long-Running Agent Tasks (Priority: P3)

An administrator schedules an agent to run on a cron schedule (e.g., daily document ingestion, weekly report generation) with automatic token refresh and persistent state.

**Why this priority**: Enables automation and reduces manual intervention for recurring tasks. Depends on P1 execution and P2 dynamic agents but is not critical for initial value delivery.

**Independent Test**: Can be fully tested by POSTing a schedule definition to `/runs/schedule` with cron expression, verifying the scheduler creates jobs, and confirming runs execute at scheduled times with fresh tokens. Delivers value by automating repetitive agent operations.

**Acceptance Scenarios**:

1. **Given** a valid cron expression and agent ID, **When** administrator creates a schedule, **Then** the scheduler registers the job and executes the agent at specified intervals
2. **Given** a scheduled run, **When** execution time arrives, **Then** the system exchanges a fresh downstream token, executes the agent, and persists run results
3. **Given** a scheduled job, **When** administrator cancels the schedule, **Then** future executions are prevented and existing runs remain accessible

---

### User Story 4 - Define and Execute Workflows (Priority: P3)

A developer defines multi-step workflows (e.g., ingest document → generate embeddings → summarize → store results) and executes them as coordinated agent operations.

**Why this priority**: Workflows orchestrate complex multi-agent operations but require P1 execution and P2 dynamic definitions. Provides advanced capability for sophisticated use cases.

**Independent Test**: Can be fully tested by creating a workflow definition with multiple steps, executing it via API, and verifying each step completes in order with outputs passed between steps. Delivers value by enabling complex document processing pipelines.

**Acceptance Scenarios**:

1. **Given** a workflow definition with sequential steps, **When** user executes the workflow, **Then** each step runs in order, passing outputs to subsequent steps
2. **Given** a workflow with conditional branching, **When** a condition is met, **Then** the appropriate branch executes
3. **Given** a workflow step failure, **When** retry policy is defined, **Then** the step retries according to policy before failing the workflow

---

### User Story 5 - Evaluate Agent Performance with Scorers (Priority: P4)

An administrator configures evaluation scorers (e.g., response quality, latency, tool call accuracy) and runs them against agent executions to measure performance.

**Why this priority**: Quality assurance and performance monitoring are important for production but not required for initial functionality. Depends on P1 execution history.

**Independent Test**: Can be fully tested by defining a scorer (e.g., response length, sentiment), running it against completed runs, and verifying scores are calculated and persisted. Delivers value by enabling data-driven agent improvement.

**Acceptance Scenarios**:

1. **Given** a scorer definition and completed run, **When** scorer is executed, **Then** a score is calculated and stored with the run
2. **Given** multiple runs with scores, **When** administrator queries scores, **Then** aggregated metrics (avg, min, max) are returned
3. **Given** a scorer with threshold, **When** a run fails to meet threshold, **Then** an alert is triggered

---

### Edge Cases

- What happens when an agent execution exceeds timeout limits?
- How does the system handle tool call failures (e.g., search API returns 500)?
- What happens when a dynamic agent references a non-existent tool in the registry?
- How does the system handle concurrent modifications to the same agent definition?
- What happens when token exchange fails during a scheduled run?
- How does the system handle malformed or malicious agent instructions?
- What happens when the agent registry fails to load during startup?
- How does the system handle database connection failures during run persistence?
- What happens when SSE stream clients disconnect mid-execution?
- How does the system handle workflow step failures with no retry policy?

## Requirements *(mandatory)*

### Functional Requirements

#### Core Agent Execution (P1)

- **FR-001**: System MUST execute agents with registered tools (search, ingest, RAG) and forward Busibox auth tokens to downstream services
- **FR-002**: System MUST persist run records including input, output, status, events, and timestamps
- **FR-003**: System MUST provide real-time run status updates via SSE streams
- **FR-004**: System MUST validate agent instructions and tool references before execution
- **FR-005**: System MUST handle tool call failures gracefully with error messages and partial results
- **FR-006**: System MUST enforce tiered execution timeouts and resource limits based on agent type: Simple agents (30 seconds, 512MB), Complex agents (5 minutes, 2GB), Batch agents (30 minutes, 4GB)

#### Dynamic Agent Management (P2)

- **FR-007**: System MUST allow administrators to create agent definitions with name, instructions, model, tools, and scopes
- **FR-008**: System MUST persist agent definitions in PostgreSQL with versioning
- **FR-009**: System MUST load active agent definitions into the registry on startup and refresh
- **FR-010**: System MUST validate tool references against the allowed tool registry before persisting
- **FR-011**: System MUST allow administrators to update agent definitions and increment version numbers
- **FR-012**: System MUST allow administrators to deactivate agents without deletion
- **FR-013**: System MUST prevent execution of inactive agents

#### Authentication & Authorization (P1)

- **FR-014**: System MUST validate Busibox JWT tokens via JWKS for all API requests
- **FR-015**: System MUST exchange user tokens for scoped downstream tokens via OAuth2 client-credentials
- **FR-016**: System MUST cache downstream tokens in database with expiry tracking
- **FR-017**: System MUST enforce scope-based access control for agent execution and management
- **FR-018**: System MUST rotate downstream tokens before expiry for scheduled runs

#### Scheduling (P3)

- **FR-019**: System MUST allow administrators to schedule agent runs with cron expressions
- **FR-020**: System MUST execute scheduled runs with fresh downstream tokens
- **FR-021**: System MUST persist scheduled run results with schedule metadata
- **FR-022**: System MUST allow administrators to cancel scheduled jobs
- **FR-023**: System MUST handle scheduler failures with retry and alerting

#### Workflows (P3)

- **FR-024**: System MUST allow administrators to define workflows with sequential and conditional steps
- **FR-025**: System MUST execute workflow steps in order, passing outputs between steps
- **FR-026**: System MUST support workflow branching based on step outputs
- **FR-027**: System MUST persist workflow execution state for resume/retry
- **FR-028**: System MUST allow administrators to define retry policies for workflow steps

#### Evaluation & Scoring (P4)

- **FR-029**: System MUST allow administrators to define scorer configurations
- **FR-030**: System MUST execute scorers against completed runs and persist scores
- **FR-031**: System MUST provide aggregated score metrics (avg, min, max, percentiles)
- **FR-032**: System MUST support threshold-based alerts for scorer results

#### Testing & Observability

- **FR-033**: System MUST provide comprehensive unit tests for all API endpoints
- **FR-034**: System MUST provide integration tests for agent execution with mocked Busibox services
- **FR-035**: System MUST provide end-to-end tests for complete user journeys (P1-P4)
- **FR-036**: System MUST log all agent executions, tool calls, and errors with structured logging
- **FR-037**: System MUST expose health check endpoints for monitoring
- **FR-038**: System MUST provide OpenTelemetry-compatible tracing for agent executions

### Key Entities

- **Agent Definition**: Represents a configured agent with name, instructions, model, tools, scopes, version, and activation status
- **Tool Definition**: Represents an allowed tool adapter with name, schema, entrypoint, and scopes
- **Workflow Definition**: Represents a multi-step workflow with steps, branching logic, and retry policies
- **Run Record**: Represents an agent execution with input, output, status, events, timestamps, and creator
- **Token Grant**: Represents a cached downstream token with subject, scopes, token value, and expiry
- **Scorer Definition**: Represents an evaluation scorer with name, configuration, and thresholds
- **Schedule**: Represents a scheduled agent run with cron expression, agent ID, and status

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Developers can execute agents with tool calls and receive structured responses in under 5 seconds for simple queries
- **SC-002**: Administrators can create and activate new agent definitions without code changes or redeployment
- **SC-003**: System handles 100 concurrent agent executions without performance degradation
- **SC-004**: 95% of agent executions complete successfully with valid tool call results
- **SC-005**: All API endpoints have 90%+ test coverage with passing unit and integration tests
- **SC-006**: Scheduled agent runs execute within 30 seconds of scheduled time with fresh tokens
- **SC-007**: Workflow executions complete with all steps logged and outputs persisted
- **SC-008**: Token exchange failures are detected and retried within 1 second
- **SC-009**: System recovers from database connection failures within 10 seconds without data loss
- **SC-010**: All agent executions are traceable via logs and OpenTelemetry spans
