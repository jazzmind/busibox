# Implementation Plan Summary

**Feature**: 004-updated-ingestion-service  
**Branch**: `004-updated-ingestion-service`  
**Status**: Planning Complete ✅  
**Date**: 2025-11-05

---

## Planning Phase Complete

All Phase 0 (Research) and Phase 1 (Design & Contracts) artifacts have been generated:

### ✅ Phase 0: Research

**File**: [research.md](./research.md)

**Key Decisions**:
1. **PDF Parsing**: Marker + TATR + page images for comprehensive text and visual extraction
2. **Embeddings**: Dense (text-embedding-3-small) + Sparse (Milvus BM25) + Visual (ColPali)
3. **Status Tracking**: SSE with PostgreSQL LISTEN/NOTIFY for efficient real-time updates
4. **Chunking**: 400-800 tokens with semantic boundaries and 10-15% overlap
5. **Vector Storage**: Milvus 2.6+ single collection with multi-field vectors
6. **Job Queue**: Redis Streams with consumer groups
7. **Error Handling**: Exponential backoff for transient errors, permanent failure marking
8. **Testing**: pytest + testcontainers for integration tests

All NEEDS CLARIFICATION items resolved with concrete technology choices and rationale.

---

### ✅ Phase 1: Design & Contracts

**Files Generated**:
1. **[data-model.md](./data-model.md)** - Database schemas, Milvus collection, storage layout
2. **[contracts/ingest-api.openapi.yaml](./contracts/ingest-api.openapi.yaml)** - Complete OpenAPI 3.0 specification
3. **[quickstart.md](./quickstart.md)** - Step-by-step deployment and testing guide

**Agent Context Updated**:
- `.cursor/rules/specify-rules.mdc` updated with Python 3.11+, FastAPI, Milvus 2.6+, PostgreSQL, Redis

---

## Constitution Check (Post-Design)

### ✅ All Gates Passed

**I. Infrastructure as Code**: All changes defined as Ansible roles and version-controlled scripts

**II. Service Isolation**: Ingestion API remains in ingest-lxc, internal-only, RLS enforced

**III. Observability**: Structured logging, health endpoints, SSE for real-time visibility

**IV. Extensibility**: Pluggable processors, support for multiple embedding models

**V. Test-Driven Infrastructure**: Integration tests with testcontainers, health checks

**VI. Documentation as Contract**: OpenAPI spec, data model, quickstart all generated

**VII. Simplicity & Pragmatism**: Standard stack (FastAPI, PostgreSQL, Milvus), complexity justified by requirements

**No violations**. All design decisions align with constitution principles.

---

## Implementation Readiness

### Ready to Implement

The planning phase provides everything needed for implementation:

1. **Technical decisions made**: All technology choices researched and justified
2. **Data models defined**: PostgreSQL tables, Milvus schema, MinIO structure
3. **API contract documented**: OpenAPI spec with all endpoints, schemas, examples
4. **Deployment guide created**: Step-by-step Ansible playbook execution
5. **Testing strategy defined**: Unit, integration, and end-to-end tests

### Next Steps (Phase 2)

Run `/busibox/speckit.tasks` to generate the task breakdown from this plan.

The tasks will cover:
1. Ansible role creation/updates (ingest_api, milvus schema, litellm models)
2. FastAPI application structure (routes, middleware, services)
3. Worker enhancements (processors, multi-vector embeddings)
4. Database migrations (PostgreSQL tables, Milvus collection)
5. Integration tests (testcontainers, end-to-end pipeline)

---

## Key Changes Summary

### Infrastructure Changes

**Ansible Roles**:
- **NEW**: `ingest_api` - FastAPI service with systemd unit
- **ENHANCED**: `ingest_worker` - Add Marker, TATR, ColPali dependencies
- **ENHANCED**: `milvus` - Deploy hybrid search schema with BM25 function
- **ENHANCED**: `litellm` - Add text-embedding-3-small configuration
- **ENHANCED** (optional): `vllm` - Add ColPali model for visual search

### Application Changes

**New Components**:
- `srv/ingest/src/api/` - FastAPI application (routes, middleware, services)
- `srv/ingest/src/shared/` - Shared models and configuration
- `srv/ingest/src/worker/processors/classifier.py` - Document classification
- `srv/ingest/src/worker/processors/metadata_extractor.py` - Metadata extraction
- `srv/ingest/src/worker/processors/colpali.py` - PDF visual embeddings

