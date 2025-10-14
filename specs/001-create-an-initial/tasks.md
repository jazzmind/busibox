---
description: "Task list for Local LLM Infrastructure Platform implementation"
---

# Tasks: Local LLM Infrastructure Platform

**Input**: Design documents from `/specs/001-create-an-initial/`  
**Prerequisites**: plan.md (complete), spec.md (complete), research.md (complete), data-model.md (complete), contracts/ (complete)

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`
- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions
- **Infrastructure**: `provision/pct/` for container scripts, `provision/ansible/roles/` for service configuration
- **Service code**: `/srv/<service-name>/` within respective containers (deployed via Ansible)
- **Tools**: `tools/` at repository root
- **Documentation**: `docs/` at repository root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 [P] Create LXC container provisioning script in `provision/pct/create_lxc_base.sh`
- [x] T002 [P] Create environment configuration in `provision/pct/vars.env`
- [x] T003 Create Ansible inventory template in `provision/ansible/inventory/hosts.yml`
- [x] T004 Create Ansible main playbook in `provision/ansible/site.yml`
- [x] T005 [P] Create Ansible Makefile with targets (all, ping, per-service) in `provision/ansible/Makefile`
- [x] T006 [P] Create basic PostgreSQL schema in `provision/ansible/roles/postgres/files/schema.sql`
- [x] T007 [P] Create Ansible role structure for all services (postgres, minio, milvus, agent_api, ingest_worker, deploywatch, node_common)
- [x] T008 [P] Create README.md describing system architecture
- [x] T009 [P] Create QUICKSTART.md with provisioning instructions
- [x] T010 [P] Create Milvus initialization script in `tools/milvus_init.py`
- [x] T011 [P] Create architecture documentation in `docs/architecture.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T012 Implement complete PostgreSQL schema with migrations in `provision/ansible/roles/postgres/files/migrations/001_initial_schema.sql`
- [x] T013 Implement PostgreSQL migration rollback script in `provision/ansible/roles/postgres/files/migrations/001_rollback.sql`
- [x] T014 Implement Row-Level Security (RLS) policies in `provision/ansible/roles/postgres/files/migrations/002_add_rls_policies.sql`
- [x] T015 Implement RLS rollback script in `provision/ansible/roles/postgres/files/migrations/002_rollback.sql`
- [x] T016 [P] Create base Python service structure for agent API in `/srv/agent/src/`
- [x] T017 [P] Create base Python service structure for ingest worker in `/srv/ingest/src/`
- [x] T018 [P] Configure structured logging (JSON format) in Python services using structlog
- [x] T019 Implement health check helper functions for all dependency checks (database, milvus, minio, redis)
- [x] T020 [P] Create Python requirements.txt for agent API service
- [x] T021 [P] Create Python requirements.txt for ingest worker service
- [x] T022 Implement Ansible task to apply database migrations in `provision/ansible/roles/postgres/tasks/main.yml`
- [x] T023 [P] Create Makefile verify target in `provision/ansible/Makefile`
- [x] T024 [P] Create Makefile verify-health target (checks all services are responding)
- [x] T025 [P] Create Makefile verify-smoke target (basic functionality tests)

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Infrastructure Provisioning (Priority: P1) 🎯 MVP

**Goal**: Enable infrastructure administrators to provision complete platform on Proxmox with automated deployment and verification

**Independent Test**: Run provisioning scripts on fresh Proxmox host, execute Ansible playbooks, verify all health checks pass

### Implementation for User Story 1

