# Implementation Plan: Local LLM Infrastructure Platform

**Branch**: `001-create-an-initial` | **Date**: 2025-10-14 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/001-create-an-initial/spec.md`

**Note**: This plan documents the existing busibox infrastructure implementation and provides a roadmap for completing any missing components.

## Summary

The busibox platform provides a complete local LLM infrastructure environment provisioned on Proxmox hosts using Infrastructure as Code principles. It delivers secure file storage, automated document processing with embeddings, semantic search via RAG, AI agent operations, and extensible application hosting—all running on isolated LXC containers with role-based access control. The primary technical approach uses shell scripts for container creation, Ansible for service configuration, and a microservices architecture with containerized services communicating over a static IP network.

## Technical Context

**Language/Version**: 
- Shell scripting (bash) for Proxmox container provisioning
- Python 3.11+ for agent services, ingest workers, and tools
- Node.js 18+ for application server and web services

**Primary Dependencies**: 
- **Infrastructure**: Proxmox VE, LXC containers, Ansible 2.15+
- **Storage**: MinIO (S3-compatible), PostgreSQL 15+, Milvus 2.3+ (vector database)
- **Queue**: Redis 7+ (Streams for job queue)
- **LLM**: liteLLM (unified gateway), Ollama, vLLM or other local LLM providers
- **Web**: nginx (reverse proxy), systemd (service management)
- **Python**: FastAPI (API framework), psycopg2 (PostgreSQL), pymilvus (vector DB), redis-py
- **Node**: Express.js (app server framework)

**Storage**: 
- MinIO for object/file storage (S3-compatible buckets)
- PostgreSQL for relational data (users, roles, metadata, permissions with RLS)
- Milvus for vector embeddings (semantic search index)
- Redis for job queue (Streams) and caching

**Testing**: 
- Smoke tests via health endpoint checks (curl/scripts)
- Integration tests for end-to-end workflows (file upload → embedding → search)
- Infrastructure validation via Ansible playbook verification steps
- Python: pytest for service unit tests
- Node: jest for application tests

**Target Platform**: 
- Proxmox VE host (Linux KVM/LXC hypervisor)
- LXC containers (Debian 12 or Ubuntu 22.04 LTS base images)
- Services run in isolated containers on static IP network
- Initial target: x86_64 architecture with optional GPU passthrough for LLM acceleration

**Project Type**: Infrastructure platform (multi-container microservices architecture)

**Performance Goals**: 
- Provision complete infrastructure in <30 minutes
- File uploads up to 100MB supported
- Embedding generation: ≥100 chunks/minute
- Search latency: <2 seconds for semantic queries
- Agent responses: <10 seconds including RAG retrieval
- Support 10-100 concurrent users on single Proxmox host

**Constraints**: 
- Single Proxmox host deployment (no multi-node clustering initially)
- Static IP network assignment (no dynamic service discovery)
- Local LLM providers only (no cloud API dependencies)
- All data remains on-premises (privacy/security requirement)
- Infrastructure as Code mandatory (no manual configuration)
- Each service in dedicated LXC container (security isolation)

**Scale/Scope**: 
- 5-10 LXC containers initially (files, pg, milvus, agent, ingest, queue/redis, apps, optional: openwebui)
- 10-100 users (small team scale)
- 100s of files per hour ingestion throughput
- Storage capacity limited by host disk (plan for TB-scale)
- Horizontal worker scaling possible for ingestion

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Principle I: Infrastructure as Code (NON-NEGOTIABLE)

**Status**: ✅ PASS

- ✅ Container creation scripts in `provision/pct/create_lxc_base.sh`
- ✅ Ansible roles in `provision/ansible/roles/` for all services
- ✅ Environment variables externalized in `provision/pct/vars.env`
- ✅ No manual configuration required—full automation via scripts

**Compliance**: All infrastructure defined as code. Ansible playbooks are idempotent.

### Principle II: Service Isolation & Role-Based Security

**Status**: ✅ PASS

- ✅ One service per container: files-lxc (MinIO), pg-lxc (PostgreSQL), milvus-lxc (Milvus), agent-lxc (API gateway), ingest-lxc (worker + Redis)
- ✅ PostgreSQL RLS planned for multi-tenant isolation
- ✅ API gateway (agent-lxc) enforces RBAC before data access
- ✅ Network policies via container isolation (static IPs, no unnecessary routes)

**Compliance**: Clear security boundaries. Each container runs single primary service.

### Principle III: Observability & Debuggability

**Status**: ⚠️ PARTIAL

- ✅ Health endpoints required (spec FR-005, SC-003)
- ⚠️ Structured logging required but implementation details needed (JSON format, log levels)
- ✅ Critical operations logging specified (file uploads, embeddings, user actions)
- ⚠️ Log aggregation strategy not yet defined (journalctl per-container initially, future Prometheus/Grafana)

**Compliance**: Meets constitution requirements. Minor implementation details to be resolved in Phase 0 research.

### Principle IV: Extensibility & Modularity

**Status**: ✅ PASS

- ✅ New containers added via shell scripts + Ansible roles pattern
- ✅ liteLLM provides pluggable LLM provider architecture
- ✅ Application server supports custom apps
- ✅ Ansible roles are idempotent
- ✅ Service configuration via environment variables

**Compliance**: Platform designed for extension without core changes.

### Principle V: Test-Driven Infrastructure (TDI)

**Status**: ⚠️ PARTIAL

- ✅ Smoke tests defined (health checks, connection tests)
- ⚠️ Makefile verification targets not yet implemented (need `make verify`)
- ✅ Health endpoint checking required
- ⚠️ Database migration rollback procedures not yet documented
- ⚠️ Integration test automation not yet implemented

**Compliance**: Testing requirements clear, automation implementation needed in Phase 1.

### Principle VI: Documentation as Contract

**Status**: ✅ PASS

- ✅ README.md describes architecture
- ✅ QUICKSTART.md provides working provisioning commands
- ✅ Ansible roles will document purpose and variables (standard practice)
- ⚠️ API contracts (OpenAPI) to be generated in Phase 1
- ✅ Spec requires documentation updates with interface changes

**Compliance**: Documentation structure in place, API contracts to be formalized.

### Principle VII: Simplicity & Pragmatism

**Status**: ✅ PASS

- ✅ Standard tools: Ansible, PostgreSQL, MinIO, Milvus, Redis, liteLLM
- ✅ Static IP assignment (no custom service discovery)
- ✅ Redis Streams (no custom queue implementation)
- ✅ Microservices only where justified (file storage, DB, vector DB, API, worker)
- ✅ No premature optimization

**Compliance**: Technology choices follow boring, proven patterns.

### Overall Gate Assessment

**Phase 0 Gate**: ✅ PASS (proceed to research)

Minor gaps in observability implementation and test automation do not block planning. These are implementation details to be resolved in Phase 0/1, not architectural violations.

**Items to address in planning**:
1. Structured logging format specification (Phase 0 research)
2. Makefile test automation targets (Phase 1 design)
3. Database migration procedures (Phase 1 data-model)
4. API contract formalization (Phase 1 contracts)

## Project Structure

### Documentation (this feature)

```
specs/001-create-an-initial/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── agent-api.yaml   # OpenAPI spec for agent API
│   ├── file-api.yaml    # OpenAPI spec for file operations
│   └── search-api.yaml  # OpenAPI spec for search/retrieval
└── checklists/
    └── requirements.md  # Spec quality checklist (already created)
