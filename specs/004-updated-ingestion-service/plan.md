# Implementation Plan: Production-Grade Document Ingestion Service

**Branch**: `004-updated-ingestion-service` | **Date**: 2025-11-05 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/004-updated-ingestion-service/spec.md`

## Summary

Create a production-grade document ingestion service that processes uploaded documents through a multi-stage pipeline (parsing, classification, chunking, embedding) and prepares them for hybrid search by generating multiple vector representations (dense semantic, sparse BM25, visual ColPali for PDFs). The service provides real-time status tracking via Server-Sent Events, handles concurrent processing with queue-based workers, and supports graceful error handling with automatic retries. Content deduplication using SHA-256 hashing minimizes storage costs while vector reuse dramatically reduces processing time for duplicate uploads.

**Technical Approach**: Extend existing `srv/ingest` Python service with FastAPI API layer with chunked upload support for unlimited file sizes, enhance worker with multi-vector embedding generation and duplicate detection, update Milvus schema for hybrid search, configure liteLLM/vLLM for embedding models, and implement SSE status streaming with PostgreSQL LISTEN/NOTIFY.

## Clarifications Applied

Based on specification clarification session (2025-11-05), the following key decisions have been incorporated:

1. **File Size Limits**: Unlimited upload size using chunked strategy and stream processing (no hard limits)
2. **Processing Timeouts**: Dynamic timeout based on document size (small=5min, medium=10min, large=20min)
3. **Duplicate Handling**: Allow duplicates with deduplication at storage level using SHA-256 content hash
4. **Vector Reuse**: Reuse existing vectors for duplicate content to reduce compute costs and processing time (<10s for duplicates)
5. **Mixed Languages**: Detect primary language, store all detected languages, use language-aware chunking

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: FastAPI 0.104+, pymilvus 2.6+ (BM25 function support), redis-py 5.0+, boto3 (MinIO S3), psycopg 3.1+ (PostgreSQL async)  
**Storage**: PostgreSQL 15+ (metadata, status), Milvus 2.6+ (vectors with BM25), MinIO (S3-compatible file storage), Redis 7.0+ (Streams for job queue)  
**Testing**: pytest 7.4+, pytest-asyncio (async tests), testcontainers-python (integration tests)  
**Target Platform**: Linux containers (LXC on Proxmox), deployed to ingest-lxc (CTID 206)  
**Project Type**: Backend service (API + worker processes)  
**Performance Goals**: Process 50 concurrent uploads, complete small docs in 2 minutes (duplicates <10s via vector reuse), maintain <2s status update latency, dynamic timeouts (5/10/20min based on size)  
**Constraints**: Internal-only API (not exposed through proxy), must integrate with existing Milvus/PostgreSQL/MinIO/liteLLM services, chunked upload required for unlimited file size support  
**Scale/Scope**: Support 100 files/minute peak throughput, handle unlimited file sizes via chunked upload, process 10+ document formats, scale workers horizontally, content deduplication with SHA-256

**Additional Context**:
- Requires changes to Ansible scripts controlling `milvus-lxc` (schema updates for hybrid search)
- Requires additional embedding-specific models configured for vllm/liteLLM (text-embedding-3-small, ColPali)
- Chunked upload implementation required for unlimited file size support (stream processing to manage memory)
- Content hash (SHA-256) calculation required for deduplication at storage and vector layers
- Duplicate detection logic needed before processing to enable vector reuse (significant performance optimization)
- Language detection must support multiple languages per document for mixed-language content
- Dynamic timeout logic based on document size (<10 pages=5min, 10-50=10min, >50=20min)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Infrastructure as Code ✅

**Status**: COMPLIANT

- New FastAPI service defined as Ansible role `ingest_api` in `provision/ansible/roles/ingest_api/`
- Worker enhancements use existing `ingest_worker` Ansible role with updated configuration
- Milvus schema changes deployed via Ansible tasks in `milvus` role
- liteLLM/vLLM embedding model configuration managed in role defaults (`provision/ansible/roles/litellm/defaults/main.yml`)
- No manual configuration required—all changes version-controlled

### II. Service Isolation & Role-Based Security ✅

**Status**: COMPLIANT

- Ingestion API runs in dedicated `ingest-lxc` container (CTID 206)
- Internal-only API (not exposed through nginx proxy)
- User context passed via headers from apps-lxc
- PostgreSQL RLS enforces data isolation at database layer
- MinIO bucket policies control file access per user/group

### III. Observability & Debuggability ✅

**Status**: COMPLIANT

- FastAPI service exposes `/health` endpoint
- Structured logging (structlog) with JSON format: timestamp, level, service, file_id, stage, error
- SSE status tracking provides real-time pipeline visibility
- Critical operations logged: upload, stage transitions, failures
- Failed jobs log sufficient context: file_id, stage, error message, user_id

### IV. Extensibility & Modularity ✅

**Status**: COMPLIANT

- Processor classes (TextExtractor, Chunker, Embedder) are pluggable
- Support for adding new document formats by extending TextExtractor
- Multiple embedding models supported via liteLLM configuration
- Ansible roles are idempotent—can re-run without side effects
- Service discovery via environment variables (MILVUS_HOST, LITELLM_BASE_URL, etc.)

### V. Test-Driven Infrastructure ✅

**Status**: COMPLIANT

- Integration tests for end-to-end upload → processing → vector storage
- Unit tests for each processor (parsing, chunking, embedding)
- Health check validation script post-deployment
- Database migrations include rollback procedures
- pytest fixtures for testcontainers (PostgreSQL, Milvus, MinIO, Redis)

### VI. Documentation as Contract ✅

**Status**: COMPLIANT

- API contract documented in `contracts/ingest-api.openapi.yaml`
- `quickstart.md` provides working deployment and test commands
- Ansible role README documents configuration variables
- Service interfaces (FastAPI routes, worker processors) documented via docstrings
- Changes to schemas/APIs require documentation updates in same commit

### VII. Simplicity & Pragmatism ✅

**Status**: COMPLIANT

- Uses standard stack: FastAPI, PostgreSQL, Milvus, Redis, MinIO—no custom frameworks
- Redis Streams for queue (no need for complex message broker)
- Flat network discovery via environment variables (no service mesh)
- No premature optimization—single worker process initially, scale horizontally when needed
- Complexity justified by requirements: hybrid search (business need), SSE (UX requirement), multi-worker (performance requirement)

**Re-check after Phase 1**: Verify data model and API contracts remain aligned with constitution principles.

## Project Structure

### Documentation (this feature)

```
specs/004-updated-ingestion-service/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── ingest-api.openapi.yaml
└── tasks.md             # Phase 2 output (NOT created by this command)
```

### Source Code (repository root)

```
srv/ingest/
├── src/
│   ├── api/                      # NEW: FastAPI application
│   │   ├── main.py              # FastAPI app setup, middleware
│   │   ├── routes/              # API endpoints
│   │   │   ├── upload.py        # POST /upload
│   │   │   ├── status.py        # GET /status/{fileId} (SSE)
│   │   │   ├── files.py         # GET /files/{fileId}, DELETE /files/{fileId}
│   │   │   └── health.py        # GET /health
│   │   ├── middleware/          # Request middleware
│   │   │   ├── auth.py          # User context validation
│   │   │   ├── logging.py       # Structured logging
│   │   │   └── cors.py          # CORS configuration
│   │   └── services/            # Business logic
│   │       ├── minio.py         # MinIO S3 client wrapper
│   │       ├── redis.py         # Redis Streams wrapper
│   │       ├── status.py        # Status tracking service
│   │       └── permissions.py   # Permission validation
│   ├── worker/                   # EXISTING: Background worker (enhanced)
│   │   ├── main.py              # Worker entry point
│   │   ├── processors/          # Processing stages
│   │   │   ├── text_extractor.py      # ENHANCED: Add Marker, Unstructured, TATR
│   │   │   ├── classifier.py          # NEW: Document classification
│   │   │   ├── metadata_extractor.py  # NEW: Metadata extraction
│   │   │   ├── chunker.py             # ENHANCED: Optimize 400-800 tokens
│   │   │   ├── embedder.py            # ENHANCED: Multi-vector (dense + ColPali)
│   │   │   └── colpali.py             # NEW: PDF page visual embeddings
│   │   └── services/            # Worker services (enhanced)
│   │       ├── minio_service.py       # EXISTING (minor updates)
│   │       ├── postgres_service.py    # ENHANCED: Status updates, LISTEN/NOTIFY
│   │       ├── milvus_service.py      # ENHANCED: Multi-vector insert, BM25
│   │       └── status_service.py      # NEW: Shared status update logic
│   ├── shared/                   # NEW: Shared models/config
│   │   ├── models.py            # Pydantic models (File, Status, Chunk, etc.)
│   │   ├── config.py            # Configuration management
│   │   └── schemas.py           # Database/Milvus schemas
│   └── utils/                    # EXISTING
│       └── config.py            # Environment configuration
├── tests/
│   ├── api/                     # NEW: API tests
│   │   ├── test_upload.py
│   │   ├── test_status_sse.py
│   │   └── test_files.py
│   ├── worker/                  # ENHANCED: Worker tests
│   │   ├── test_extractors.py
│   │   ├── test_chunker.py
│   │   ├── test_embedder.py
│   │   └── test_colpali.py
│   ├── integration/             # NEW: End-to-end tests
│   │   └── test_pipeline.py
│   └── fixtures/                # NEW: Test fixtures
│       ├── sample_files/        # PDF, DOCX, TXT samples
│       └── containers.py        # Testcontainers setup
├── requirements.txt             # ENHANCED: Add FastAPI, SSE, new parsers
└── README.md                    # ENHANCED: API docs, deployment