- [x] T026 [P] [US1] Enhance `provision/pct/vars.env` with documentation comments for all configuration options
- [x] T027 [P] [US1] Add container creation validation to `provision/pct/create_lxc_base.sh` (check if CTID exists, verify network config)
- [x] T028 [US1] Implement MinIO deployment in `provision/ansible/roles/minio/tasks/main.yml` (install, configure buckets, setup systemd service)
- [x] T029 [US1] Implement PostgreSQL deployment in `provision/ansible/roles/postgres/tasks/main.yml` (install, run migrations, create users)
- [x] T030 [US1] Implement Milvus deployment in `provision/ansible/roles/milvus/tasks/main.yml` (Docker installation, Milvus container, systemd service)
- [x] T031 [US1] Implement Redis deployment (part of ingest_worker role) for job queue
- [x] T032 [US1] Implement Node.js common setup in `provision/ansible/roles/node_common/tasks/main.yml` (Node 20 LTS, PM2, yarn/pnpm)
- [x] T033 [US1] Configure MinIO webhook in Ansible role to trigger agent API endpoint
- [x] T034 [US1] Implement deploywatch systemd service in `provision/ansible/roles/deploywatch/tasks/main.yml`
- [x] T035 [US1] Add health check implementations to all Ansible roles (verify service is running and responsive)
- [ ] T036 [US1] Test end-to-end: Run `provision/pct/create_lxc_base.sh` → `make all` → `make verify` on test Proxmox host
- [x] T037 [US1] Document any manual post-deployment steps in QUICKSTART.md (credential changes, LLM provider setup)

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently - complete infrastructure can be provisioned

---

## Phase 4: User Story 2 - Secure File Upload and Storage (Priority: P2)

**Goal**: Enable users to upload files securely with RBAC enforcement and webhook triggering

**Independent Test**: Authenticate as different users, upload files, verify permission enforcement and webhook events

### Implementation for User Story 2

- [ ] T038 [P] [US2] Create User model in `/srv/agent/src/models/user.py` (User, Role, UserRole models)
- [ ] T039 [P] [US2] Create File model in `/srv/agent/src/models/file.py` (FileMetadata model)
- [ ] T040 [US2] Implement authentication service in `/srv/agent/src/services/auth.py` (JWT generation, password hashing, user lookup)
- [ ] T041 [US2] Implement MinIO client service in `/srv/agent/src/services/minio.py` (presigned URL generation, bucket operations)
- [ ] T042 [US2] Implement PostgreSQL client service in `/srv/agent/src/services/postgres.py` (connection pool, RLS context setting)
- [ ] T043 [US2] Implement authentication middleware for FastAPI in `/srv/agent/src/services/auth.py`
- [ ] T044 [US2] Implement RBAC permission checking in `/srv/agent/src/services/auth.py`
- [ ] T045 [US2] Create FastAPI health endpoint in `/srv/agent/src/routes/health.py` (check all dependencies)
- [ ] T046 [US2] Create authentication endpoints in `/srv/agent/src/routes/auth.py` (POST /auth/login, GET /auth/me)
- [ ] T047 [US2] Create file upload endpoints in `/srv/agent/src/routes/files.py` (POST /files/upload - initiate upload with presigned URL)
- [ ] T048 [US2] Create file metadata endpoint in `/srv/agent/src/routes/files.py` (GET /files/{file_id})
- [ ] T049 [US2] Create file download endpoint in `/srv/agent/src/routes/files.py` (GET /files/{file_id}/download - presigned URL)
- [ ] T050 [US2] Create file delete endpoint in `/srv/agent/src/routes/files.py` (DELETE /files/{file_id})
- [ ] T051 [US2] Implement MinIO webhook receiver in `/srv/agent/src/routes/webhooks.py` (POST /webhooks/minio - enqueue ingestion job)
- [ ] T052 [US2] Create FastAPI main application in `/srv/agent/src/main.py` (app initialization, route registration, CORS, error handling)
- [ ] T053 [US2] Deploy agent API service via Ansible (copy code, install dependencies, configure systemd service)
- [ ] T054 [US2] Test: Create test user via PostgreSQL, authenticate via API, upload file, verify presigned URL works
- [ ] T055 [US2] Test: Verify RLS - user A cannot access user B's files
- [ ] T056 [US2] Test: Upload file and verify webhook event is triggered and logged

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently - users can upload and manage files securely

---

## Phase 5: User Story 3 - Automated File Processing and Embedding (Priority: P3)

**Goal**: Automatically process uploaded files by extracting text, chunking, generating embeddings, and storing in vector DB

**Independent Test**: Upload test file, verify embeddings in Milvus, metadata in PostgreSQL, job completion logged

### Implementation for User Story 3