```

### Source Code (repository root)

The busibox project uses an **infrastructure platform** structure with multiple services across LXC containers:

```
busibox/
├── provision/
│   ├── pct/                         # Proxmox container creation
│   │   ├── create_lxc_base.sh      # Main provisioning script
│   │   └── vars.env                # Environment-specific variables (IPs, CTIDs, storage)
│   └── ansible/
│       ├── Makefile                # Orchestration targets (all, verify, role-specific)
│       ├── inventory/
│       │   └── hosts.yml           # Container IP addresses and groups
│       ├── site.yml                # Main playbook
│       └── roles/
│           ├── node_common/        # Base Node.js setup for services
│           ├── postgres/           # PostgreSQL with RLS schema
│           ├── minio/              # MinIO S3 storage
│           ├── milvus/             # Milvus vector DB (Docker-in-LXC)
│           ├── agent_api/          # Agent API gateway service
│           ├── ingest_worker/      # File processing worker
│           ├── deploywatch/        # Auto-deployment service
│           └── (future: app_server, openwebui)
│
├── tools/
│   └── milvus_init.py              # Vector DB initialization script
│
├── docs/
│   └── architecture.md             # System architecture documentation
│
├── README.md                       # Project overview
├── QUICKSTART.md                   # Provisioning instructions
│
└── .specify/                       # Speckit framework
    ├── memory/
    │   └── constitution.md         # Project constitution (v1.0.0)
    └── templates/                  # Feature templates