provision/ansible/roles/
├── ingest_api/                  # NEW: Deploy FastAPI service
│   ├── tasks/
│   │   └── main.yml            # Install deps, systemd service
│   ├── templates/
│   │   ├── ingest-api.service.j2    # Systemd unit file
│   │   └── ingest-api.env.j2        # Environment variables
│   └── handlers/
│       └── main.yml            # Restart handler
├── ingest_worker/               # ENHANCED: Update worker config
│   └── tasks/
│       └── main.yml            # Add new Python deps, update env
├── milvus/                      # ENHANCED: Schema updates
│   ├── tasks/
│   │   └── main.yml            # Add schema migration task
│   └── files/
│       └── hybrid_schema.py    # NEW: Milvus hybrid collection setup
├── litellm/                     # ENHANCED: Embedding models
│   ├── defaults/
│   │   └── main.yml            # Add text-embedding-3-small config
│   └── templates/
│       └── config.yaml.j2      # Embedding model endpoints
└── vllm/                        # ENHANCED: ColPali model (optional)
    ├── defaults/
    │   └── main.yml            # ColPali model config
    └── tasks/
        └── models.yml          # Download ColPali weights
```

**Structure Decision**: Extended existing `srv/ingest` service with new `api/` subdirectory for FastAPI application while keeping worker in `worker/` subdirectory. This maintains separation of concerns (API vs worker) while sharing common code via `shared/` directory. Ansible roles follow existing pattern with new `ingest_api` role and enhancements to `ingest_worker`, `milvus`, `litellm`, and `vllm` roles.

## Complexity Tracking

No constitution violations require justification. All complexity is aligned with documented requirements:

- **Multi-vector embeddings**: Required by hybrid search specification (FR-021, FR-022, FR-023, FR-024)
- **SSE for status**: Required by real-time visibility user story (US1, FR-007 to FR-011)
- **Multiple worker processes**: Required by concurrency requirements (FR-037 to FR-041, SC-008)
- **Separate API + worker**: Required by operational independence (different resource profiles)
- **Chunked upload with streaming**: Required by unlimited file size support (Clarification #1, FR-002)
- **Content deduplication (SHA-256)**: Required for storage cost optimization (Clarification #3, FR-004, FR-029)
- **Vector reuse for duplicates**: Required for compute cost optimization and performance (Clarification #4, FR-012, FR-024)
- **Dynamic timeouts**: Required to balance large file processing with resource protection (Clarification #2, FR-036)
- **Multi-language detection**: Required for mixed-language document support (Clarification #5, FR-017, FR-026)

All technology choices (FastAPI, Redis Streams, Milvus BM25, ColPali, SHA-256 hashing) are justified by concrete requirements and use proven, standard libraries.