- [ ] T057 [P] [US3] Create Job model in `/srv/ingest/src/models/job.py` (IngestionJob model with status transitions)
- [ ] T058 [P] [US3] Create Chunk model in `/srv/ingest/src/models/chunk.py` (Chunk model with content, tokens, positions)
- [ ] T059 [US3] Implement Redis client service in `/srv/ingest/src/services/redis.py` (Streams consumer group, job queue operations)
- [ ] T060 [US3] Implement MinIO client for file retrieval in `/srv/ingest/src/services/minio.py` (download file from bucket)
- [ ] T061 [US3] Implement PostgreSQL client for metadata writes in `/srv/ingest/src/services/postgres.py` (chunk insertion, job status updates)
- [ ] T062 [US3] Implement Milvus client for vector storage in `/srv/ingest/src/services/milvus.py` (insert embeddings, collection operations)
- [ ] T063 [US3] Implement LLM client for embeddings in `/srv/ingest/src/services/llm.py` (liteLLM integration, embedding generation)
- [ ] T064 [US3] Implement PDF text extraction in `/srv/ingest/src/processors/text_extraction.py` (pdfplumber with PyPDF2 fallback)
- [ ] T065 [US3] Implement DOCX text extraction in `/srv/ingest/src/processors/text_extraction.py` (python-docx)
- [ ] T066 [US3] Implement TXT/MD text extraction in `/srv/ingest/src/processors/text_extraction.py` (direct file read)
- [ ] T067 [US3] Implement semantic chunking in `/srv/ingest/src/processors/chunking.py` (spaCy sentence-based, 512 tokens, 50 overlap)
- [ ] T068 [US3] Implement embedding generation in `/srv/ingest/src/processors/embedding.py` (batch embedding via liteLLM)
- [ ] T069 [US3] Implement main worker process in `/srv/ingest/src/worker.py` (Redis Streams consumer, job processing loop)
- [ ] T070 [US3] Implement error handling and retry logic in `/srv/ingest/src/worker.py` (update job status, log errors)
- [ ] T071 [US3] Create worker health check endpoint in `/srv/ingest/src/worker.py` (simple HTTP health endpoint on port 3002)
- [ ] T072 [US3] Configure liteLLM gateway on agent-lxc container (install liteLLM, configure providers in `/etc/litellm/config.yaml`, create systemd service)
- [ ] T073 [US3] Deploy ingest worker service via Ansible (copy code, install dependencies including spaCy model, configure systemd service)
- [ ] T074 [US3] Test: Upload PDF file, monitor worker logs, verify chunks in PostgreSQL
- [ ] T075 [US3] Test: Verify embeddings exist in Milvus collection with correct file_id references
- [ ] T076 [US3] Test: Upload malformed file, verify job fails gracefully with error logged

**Checkpoint**: At this point, User Stories 1, 2, AND 3 should all work independently - files are automatically processed into searchable embeddings

---

## Phase 6: User Story 4 - Semantic Search and Retrieval (Priority: P4)

**Goal**: Enable users to perform semantic search across uploaded documents with permission filtering

**Independent Test**: Upload documents, perform semantic searches, verify results are relevant and permission-filtered

### Implementation for User Story 4