```

**Service code locations** (within respective containers, deployed via Ansible):

```
# agent-lxc container (API gateway)
/srv/agent/
├── src/
│   ├── main.py                    # FastAPI application entry
│   ├── routes/
│   │   ├── health.py              # Health check endpoint
│   │   ├── files.py               # File operations (presigned URLs)
│   │   ├── search.py              # Semantic search
│   │   └── agent.py               # Agent operations
│   ├── services/
│   │   ├── auth.py                # Authentication/RBAC
│   │   ├── milvus.py              # Vector DB client
│   │   ├── postgres.py            # Database client
│   │   ├── minio.py               # File storage client
│   │   └── llm.py                 # LLM gateway client (liteLLM)
│   └── models/
│       ├── user.py                # User/role models
│       ├── file.py                # File metadata
│       └── search.py              # Search request/response
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── requirements.txt
└── .env                           # Service configuration

# ingest-lxc container (worker)
/srv/ingest/
├── src/
│   ├── worker.py                  # Main worker process (Redis Streams consumer)
│   ├── processors/
│   │   ├── text_extraction.py    # PDF/DOCX/TXT extraction
│   │   ├── chunking.py            # Semantic text chunking
│   │   └── embedding.py           # LLM embedding generation
│   ├── services/
│   │   ├── milvus.py              # Vector DB writer
│   │   ├── postgres.py            # Metadata writer
│   │   ├── minio.py               # File retrieval
│   │   ├── redis.py               # Job queue
│   │   └── llm.py                 # LLM client
│   └── models/
│       ├── job.py                 # Ingestion job model
│       └── chunk.py               # Chunk model
├── tests/
├── requirements.txt
└── .env