**Enhanced Components**:
- `srv/ingest/src/worker/processors/text_extractor.py` - Add Marker, TATR
- `srv/ingest/src/worker/processors/chunker.py` - Optimize for 400-800 tokens
- `srv/ingest/src/worker/processors/embedder.py` - Multi-vector generation
- `srv/ingest/src/worker/services/milvus_service.py` - Hybrid insert logic
- `srv/ingest/src/worker/services/postgres_service.py` - LISTEN/NOTIFY

### Database Changes

**PostgreSQL Tables** (3 new):
- `ingestion_files` - File metadata and extraction results
- `ingestion_status` - Real-time processing status
- `ingestion_chunks` - Chunk-level metadata

**Milvus Collection** (new schema):
- Single `documents` collection with dense, sparse (BM25), and multi-vector (ColPali) fields
- Automatic BM25 sparse vector generation via Milvus function
- Indexes for HNSW (dense, visual) and inverted index (sparse)

---

## Dependencies Added

### Python Packages

```
fastapi==0.104.1
uvicorn[standard]==0.24.0
sse-starlette==1.6.5
asyncpg==0.29.0
pymilvus==2.6.0
redis==5.0.1
boto3==1.29.0
marker-pdf==0.2.0
tatr==0.1.0
pdf2image==1.16.3
colpali==0.1.0
spacy==3.7.2
tiktoken==0.5.1
tenacity==8.2.3
pydantic==2.5.0
structlog==23.2.0
pytest==7.4.3
pytest-asyncio==0.21.1
testcontainers==3.7.1
```

### System Packages

```
poppler-utils  # For pdf2image
tesseract-ocr  # For OCR fallback
```

---

## Performance Characteristics

### Target Metrics

- **Concurrent uploads**: 50 files
- **Processing time**: <2 minutes for small docs (<10 pages)
- **Status update latency**: <2 seconds
- **Throughput**: 100 files/minute peak
- **Max file size**: 100MB

### Scalability

- **Horizontal worker scaling**: Add more workers via systemd (ingest-worker@{1..N})
- **Embedding batching**: Batch multiple chunks per API call
- **Milvus partitioning**: Partition by user_id for large-scale deployments
- **Redis Stream limits**: Set maxlen to prevent unbounded growth

---

## Testing Strategy

### Unit Tests

- Text extractors (PDF, DOCX, TXT)
- Chunking logic (semantic boundaries, token limits)
- Embedding generation (mock liteLLM)
- Classification (document type, language)

### Integration Tests

- Upload → MinIO storage
- Job queuing → Redis Streams
- Processing pipeline → Milvus insert
- SSE status streaming → PostgreSQL NOTIFY

### End-to-End Tests

- Full pipeline: upload → parse → chunk → embed → index → search
- Error scenarios: corrupted file, service unavailable, timeout
- Deletion: cascade across MinIO, Milvus, PostgreSQL

---

## Risk Mitigation

### Technical Risks

| Risk | Mitigation |
|------|------------|
| ColPali requires GPU | Make ColPali optional, text-only mode works without GPU |
| Embedding API rate limits | Implement exponential backoff, queue requests |
| Milvus schema migration | Create new collection, migrate data, deprecate old |
| Large file processing | Stream uploads to MinIO, chunk processing for memory efficiency |

### Operational Risks

| Risk | Mitigation |
|------|------------|
| Worker crashes mid-processing | Redis Streams auto-retry unacknowledged messages |
| PostgreSQL connection pool exhaustion | Use connection pooling with limits, async IO |
| Milvus memory usage | Monitor memory, adjust HNSW parameters (M, efConstruction) |
| Storage capacity | Monitor MinIO usage, implement retention policies |

---

## Documentation Artifacts

All planning artifacts are in `specs/004-updated-ingestion-service/`:

- ✅ `plan.md` - This implementation plan
- ✅ `research.md` - Technology decisions and best practices
- ✅ `data-model.md` - Database schemas and data structures
- ✅ `contracts/ingest-api.openapi.yaml` - API specification
- ✅ `quickstart.md` - Deployment and testing guide
- ⏳ `tasks.md` - Task breakdown (next step: run `/busibox/speckit.tasks`)

---

## Approval & Next Steps

### Planning Phase: ✅ COMPLETE

All required artifacts generated and constitution compliance verified.

### Ready for Task Breakdown

Run the following command to proceed to Phase 2 (task generation):

```bash
/busibox/speckit.tasks
```

This will:
1. Parse the implementation plan
2. Generate concrete tasks for each component
3. Create task dependencies and ordering
4. Output to `specs/004-updated-ingestion-service/tasks.md`

---

**Branch**: `004-updated-ingestion-service`  
**Specs Directory**: `/Users/wessonnenreich/Code/sonnenreich/busibox/specs/004-updated-ingestion-service`  
**Status**: Ready for implementation task generation