- [ ] T077 [P] [US4] Create Search models in `/srv/agent/src/models/search.py` (SearchRequest, SearchResult, SearchResponse)
- [ ] T078 [US4] Implement Milvus search client in `/srv/agent/src/services/milvus.py` (vector similarity search, result retrieval)
- [ ] T079 [US4] Implement embedding service for query encoding in `/srv/agent/src/services/llm.py` (convert text query to vector)
- [ ] T080 [US4] Implement permission filtering for search results in `/srv/agent/src/services/auth.py` (filter chunks by user's file access)
- [ ] T081 [US4] Create search endpoint in `/srv/agent/src/routes/search.py` (POST /search - semantic search with filters)
- [ ] T082 [US4] Implement result ranking and scoring in `/srv/agent/src/routes/search.py` (relevance score calculation, min_score filtering)
- [ ] T083 [US4] Test: Upload known documents, search with specific queries, verify relevant chunks returned
- [ ] T084 [US4] Test: User A searches - verify only sees own files' chunks, not User B's files
- [ ] T085 [US4] Test: Search performance - verify <2 second response time for typical queries

**Checkpoint**: At this point, User Stories 1-4 should all work independently - semantic search is functional with proper security

---

## Phase 7: User Story 5 - AI Agent Operations (Priority: P5)

**Goal**: Enable users to invoke AI agents that combine RAG retrieval with LLM generation

**Independent Test**: Invoke agent with query, verify it retrieves context, generates response, respects permissions

### Implementation for User Story 5

- [ ] T086 [P] [US5] Create Agent models in `/srv/agent/src/models/agent.py` (AgentInvokeRequest, AgentInvokeResponse)
- [ ] T087 [US5] Implement LLM completion service in `/srv/agent/src/services/llm.py` (chat completion via liteLLM with context)
- [ ] T088 [US5] Implement RAG workflow in `/srv/agent/src/services/agent.py` (search → retrieve → format context → LLM call)
- [ ] T089 [US5] Implement context formatting for LLM prompts in `/srv/agent/src/services/agent.py` (combine chunks into prompt)
- [ ] T090 [US5] Create agent invoke endpoint in `/srv/agent/src/routes/agent.py` (POST /agent/invoke)
- [ ] T091 [US5] Implement permission inheritance for agent operations in `/srv/agent/src/routes/agent.py` (agent uses user's permissions)
- [ ] T092 [US5] Implement error handling for LLM failures in `/srv/agent/src/services/agent.py` (timeout, retry, fallback)
- [ ] T093 [US5] Test: Invoke agent with question about uploaded documents, verify response uses retrieved context
- [ ] T094 [US5] Test: Verify agent respects user permissions - cannot retrieve context from files user doesn't own
- [ ] T095 [US5] Test: Agent response time - verify <10 seconds including RAG retrieval

**Checkpoint**: At this point, User Stories 1-5 should all work independently - AI agents with RAG are functional

---

## Phase 8: User Story 6 - Application Development and Deployment (Priority: P6)

**Goal**: Enable developers to deploy custom applications on the platform with reverse proxy access

**Independent Test**: Deploy sample app, verify it can authenticate users and call agent API

### Implementation for User Story 6

- [ ] T096 [US6] Create LXC container for app server (apps-lxc) in `provision/pct/create_lxc_base.sh`
- [ ] T097 [US6] Create Ansible role for app server in `provision/ansible/roles/app_server/`
- [ ] T098 [US6] Implement nginx reverse proxy configuration in Ansible role (route /app1/, /app2/ to app ports)
- [ ] T099 [US6] Create sample application template in `provision/ansible/roles/app_server/files/sample-app/`
- [ ] T100 [US6] Implement app deployment mechanism (copy app code, install dependencies, start with PM2/systemd)
- [ ] T101 [US6] Configure nginx authentication passthrough (X-User-Id header from JWT)
- [ ] T102 [US6] Document app deployment process in docs/app-deployment.md
- [ ] T103 [US6] Test: Deploy sample app, access via nginx proxy, verify routing works
- [ ] T104 [US6] Test: Sample app calls agent API with authenticated user context

**Checkpoint**: At this point, User Stories 1-6 should all work independently - custom apps can be deployed

---

## Phase 9: User Story 7 - Multiple LLM Provider Access (Priority: P7)

**Goal**: Provide unified interface to multiple local LLM providers through liteLLM gateway

**Independent Test**: Configure multiple providers, make requests, verify routing works

### Implementation for User Story 7

- [ ] T105 [US7] Implement liteLLM configuration template in `provision/ansible/roles/agent_api/templates/litellm-config.yaml.j2`
- [ ] T106 [US7] Create Ansible tasks to deploy liteLLM as systemd service
- [ ] T107 [US7] Implement model discovery endpoint (GET /models) in agent API that proxies to liteLLM
- [ ] T108 [US7] Add model validation in agent invoke endpoint (verify requested model exists)
- [ ] T109 [US7] Implement fallback logic for unavailable providers in `/srv/agent/src/services/llm.py`
- [ ] T110 [US7] Document LLM provider configuration in docs/llm-providers.md
- [ ] T111 [US7] Test: Configure Ollama and vLLM providers, verify requests route correctly
- [ ] T112 [US7] Test: Disable one provider, verify appropriate error message returned

**Checkpoint**: At this point, User Stories 1-7 should all work independently - multiple LLM providers accessible

---

## Phase 10: User Story 8 - Automated Service Updates (Priority: P8)

**Goal**: Automatically detect and deploy new service versions from GitHub releases

**Independent Test**: Create test release, verify deploywatch detects and deploys it

### Implementation for User Story 8

- [ ] T113 [US8] Implement GitHub release polling in `/srv/deploywatch/deploywatch.sh` (check releases API)
- [ ] T114 [US8] Implement version comparison logic in deploywatch script (only deploy if newer)
- [ ] T115 [US8] Implement service update procedure in deploywatch (pull code, stop service, update, start, health check)
- [ ] T116 [US8] Implement rollback logic in deploywatch (if health check fails after update)
- [ ] T117 [US8] Configure deploywatch systemd timer (run every 5 minutes)
- [ ] T118 [US8] Implement deployment logging in deploywatch (success, failure, rollback events)
- [ ] T119 [US8] Test: Create test GitHub release, wait for deploywatch to detect and deploy
- [ ] T120 [US8] Test: Deploy bad release (fails health check), verify rollback occurs

**Checkpoint**: All user stories should now be independently functional - platform is feature-complete

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T121 [P] Add comprehensive logging to all services (structured JSON logs via structlog/winston)
- [ ] T122 [P] Implement trace_id generation in agent API in `/srv/agent/src/middleware/tracing.py` (generate UUID per request, add to response headers)
- [ ] T122a [P] Implement trace_id propagation in agent API (pass trace_id to all service calls: milvus, postgres, minio, redis)
- [ ] T122b [P] Implement trace_id propagation in ingest worker (extract from job metadata, include in all log entries and service calls)
- [ ] T123 [P] Add performance monitoring (log request duration, embedding generation time, search latency)
- [ ] T124 Create administration guide in docs/administration.md (user management, backup procedures)
- [ ] T125 Create troubleshooting guide in docs/troubleshooting.md (common issues, debugging steps)
- [ ] T126 [P] Add security hardening (firewall rules via ufw, fail2ban for API rate limiting)
- [ ] T127 Create backup scripts for PostgreSQL, MinIO, and Milvus data
- [ ] T128 Document backup and restore procedures in docs/backup-restore.md
- [ ] T129 [P] Add monitoring setup guide for Prometheus/Grafana (optional, future enhancement)
- [ ] T130 Run full end-to-end integration test (all user stories P1-P8)
- [ ] T131 Performance testing (load test with concurrent users, verify success criteria met)
- [ ] T131a [P] Load test search endpoint (50 concurrent users via locust/k6, measure response time < 2s per SC-010)
- [ ] T131b [P] Test agent success rate (100 invocations, verify >= 95% success per SC-016)
- [ ] T131c [P] Fault injection test (stop individual services, verify graceful degradation per SC-019)
- [ ] T132 Security audit (verify RLS works, no SQL injection, proper auth enforcement)
- [ ] T133 Implement disk space monitoring with alerts in all containers (alert when > 80% usage)
- [ ] T134 Configure PostgreSQL connection pool limits in `/srv/agent/src/services/postgres.py` and `/srv/ingest/src/services/postgres.py` (max_connections, monitor exhaustion)
- [ ] T135 Documentation review (ensure all docs are current and accurate)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: ✅ COMPLETE - Project structure exists
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-10)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8)
- **Polish (Phase 11)**: Depends on desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational - May use US1 infrastructure but independently testable
- **User Story 3 (P3)**: Can start after Foundational - Uses US2 webhook but can be tested independently
- **User Story 4 (P4)**: Can start after Foundational - Uses US3 embeddings but can test with pre-populated data
- **User Story 5 (P5)**: Can start after Foundational - Uses US4 search but can test independently
- **User Story 6 (P6)**: Can start after Foundational - Uses US2/US5 APIs but independently deployable
- **User Story 7 (P7)**: Can start after Foundational - Enhances US3/US5 but can be tested independently
- **User Story 8 (P8)**: Can start after Foundational - Operates on any services, independently testable