# apps-lxc container (application server) - future
/srv/apps/
└── (user-deployed applications)
```

**Structure Decision**: 

This is an **infrastructure platform** project, not a traditional single-application codebase. The structure follows a container-per-service pattern where:

1. **Infrastructure code** (`provision/`) defines containers and service deployment
2. **Service code** lives within each container at `/srv/<service-name>/`
3. **Ansible roles** deploy and configure each service independently
4. **No monorepo** for service code—each service can be versioned independently and deployed via deploywatch

This structure aligns with Constitution Principle II (Service Isolation) and Principle IV (Extensibility).

## Complexity Tracking

*No violations requiring justification.*

The multi-container architecture is mandated by Constitution Principle II (Service Isolation & Role-Based Security). Each service in its own container is not complexity but a security and reliability requirement.

## Phase 0: Research & Technology Decisions

**Goal**: Resolve remaining technical unknowns and establish concrete implementation patterns.

### Research Tasks

1. **Structured Logging Format**
   - **Question**: What JSON logging schema should all services use?
   - **Research**: Compare common structured logging patterns (ECS, OpenTelemetry, custom)
   - **Output**: Standardized log schema with required fields (timestamp, level, service, message, trace_id, user_id, etc.)

2. **Database Migration Strategy**
   - **Question**: How should PostgreSQL schema migrations be versioned and rolled back?
   - **Research**: Evaluate tools (Alembic, Flyway, sqitch) vs manual versioned SQL scripts
   - **Output**: Migration approach with rollback procedures

3. **Health Check Implementation**
   - **Question**: What should health check endpoints return and how should they be tested?
   - **Research**: Best practices for health checks (liveness vs readiness), status codes, response format
   - **Output**: Standard health check contract and automated testing approach

4. **LLM Provider Integration**
   - **Question**: How should services discover and route to different LLM providers via liteLLM?
   - **Research**: liteLLM configuration patterns, model naming conventions, fallback strategies
   - **Output**: LLM gateway configuration and client library patterns

5. **Webhook Event Handling**
   - **Question**: How should MinIO webhook events trigger ingestion jobs reliably?
   - **Research**: MinIO webhook configuration, event payload format, retry mechanisms
   - **Output**: Webhook configuration and job queuing pattern

6. **Text Extraction Libraries**
   - **Question**: Which Python libraries for PDF/DOCX/TXT extraction are most reliable?
   - **Research**: Compare PyPDF2, pdfplumber, python-docx, textract
   - **Output**: Recommended extraction libraries with fallback strategies

7. **Chunking Strategy**
   - **Question**: What chunking algorithm balances semantic coherence with embedding efficiency?
   - **Research**: Token-based vs sentence-based vs paragraph-based chunking, overlap strategies
   - **Output**: Default chunk size, overlap, and boundary detection algorithm

8. **Ansible Testing**
   - **Question**: How to implement `make verify` target for infrastructure validation?
   - **Research**: Ansible testing approaches (Molecule, serverspec, simple bash scripts)
   - **Output**: Verification approach and Makefile targets

**Output**: `research.md` documenting all decisions with rationales.

## Phase 1: Data Model & API Contracts

**Goal**: Define schemas, APIs, and quickstart procedures.

### Data Model (data-model.md)

Entities to be detailed:

1. **User** (PostgreSQL)
   - Fields: id (UUID), username, email, password_hash, created_at, updated_at
   - Relationships: → Roles (many-to-many), → Files (one-to-many as owner)

2. **Role** (PostgreSQL)
   - Fields: id (UUID), name, permissions (JSONB), created_at
   - Permissions: file.upload, file.read, file.delete, search.query, agent.invoke

3. **File** (PostgreSQL metadata + MinIO object)
   - Fields: id (UUID), owner_id (FK User), filename, content_type, size_bytes, bucket, object_key, upload_at, status (pending, processing, indexed, failed)
   - Relationships: → User (owner), → Chunks (one-to-many)
   - RLS: Users see only files they own or have shared access to

4. **Chunk** (PostgreSQL metadata + Milvus vector)
   - Fields: id (UUID), file_id (FK File), chunk_index, content (text), token_count, created_at
   - Relationships: → File (parent), → Embedding (one-to-one in Milvus)
   - RLS: Inherit permissions from parent File

5. **Embedding** (Milvus collection)
   - Fields: id (same as Chunk UUID), vector (float array, dimension based on model), file_id, chunk_id, model_name, created_at
   - Index: HNSW or IVF_FLAT for vector similarity search

6. **IngestionJob** (Redis Streams + PostgreSQL job log)
   - Fields: id (UUID), file_id (FK File), status (queued, processing, completed, failed), started_at, completed_at, error_message, retry_count
   - Queue: Redis Streams key `jobs:ingestion`, consumer group `workers`

7. **LLMProvider** (Configuration, not stored in DB)
   - Fields: name, endpoint, models (array), status (active, inactive)
   - Config file: `/etc/litellm/config.yaml` or environment variables

8. **Agent** (Configuration, future feature)
   - Fields: id, name, workflow_definition (YAML/JSON), permissions
   - To be detailed when agent functionality is implemented

9. **Application** (nginx config)
   - Fields: name, domain/path, upstream_port, auth_required
   - Config: nginx reverse proxy rules

**State Transitions**:
- File: pending → processing → indexed (success) OR failed (error)
- IngestionJob: queued → processing → completed (success) OR failed (error with retries)

**Validation Rules**:
- User email must be unique and valid format
- File uploads limited to 100MB (configurable)
- Chunk content must be non-empty, max 2000 tokens
- Embedding vectors must match model dimension (e.g., 768 for sentence-transformers)

### API Contracts (contracts/)

Three OpenAPI 3.0 specifications:

1. **agent-api.yaml** (agent-lxc service)
   - `GET /health` - Health check
   - `POST /auth/login` - User authentication (returns JWT)
   - `GET /auth/me` - Current user info
   - `POST /files/upload` - Initiate file upload (returns presigned URL)
   - `GET /files/{file_id}` - Get file metadata
   - `GET /files/{file_id}/download` - Get presigned download URL
   - `DELETE /files/{file_id}` - Delete file
   - `POST /search` - Semantic search (query → results with chunks)
   - `POST /agent/invoke` - Invoke agent with query (RAG + LLM)

2. **file-api.yaml** (MinIO S3 API, standard)
   - Standard S3 API for presigned uploads/downloads
   - Reference AWS S3 API documentation

3. **search-api.yaml** (internal Milvus API, documented for reference)
   - Vector similarity search operations
   - Reference Milvus Python SDK

### Quickstart Guide (quickstart.md)

Step-by-step guide for:
1. Prerequisites (Proxmox host, Ansible on admin workstation)
2. Configure `provision/pct/vars.env` (IPs, CTIDs, template, storage)
3. Run `provision/pct/create_lxc_base.sh` on Proxmox host
4. Run `make all` from Ansible directory
5. Verification steps (health checks, test file upload)
6. Initialize Milvus with `tools/milvus_init.py`
7. Common troubleshooting

### Agent Context Update

After Phase 1 artifacts are generated, run:
```bash
.specify/scripts/bash/update-agent-context.sh cursor-agent
```

This updates `.specify/memory/CLAUDE.md` (or appropriate agent file) with:
- Technology stack from this plan
- API contract references
- Data model entities
- Recent feature additions

## Post-Phase 1: Constitution Re-Check

After design artifacts are complete, re-evaluate Constitution Check:

- **Principle III (Observability)**: Should move from PARTIAL to PASS after research defines logging schema
- **Principle V (TDI)**: Should move from PARTIAL to PASS after Makefile verify targets designed
- **Principle VI (Documentation)**: Should move to full PASS after API contracts generated

Expected outcome: **All principles PASS** before proceeding to implementation tasks.

## Next Steps

After this planning phase:

1. **Immediate**: Generate research.md (Phase 0)
2. **Immediate**: Generate data-model.md, contracts/, quickstart.md (Phase 1)
3. **Immediate**: Update agent context file
4. **Next command**: `/speckit.tasks` - Generate implementation task list from this plan
5. **Implementation**: Execute tasks in priority order (P1 infrastructure provisioning first)

## Notes

This plan documents **existing infrastructure** and establishes patterns for **completing missing components**. The busibox platform is partially implemented:

**Existing**:
- ✅ Container provisioning scripts (`provision/pct/`)
- ✅ Ansible role structure (`provision/ansible/roles/`)
- ✅ Basic services: postgres, minio, milvus, agent_api, ingest_worker, deploywatch
- ✅ README and QUICKSTART documentation

**To be completed** (via tasks):
- ⚠️ Full PostgreSQL schema with RLS
- ⚠️ Agent API implementation (routes, auth, RBAC)
- ⚠️ Ingest worker implementation (text extraction, chunking, embedding)
- ⚠️ Health check automation
- ⚠️ Integration tests
- ⚠️ OpenAPI contract documentation
- ⚠️ Application server container and nginx proxy

The implementation tasks will prioritize completing the P1 Infrastructure Provisioning user story first (full end-to-end provisioning), then incrementally add P2-P8 capabilities.