### Within Each User Story

- Foundation phase tasks must complete before any user story tasks
- Models before services (within each story)
- Services before endpoints (within each story)
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, all user stories can start in parallel (if team capacity allows)
- Models within a story marked [P] can run in parallel
- Different user stories can be worked on in parallel by different team members

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. ✅ Complete Phase 1: Setup (DONE)
2. Complete Phase 2: Foundational (database schema, service structure, health checks)
3. Complete Phase 3: User Story 1 (infrastructure provisioning)
4. **STOP and VALIDATE**: Test User Story 1 independently
5. Deploy on test Proxmox host, verify all health checks pass

### Incremental Delivery

1. ✅ Setup complete
2. Foundation (T012-T025) → Foundation ready
3. Add User Story 1 (T026-T037) → Test independently → Full infrastructure provisioning works (MVP!)
4. Add User Story 2 (T038-T056) → Test independently → File upload and storage works
5. Add User Story 3 (T057-T076) → Test independently → Automated processing works
6. Add User Story 4 (T077-T085) → Test independently → Semantic search works
7. Add User Story 5 (T086-T095) → Test independently → AI agents work
8. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1 (Infrastructure)
   - Developer B: User Story 2 (File Upload)
   - Developer C: User Story 3 (Processing)
3. Stories complete and integrate independently

---

## Task Status Summary

### Completed Tasks
- **Phase 1 (Setup)**: 11/11 tasks complete (100%) ✅
  - ✅ T001-T009: Container scripts, Ansible structure, documentation
  - ✅ T010-T011: Milvus init script, architecture docs

- **Phase 2 (Foundational)**: 14/14 tasks complete (100%) ✅
  - ✅ T012-T015: Database migrations with RLS policies
  - ✅ T016-T017: Python service structures (agent API, ingest worker)
  - ✅ T018-T019: Structured logging and health checks
  - ✅ T020-T021: Python requirements files
  - ✅ T022-T025: Ansible migrations and verification targets

- **Phase 3 (US1)**: 11/12 tasks complete (92%) 🚀
  - ✅ T026-T027: Container creation with test mode and validation
  - ✅ T028-T031: Service deployments (MinIO, PostgreSQL, Milvus, Redis+Worker)
  - ✅ T032-T035: Node.js setup, Agent API, webhook config, health checks
  - ✅ T037: Documentation complete (QUICKSTART.md updated)
  - ⏳ T036: End-to-end testing (ready for execution)
- **Phase 4 (US2)**: 0/19 tasks complete (0%)
- **Phase 5 (US3)**: 0/20 tasks complete (0%)
- **Phase 6 (US4)**: 0/9 tasks complete (0%)
- **Phase 7 (US5)**: 0/10 tasks complete (0%)
- **Phase 8 (US6)**: 0/9 tasks complete (0%)
- **Phase 9 (US7)**: 0/8 tasks complete (0%)
- **Phase 10 (US8)**: 0/8 tasks complete (0%)
- **Phase 11 (Polish)**: 0/18 tasks complete (0%)

**Total**: 36/138 tasks complete (26%)

**Recent Additions** (from analysis):
- T122a-T122b: Trace ID propagation for observability
- T131a-T131c: Performance and reliability testing tasks
- T133-T134: Edge case handling (disk space, connection pool limits)

### Next Immediate Tasks (Priority Order)

1. **T010**: Create `tools/milvus_init.py` (setup completion)
2. **T011**: Create `docs/architecture.md` (setup completion)
3. **T012-T025**: Complete Foundational phase (CRITICAL - blocks all user stories)
4. **T026-T037**: Implement User Story 1 for MVP (infrastructure provisioning)

---

## Notes

- Tasks marked [P] involve different files with no dependencies - can be executed in parallel
- Each user story is independently completable and testable
- Stop at any checkpoint to validate story independently
- Constitution principles verified: Infrastructure as Code, Service Isolation, Observability, Test-Driven Infrastructure
- All services follow structured logging (JSON format with trace_id)
- Health checks required for all services before deployment considered successful
- Database migrations must have rollback procedures documented

