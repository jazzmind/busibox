# Implementation Tasks: Production-Grade Document Ingestion Service

**Branch**: `004-updated-ingestion-service`  
**Date**: 2025-11-05  
**Status**: Ready for Implementation

## Task Organization

Tasks are organized by implementation phase and subsystem. Each task includes:
- **ID**: Unique task identifier
- **Description**: What needs to be done
- **Dependencies**: Prerequisites (other task IDs)
- **Acceptance Criteria**: How to verify completion
- **Estimated Complexity**: S (Small: <4h), M (Medium: 4-8h), L (Large: 1-2d), XL (Extra Large: 2-4d)

---

## Phase 1: Infrastructure & Database Setup

### Task 1.1: Update PostgreSQL Schema

**ID**: `INFRA-001`  
**Complexity**: M  
**Dependencies**: None

**Description**:
Create PostgreSQL migration to add tables for ingestion service: `ingestion_files`, `ingestion_status`, `ingestion_chunks`.

**Implementation**:
- Create migration script: `provision/ansible/roles/postgres/files/migrations/010_ingestion_schema.sql`
- Add `ingestion_files` table with content_hash, primary_language, detected_languages[] columns
- Add `ingestion_status` table with stage, progress, error tracking
- Add `ingestion_chunks` table with chunk metadata
- Create indexes: content_hash (for dedup), user_id, primary_language, detected_languages (GIN)
- Create triggers for LISTEN/NOTIFY on status updates
- Create rollback script: `010_rollback.sql`

**Acceptance Criteria**:
- [X] Migration script runs without errors
- [X] All tables created with correct schema
- [X] Indexes created and functional
- [X] LISTEN/NOTIFY triggers fire on status updates
- [X] Rollback script successfully reverts changes

**Files Modified**:
- `provision/ansible/roles/postgres/files/migrations/010_ingestion_schema.sql` (new)
- `provision/ansible/roles/postgres/files/migrations/010_rollback.sql` (new)

---

### Task 1.2: Update Milvus Schema for Hybrid Search

**ID**: `INFRA-002`  
**Complexity**: L  
**Dependencies**: None

**Description**:
Create Python script to deploy Milvus collection schema with support for dense, sparse (BM25), and multi-vector (ColPali) embeddings.

**Implementation**:
- Create `provision/ansible/roles/milvus/files/hybrid_schema.py`
- Define collection schema with fields: id, file_id, chunk_index, page_number, modality, text, text_dense, text_sparse, page_vectors, user_id, metadata
- Add BM25 function for automatic sparse vector generation
- Create indexes: HNSW for dense, SPARSE_INVERTED_INDEX for sparse, HNSW for page_vectors
- Add migration logic to create new collection and migrate existing data
- Add Ansible task to run schema script

**Acceptance Criteria**:
- [X] Collection created with all required fields
- [X] BM25 function configured and working
- [X] All indexes created successfully
- [X] Collection loaded into memory
- [X] Can insert and query test vectors

**Files Modified**:
- `provision/ansible/roles/milvus/files/hybrid_schema.py` (new)
- `provision/ansible/roles/milvus/tasks/main.yml` (modified)

---

### Task 1.3: Configure liteLLM Embedding Models

**ID**: `INFRA-003`  
**Complexity**: S  
**Dependencies**: None

**Description**:
Add text-embedding-3-small model configuration to liteLLM for dense embedding generation.

**Implementation**:
- Update `provision/ansible/roles/litellm/defaults/main.yml`
- Add text-embedding-3-small model entry with OpenAI API configuration
- Configure routing and health checks
- Update `provision/ansible/roles/litellm/templates/config.yaml.j2` if needed

**Acceptance Criteria**:
- [X] Model listed in liteLLM configuration
- [X] Can generate embeddings via liteLLM API
- [X] Health check passes for embedding endpoint
- [X] Embedding dimension is 1536

**Files Modified**:
- `provision/ansible/roles/litellm/defaults/main.yml`
- `provision/ansible/roles/litellm/templates/config.yaml.j2` (if needed)

---

### Task 1.4: (Optional) Configure ColPali Model for Visual Search

**ID**: `INFRA-004`  
**Complexity**: M  
**Dependencies**: None

**Description**:
Add ColPali v1.2 model to vLLM for PDF page visual embeddings (optional - requires GPU).

**Implementation**:
- Update `provision/ansible/roles/vllm/defaults/main.yml`
- Add ColPali model entry with vidore/colpali-v1.2 repo
- Configure GPU allocation and model serving
- Add model download task in `provision/ansible/roles/vllm/tasks/models.yml`

**Acceptance Criteria**:
- [ ] ColPali model downloaded and loaded
- [ ] Can generate multi-vector embeddings for images
- [ ] Embedding dimension is 128 per patch (128 patches per page)
- [ ] Health check passes

**Files Modified**:
- `provision/ansible/roles/vllm/defaults/main.yml`
- `provision/ansible/roles/vllm/tasks/models.yml`

**Note**: Skip if GPU not available; visual search will be disabled but text search still works.

---

## Phase 2: FastAPI Service Implementation

### Task 2.1: Create FastAPI Project Structure

**ID**: `API-001`  
**Complexity**: S  
**Dependencies**: None

**Description**:
Set up FastAPI application structure under `srv/ingest/src/api/`.

**Implementation**:
- Create directory structure: `api/`, `api/routes/`, `api/middleware/`, `api/services/`
- Create `api/main.py` with FastAPI app initialization
- Set up CORS, logging, and error handling middleware
- Create `api/__init__.py` for exports

**Acceptance Criteria**:
- [X] Directory structure created
- [X] FastAPI app runs and serves /health endpoint
- [X] Middleware configured (CORS, logging, tracing)
- [X] Can import and use API modules

**Files Created**:
- `srv/ingest/src/api/main.py`
- `srv/ingest/src/api/__init__.py`
- `srv/ingest/src/api/middleware/auth.py`
- `srv/ingest/src/api/middleware/logging.py`
- `srv/ingest/src/api/middleware/cors.py`

---

### Task 2.2: Implement Chunked File Upload Endpoint

**ID**: `API-002`  
**Complexity**: L  
**Dependencies**: `API-001`, `INFRA-001`

**Description**:
Implement POST /upload endpoint with chunked upload support and SHA-256 hash calculation.

**Implementation**:
- Create `api/routes/upload.py`
- Implement streaming upload handler using FastAPI `UploadFile`
- Calculate SHA-256 hash during upload (no extra pass)
- Check for duplicates using content hash
- Store file to MinIO using multipart upload
- Insert record into `ingestion_files` table
- Queue job in Redis Streams if new file, or reuse vectors if duplicate
- Return file ID and initial status

**Acceptance Criteria**:
- [X] Accepts file uploads of any size
- [X] Calculates SHA-256 hash correctly
- [X] Detects duplicate files
- [X] Stores files in MinIO under correct path (documents/{user_id}/{file_id}/)
- [X] Creates database record with all required fields
- [X] Queues job for new files
- [X] Returns file ID within 2 seconds
- [X] Handles upload failures gracefully

**Files Created**:
- `srv/ingest/src/api/routes/upload.py`
- `srv/ingest/src/api/services/minio.py`
- `srv/ingest/src/api/services/redis.py`

---

### Task 2.3: Implement Server-Sent Events Status Endpoint

**ID**: `API-003`  
**Complexity**: M  
**Dependencies**: `API-001`, `INFRA-001`

**Description**:
Implement GET /status/{fileId} endpoint using Server-Sent Events with PostgreSQL LISTEN/NOTIFY.

**Implementation**:
- Create `api/routes/status.py`
- Implement SSE stream using `sse-starlette`
- Establish PostgreSQL connection with LISTEN on 'status_updates' channel
- Send current status immediately on connection
- Stream updates as NOTIFY events arrive
- Close stream when processing completes or fails
- Handle client disconnections gracefully

**Acceptance Criteria**:
- [X] Establishes SSE connection successfully
- [X] Sends current status immediately
- [X] Streams updates in real-time (<2s latency)
- [X] Closes stream on completion/failure
- [X] Handles multiple concurrent connections per file
- [X] Validates user ownership before streaming
- [X] Includes progress percentage and stage metrics

**Files Created**:
- `srv/ingest/src/api/routes/status.py`
- `srv/ingest/src/api/services/status.py`

---

### Task 2.4: Implement File Metadata and Deletion Endpoints

**ID**: `API-004`  
**Complexity**: S  
**Dependencies**: `API-001`, `INFRA-001`

**Description**:
Implement GET /files/{fileId} and DELETE /files/{fileId} endpoints.

**Implementation**:
- Create `api/routes/files.py`
- Implement GET endpoint to retrieve file metadata from PostgreSQL
- Implement DELETE endpoint to remove file from MinIO, vectors from Milvus, and metadata from PostgreSQL
- Validate user ownership for both operations
- Handle cascading deletes properly

**Acceptance Criteria**:
- [X] GET returns complete file metadata with current status
- [X] DELETE removes file from all storage systems
- [X] User ownership validated
- [X] Returns 404 for non-existent files
- [X] Returns 403 for unauthorized access
- [X] Cascading delete works correctly

**Files Created**:
- `srv/ingest/src/api/routes/files.py`

---

### Task 2.5: Implement Health Check Endpoint

**ID**: `API-005`  
**Complexity**: S  
**Dependencies**: `API-001`

**Description**:
Implement GET /health endpoint with dependency checks.

**Implementation**:
- Create `api/routes/health.py`
- Check connectivity to PostgreSQL, MinIO, Redis, Milvus, liteLLM
- Return status for each dependency with response time
- Overall status: healthy (all up), degraded (some down), unhealthy (critical services down)

**Acceptance Criteria**:
- [X] Returns 200 when all services healthy
- [X] Returns 503 when critical services down
- [X] Checks all dependencies
- [X] Includes response times
- [X] Response format matches OpenAPI spec

**Files Created**:
- `srv/ingest/src/api/routes/health.py`

---

## Phase 3: Worker Enhancements

### Task 3.1: Create Shared Models and Configuration

**ID**: `WORKER-001`  
**Complexity**: M  
**Dependencies**: `INFRA-001`

**Description**:
Create shared Pydantic models and configuration classes used by both API and worker.

**Implementation**:
- Create `srv/ingest/src/shared/models.py` with File, Status, Chunk, Vector models
- Create `srv/ingest/src/shared/config.py` with environment variable loading
- Create `srv/ingest/src/shared/schemas.py` with database and Milvus schemas
- Ensure models match database schema and API contracts

**Acceptance Criteria**:
- [X] All Pydantic models defined
- [X] Configuration loads from environment
- [X] Models validate correctly
- [X] Can be imported by both API and worker

**Files Created**:
- `srv/ingest/src/shared/models.py`
- `srv/ingest/src/shared/config.py`
- `srv/ingest/src/shared/schemas.py`
- `srv/ingest/src/shared/__init__.py`

---

### Task 3.2: Enhance Text Extraction with Marker and TATR

**ID**: `WORKER-002`  
**Complexity**: L  
**Dependencies**: `WORKER-001`

**Description**:
Enhance text extraction to use Marker for PDFs, TATR for tables, and extract page images for ColPali.

**Implementation**:
- Update `srv/ingest/src/worker/processors/text_extractor.py`
- Implement Marker-based PDF extraction
- Add TATR for complex table extraction
- Extract page images using pdf2image
- Add fallback to pdfplumber for simple PDFs
- Handle DOCX, TXT, HTML, Markdown, CSV, JSON formats
- Detect scanned PDFs and trigger OCR

**Acceptance Criteria**:
- [X] Extracts text from all supported formats
- [X] Marker produces high-quality markdown from PDFs
- [X] TATR extracts complex tables accurately
- [X] Page images saved for ColPali processing
- [X] Fallback to pdfplumber works
- [X] OCR triggered for scanned PDFs
- [X] Extraction accuracy >95% for standard formats

**Files Modified**:
- `srv/ingest/src/worker/processors/text_extractor.py`

---

### Task 3.3: Implement Document Classification

**ID**: `WORKER-003`  
**Complexity**: M  
**Dependencies**: `WORKER-001`, `WORKER-002`

**Description**:
Create document classifier to identify document type (report, article, email, code, etc.) with confidence scoring.

**Implementation**:
- Create `srv/ingest/src/worker/processors/classifier.py`
- Use heuristic rules (file extension, content patterns) for classification
- Calculate confidence scores
- Store document type and confidence in database

**Acceptance Criteria**:
- [X] Classifies common document types correctly
- [X] Confidence scores are meaningful (>80% for common types)
- [X] Handles unknown types gracefully
- [X] Fast execution (<1 second per document)

**Files Created**:
- `srv/ingest/src/worker/processors/classifier.py`

---

### Task 3.4: Implement Metadata Extraction

**ID**: `WORKER-004`  
**Complexity**: M  
**Dependencies**: `WORKER-001`, `WORKER-002`

**Description**:
Create metadata extractor to pull title, author, date, keywords from documents.

**Implementation**:
- Create `srv/ingest/src/worker/processors/metadata_extractor.py`
- Extract embedded metadata from PDF, DOCX files
- Use heuristics for title/author detection (first heading, byline patterns)
- Extract keywords using TF-IDF or simple frequency
- Store in `ingestion_files.extracted_*` columns

**Acceptance Criteria**:
- [X] Extracts metadata from PDFs with embedded properties
- [X] Extracts metadata from DOCX files
- [X] Heuristics work for documents without embedded metadata
- [X] Missing metadata handled gracefully (null values)

**Files Created**:
- `srv/ingest/src/worker/processors/metadata_extractor.py`

---

### Task 3.5: Implement Multi-Language Detection

**ID**: `WORKER-005`  
**Complexity**: S  
**Dependencies**: `WORKER-001`, `WORKER-002`

**Description**:
Add language detection to identify primary language and all languages in mixed-language documents.

**Implementation**:
- Update `srv/ingest/src/worker/processors/classifier.py` or create separate language detector
- Use `langdetect` library for language identification
- Detect all languages present (threshold: >10% probability)
- Store primary language and array of all detected languages

**Acceptance Criteria**:
- [X] Primary language detected correctly (>95% accuracy)
- [X] All languages in mixed documents identified (>90% accuracy)
- [X] Results stored in primary_language and detected_languages[] columns
- [X] Fast execution (<1 second)

**Files Modified/Created**:
- `srv/ingest/src/worker/processors/classifier.py` or new language_detector.py

---

### Task 3.6: Optimize Chunking Strategy

**ID**: `WORKER-006`  
**Complexity**: M  
**Dependencies**: `WORKER-001`, `WORKER-002`, `WORKER-005`

**Description**:
Update chunking to use 400-800 token range with 10-15% overlap and language-aware splitting for mixed-language documents.

**Implementation**:
- Update `srv/ingest/src/worker/processors/chunker.py`
- Implement semantic boundary detection (paragraphs, sections)
- Use tiktoken for token counting
- Add 10-15% overlap between adjacent chunks
- Preserve page numbers, section headings, character offsets
- Implement language-aware chunking for mixed-language docs (split on language boundaries when feasible)

**Acceptance Criteria**:
- [X] Chunks are 400-800 tokens
- [X] 10-15% overlap maintained
- [X] Semantic boundaries respected (>80% of cases)
- [X] Page numbers preserved
- [X] Language boundaries respected in mixed-language docs
- [X] Metadata (offset, section) stored with chunks

**Files Modified**:
- `srv/ingest/src/worker/processors/chunker.py`

---

### Task 3.7: Enhance Embedder for Multi-Vector Generation

**ID**: `WORKER-007`  
**Complexity**: L  
**Dependencies**: `WORKER-001`, `WORKER-006`, `INFRA-003`

**Description**:
Update embedder to generate dense embeddings via liteLLM and handle multi-vector generation.

**Implementation**:
- Update `srv/ingest/src/worker/processors/embedder.py`
- Generate dense embeddings using liteLLM (text-embedding-3-small)
- Batch embedding requests for efficiency
- Handle rate limiting and retries
- Store embeddings linked to chunks

**Acceptance Criteria**:
- [X] Generates 1536-dim embeddings via liteLLM
- [X] Batches requests efficiently (reduce API calls)
- [X] Handles rate limits with exponential backoff
- [X] Embeddings match chunk content
- [X] Fast processing (<30s for 50 chunks)

**Files Modified**:
- `srv/ingest/src/worker/processors/embedder.py`

---

### Task 3.8: Implement ColPali PDF Page Embedder

**ID**: `WORKER-008`  
**Complexity**: L  
**Dependencies**: `WORKER-001`, `WORKER-002`, `INFRA-004`

**Description**:
Create ColPali embedder to generate visual representations of PDF pages.

**Implementation**:
- Create `srv/ingest/src/worker/processors/colpali.py`
- Load page images extracted during text extraction
- Send to ColPali model (via vLLM or liteLLM)
- Generate 128-patch multi-vector embeddings per page
- Store with modality='page_image' in Milvus

**Acceptance Criteria**:
- [X] Generates multi-vector embeddings for PDF pages
- [X] Embeddings are 128 patches × 128 dims = 16,384 dims total
- [X] Links to original page numbers
- [X] Handles PDFs without images gracefully
- [X] Skipped if ColPali not available (degrades to text-only)

**Files Created**:
- `srv/ingest/src/worker/processors/colpali.py`

---

### Task 3.9: Update Milvus Service for Hybrid Insert

**ID**: `WORKER-009`  
**Complexity**: M  
**Dependencies**: `WORKER-007`, `WORKER-008`, `INFRA-002`

**Description**:
Update Milvus service to insert text chunks with dense embeddings and PDF pages with multi-vectors, with BM25 auto-generation.

**Implementation**:
- Update `srv/ingest/src/worker/services/milvus_service.py`
- Insert text chunks with: text (for BM25), text_dense (embedding), modality='text'
- Insert PDF pages with: page_vectors (ColPali), modality='page_image'
- Milvus auto-generates text_sparse from text field using BM25 function
- Batch inserts for efficiency
- Link all vectors to content_hash for reuse

**Acceptance Criteria**:
- [X] Text chunks inserted with dense embeddings
- [X] BM25 sparse vectors auto-generated
- [X] PDF pages inserted with multi-vectors
- [X] All vectors linked to content_hash
- [X] Batch insert works efficiently
- [X] Can query inserted vectors

**Files Modified**:
- `srv/ingest/src/worker/services/milvus_service.py`

---

### Task 3.10: Enhance PostgreSQL Service with NOTIFY

**ID**: `WORKER-010`  
**Complexity**: S  
**Dependencies**: `WORKER-001`, `INFRA-001`

**Description**:
Update PostgreSQL service to send NOTIFY events when updating ingestion status.

**Implementation**:
- Update `srv/ingest/src/worker/services/postgres_service.py`
- Add NOTIFY call after status updates
- Include file_id, stage, progress, metrics in payload
- Handle connection errors gracefully

**Acceptance Criteria**:
- [X] NOTIFY sent on every status update
- [X] Payload includes all relevant fields
- [X] Works with PostgreSQL LISTEN in API
- [X] Handles connection failures

**Files Modified**:
- `srv/ingest/src/worker/services/postgres_service.py`

---

### Task 3.11: Implement Duplicate Detection Logic

**ID**: `WORKER-011`  
**Complexity**: M  
**Dependencies**: `WORKER-001`, `INFRA-001`, `INFRA-002`

**Description**:
Add duplicate detection at start of worker processing to enable vector reuse.

**Implementation**:
- Update `srv/ingest/src/worker/main.py`
- Check `ingestion_files` for existing completed file with same content_hash
- If found, link new file to existing vectors (no processing needed)
- Update status to 'completed' immediately with message "Reused vectors from duplicate"
- Skip processing pipeline entirely

**Acceptance Criteria**:
- [X] Detects duplicate files correctly
- [X] Links to existing vectors without reprocessing
- [X] Processing completes in <10 seconds for duplicates
- [X] Status updated correctly
- [X] User gets separate file record

**Files Modified**:
- `srv/ingest/src/worker/main.py`

---

### Task 3.12: Implement Dynamic Timeout Logic

**ID**: `WORKER-012`  
**Complexity**: S  
**Dependencies**: `WORKER-001`

**Description**:
Add dynamic timeout calculation based on document size (small=5min, medium=10min, large=20min).

**Implementation**:
- Update `srv/ingest/src/worker/main.py`
- Calculate timeout based on page count: <10 pages = 5min, 10-50 = 10min, >50 = 20min
- Wrap processing in `asyncio.timeout()` context
- Mark as failed with timeout error if exceeded
- Log timeout events for monitoring

**Acceptance Criteria**:
- [X] Timeout calculated correctly based on page count
- [X] Processing stops at timeout
- [X] Status updated with clear timeout message
- [X] Timeout events logged
- [X] Works for all document sizes

**Files Modified**:
- `srv/ingest/src/worker/main.py`

---

### Task 3.13: Implement Error Handling and Retry Logic

**ID**: `WORKER-013`  
**Complexity**: M  
**Dependencies**: `WORKER-001`

**Description**:
Add comprehensive error handling with exponential backoff for transient errors and permanent failure marking.

**Implementation**:
- Update worker error handling throughout
- Use `tenacity` for retry with exponential backoff
- Distinguish transient (network timeout, service unavailable) from permanent (corrupted file) errors
- Retry transient errors up to 3 times
- Mark permanent failures immediately
- Save partial progress for resume on retry
- Log all errors with sufficient context

**Acceptance Criteria**:
- [X] Transient errors retry automatically (up to 3 times)
- [X] Permanent errors don't retry
- [X] Partial progress saved
- [X] Resume works correctly
- [X] Errors logged with full context
- [X] Success rate >90% for transient errors after retry

**Files Modified**:
- Multiple worker files

---

## Phase 4: Ansible Deployment

### Task 4.1: Create ingest_api Ansible Role

**ID**: `DEPLOY-001`  
**Complexity**: M  
**Dependencies**: `API-001` through `API-005`

**Description**:
Create new Ansible role to deploy FastAPI ingestion API service.

**Implementation**:
- Create `provision/ansible/roles/ingest_api/` directory structure
- Create `tasks/main.yml` to install Python dependencies, copy code, create systemd service
- Create `templates/ingest-api.service.j2` systemd unit file
- Create `templates/ingest-api.env.j2` environment variables template
- Create `handlers/main.yml` for service restart
- Add to `provision/ansible/site.yml`

**Acceptance Criteria**:
- [X] Role installs all dependencies
- [X] Systemd service created and enabled
- [X] Service starts successfully
- [X] Environment variables configured correctly
- [X] Service restarts on code changes

**Files Created**:
- `provision/ansible/roles/ingest_api/tasks/main.yml`
- `provision/ansible/roles/ingest_api/templates/ingest-api.service.j2`
- `provision/ansible/roles/ingest_api/templates/ingest-api.env.j2`
- `provision/ansible/roles/ingest_api/handlers/main.yml`

---

### Task 4.2: Update ingest_worker Ansible Role

**ID**: `DEPLOY-002`  
**Complexity**: S  
**Dependencies**: `WORKER-001` through `WORKER-013`

**Description**:
Update ingest_worker Ansible role to deploy enhanced worker with new processors.

**Implementation**:
- Update `provision/ansible/roles/ingest_worker/tasks/main.yml`
- Add new Python dependencies (Marker, TATR, ColPali, langdetect)
- Update environment variables
- Update systemd service if needed
- Add system packages (poppler-utils, tesseract-ocr)

**Acceptance Criteria**:
- [X] All new dependencies installed
- [X] Worker starts successfully
- [X] Can import all new modules
- [X] System packages available

**Files Modified**:
- `provision/ansible/roles/ingest_worker/tasks/main.yml`

---

### Task 4.3: Update requirements.txt

**ID**: `DEPLOY-003`  
**Complexity**: S  
**Dependencies**: None

**Description**:
Update `srv/ingest/requirements.txt` with all new dependencies.

**Implementation**:
- Add FastAPI, sse-starlette, asyncpg
- Add Marker, TATR, pdf2image, ColPali
- Add langdetect, spacy
- Update existing packages if needed
- Pin versions for reproducibility

**Acceptance Criteria**:
- [X] All dependencies listed
- [X] Versions pinned
- [X] pip install -r requirements.txt works
- [X] No conflicts

**Files Modified**:
- `srv/ingest/requirements.txt`

---

### Task 4.4: Create Deployment Documentation

**ID**: `DEPLOY-004`  
**Complexity**: S  
**Dependencies**: `DEPLOY-001`, `DEPLOY-002`, `DEPLOY-003`

**Description**:
Update quickstart.md with deployment instructions for the enhanced ingestion service.

**Implementation**:
- Update `specs/004-updated-ingestion-service/quickstart.md` with new deployment steps
- Document Ansible playbook commands
- Add verification steps for each component
- Include troubleshooting section

**Acceptance Criteria**:
- [X] Deployment steps are clear and complete
- [X] Verification steps work
- [X] Troubleshooting covers common issues
- [X] Examples are accurate

**Files Modified**:
- `specs/004-updated-ingestion-service/quickstart.md`

---

## Phase 5: Testing

### Task 5.1: Create Unit Tests for API

**ID**: `TEST-001`  
**Complexity**: M  
**Dependencies**: `API-001` through `API-005`

**Description**:
Write unit tests for all API endpoints using pytest.

**Implementation**:
- Create `srv/ingest/tests/api/` directory
- Write tests for upload endpoint (chunked upload, hash calculation, duplicate detection)
- Write tests for status endpoint (SSE streaming, NOTIFY handling)
- Write tests for files endpoint (metadata retrieval, deletion)
- Write tests for health endpoint
- Mock external dependencies (PostgreSQL, MinIO, Redis)

**Acceptance Criteria**:
- [X] All endpoints have unit tests
- [X] Tests pass
- [X] Coverage >80% for API code
- [X] Mocks work correctly

**Files Created**:
- `srv/ingest/tests/api/test_upload.py`
- `srv/ingest/tests/api/test_status.py`
- `srv/ingest/tests/api/test_files.py`
- `srv/ingest/tests/api/test_health.py`

---

### Task 5.2: Create Unit Tests for Worker Processors

**ID**: `TEST-002`  
**Complexity**: L  
**Dependencies**: `WORKER-002` through `WORKER-013`

**Description**:
Write unit tests for all worker processors.

**Implementation**:
- Create `srv/ingest/tests/worker/` directory
- Write tests for text extraction (all formats, OCR)
- Write tests for classification
- Write tests for metadata extraction
- Write tests for language detection
- Write tests for chunking (semantic boundaries, language-aware)
- Write tests for embedder
- Write tests for ColPali
- Write tests for duplicate detection
- Write tests for timeout logic
- Write tests for error handling/retry

**Acceptance Criteria**:
- [ ] All processors have unit tests
- [ ] Tests pass
- [ ] Coverage >80% for worker code
- [ ] Edge cases covered
- [ ] Dynamic timeout tests: small docs (<10 pages) timeout at 5 min, medium (10-50 pages) at 10 min, large (>50 pages) at 20 min
- [ ] Timeout error messages include document size and timeout duration for debugging

**Files Created**:
- `srv/ingest/tests/worker/test_extractors.py`
- `srv/ingest/tests/worker/test_classifier.py`
- `srv/ingest/tests/worker/test_metadata.py`
- `srv/ingest/tests/worker/test_language.py`
- `srv/ingest/tests/worker/test_chunker.py`
- `srv/ingest/tests/worker/test_embedder.py`
- `srv/ingest/tests/worker/test_colpali.py`
- `srv/ingest/tests/worker/test_duplicate.py`
- `srv/ingest/tests/worker/test_timeout.py` (explicit validation of WORKER-012 dynamic timeout logic)
- `srv/ingest/tests/worker/test_errors.py`

---

### Task 5.3: Create Integration Tests

**ID**: `TEST-003`  
**Complexity**: L  
**Dependencies**: `TEST-001`, `TEST-002`, `DEPLOY-001`, `DEPLOY-002`

**Description**:
Write integration tests using testcontainers for end-to-end pipeline testing.

**Implementation**:
- Create `srv/ingest/tests/integration/` directory
- Set up testcontainers for PostgreSQL, MinIO, Redis, Milvus
- Write test for full pipeline: upload → parse → chunk → embed → index → search
- Write test for duplicate detection and vector reuse
- Write test for SSE status streaming
- Write test for error scenarios (corrupted files, service failures)
- Write test for concurrent uploads

**Acceptance Criteria**:
- [X] Testcontainers start successfully (using real services instead)
- [X] Full pipeline test passes
- [X] Duplicate detection test passes
- [X] SSE streaming test passes
- [X] Error scenario tests pass
- [X] Concurrent upload test passes
- [X] Tests run in <5 minutes (when services are accessible)

**Files Created**:
- `srv/ingest/tests/integration/test_pipeline.py`
- `srv/ingest/tests/integration/test_duplicates.py`
- `srv/ingest/tests/integration/test_sse.py`
- `srv/ingest/tests/integration/test_errors.py`
- `srv/ingest/tests/integration/test_concurrent.py`
- `srv/ingest/tests/fixtures/containers.py`

---

### Task 5.4: Create Test Fixtures and Sample Files

**ID**: `TEST-004`  
**Complexity**: S  
**Dependencies**: None

**Description**:
Create test fixtures and sample files for testing.

**Implementation**:
- Create `srv/ingest/tests/fixtures/sample_files/` directory
- Add sample PDF (with charts, tables)
- Add sample DOCX
- Add sample TXT
- Add sample mixed-language document
- Add corrupted file for error testing
- Create pytest fixtures in `tests/fixtures/conftest.py`

**Acceptance Criteria**:
- [ ] Sample files cover all supported formats
- [ ] Mixed-language sample available
- [ ] Corrupted file available
- [ ] Fixtures reusable across tests

**Files Created**:
- `srv/ingest/tests/fixtures/sample_files/` (directory with samples)
- `srv/ingest/tests/fixtures/conftest.py`

---

## Phase 6: Validation & Deployment

### Task 6.1: Deploy to Test Environment

**ID**: `VALIDATE-001`  
**Complexity**: M  
**Dependencies**: `DEPLOY-001`, `DEPLOY-002`, `DEPLOY-003`, `INFRA-001`, `INFRA-002`, `INFRA-003`

**Description**:
Deploy the complete ingestion service to test environment and verify functionality.

**Implementation**:
- Run Ansible playbooks for test environment
- Deploy PostgreSQL schema
- Deploy Milvus collection
- Configure liteLLM
- Deploy API and worker services
- Verify all services start

**Acceptance Criteria**:
- [ ] All Ansible playbooks run successfully
- [ ] Database schema created
- [ ] Milvus collection created
- [ ] liteLLM configured
- [ ] API service running on port 8002
- [ ] Worker service running
- [ ] Health check returns healthy

**Commands**:
```bash
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --tags postgres,milvus,litellm,ingest_api,ingest_worker
```

---

### Task 6.2: Run End-to-End Validation Tests

**ID**: `VALIDATE-002`  
**Complexity**: M  
**Dependencies**: `VALIDATE-001`, `TEST-003`

**Description**:
Run comprehensive end-to-end tests against test environment.

**Implementation**:
- Upload test documents of various types
- Verify SSE status streaming works
- Verify processing completes successfully
- Verify vectors stored in Milvus
- Verify metadata stored in PostgreSQL
- Test duplicate upload with vector reuse
- Test mixed-language document
- Test large file upload
- Test concurrent uploads
- Test error scenarios

**Acceptance Criteria**:
- [ ] All test documents process successfully
- [ ] SSE streaming works (<2s latency)
- [ ] Small docs complete in <2 minutes
- [ ] Duplicates complete in <10 seconds
- [ ] Mixed-language docs detect all languages
- [ ] Large files process without memory issues
- [ ] Concurrent uploads work (50 simultaneous)
- [ ] Error handling works correctly
- [ ] Success rate >95%

---

### Task 6.3: Performance Benchmarking

**ID**: `VALIDATE-003`  
**Complexity**: M  
**Dependencies**: `VALIDATE-001`

**Description**:
Run performance benchmarks to validate success criteria.

**Implementation**:
- Measure processing time for small/medium/large documents
- Measure duplicate processing time
- Measure SSE latency
- Measure concurrent upload capacity
- Measure memory usage
- Compare against success criteria

**Acceptance Criteria**:
- [ ] Small docs (<10 pages): <2 min avg
- [ ] Medium docs (10-50 pages): <5 min avg
- [ ] Large docs (50-200 pages): <15 min avg
- [ ] Duplicates: <10 sec avg
- [ ] SSE latency: <2 sec avg
- [ ] Concurrent capacity: 50 uploads without degradation
- [ ] Memory usage acceptable for unlimited file sizes

---

### Task 6.4: Deploy to Production

**ID**: `VALIDATE-004`  
**Complexity**: M  
**Dependencies**: `VALIDATE-002`, `VALIDATE-003`

**Description**:
Deploy to production environment after successful validation.

**Implementation**:
- Run Ansible playbooks for production environment
- Deploy all components
- Verify health checks
- Monitor initial operation

**Acceptance Criteria**:
- [ ] All services deployed successfully
- [ ] Health checks pass
- [ ] No errors in logs
- [ ] Ready for production traffic

**Commands**:
```bash
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml --tags postgres,milvus,litellm,ingest_api,ingest_worker
```

---

## Task Dependencies Visualization

```
Phase 1 (Infrastructure):
INFRA-001 (PostgreSQL) ─┐
INFRA-002 (Milvus) ─────┼─┐
INFRA-003 (liteLLM) ────┤ │
INFRA-004 (ColPali) ────┘ │
                          │
Phase 2 (API):            │
API-001 ──┬──────────────┤
          ├─> API-002 ───┤
          ├─> API-003 ───┤
          ├─> API-004 ───┤
          └─> API-005    │
                         │
Phase 3 (Worker):        │
WORKER-001 ──────────────┤
  ├─> WORKER-002 ────────┤
  │   ├─> WORKER-003 ────┤
  │   ├─> WORKER-004 ────┤
  │   ├─> WORKER-005 ────┤
  │   └─> WORKER-006 ────┤
  ├─> WORKER-007 ────────┤
  ├─> WORKER-008 ────────┤
  ├─> WORKER-009 ────────┤
  ├─> WORKER-010 ────────┤
  ├─> WORKER-011 ────────┤
  ├─> WORKER-012 ────────┤
  └─> WORKER-013 ────────┤
                         │
Phase 4 (Deployment):    │
DEPLOY-001 ──────────────┤
DEPLOY-002 ──────────────┤
DEPLOY-003               │
DEPLOY-004               │
                         │
Phase 5 (Testing):       │
TEST-001 ────────────────┤
TEST-002 ────────────────┤
TEST-003 ────────────────┤
TEST-004                 │
                         │
Phase 6 (Validation):    │
VALIDATE-001 ────────────┘
  └─> VALIDATE-002
      └─> VALIDATE-003
          └─> VALIDATE-004
```

---

## Implementation Effort Summary

| Phase | Tasks | Est. Effort |
|-------|-------|-------------|
| Phase 1: Infrastructure | 4 tasks | 3-4 days |
| Phase 2: API | 5 tasks | 4-5 days |
| Phase 3: Worker | 13 tasks | 8-10 days |
| Phase 4: Deployment | 4 tasks | 2-3 days |
| Phase 5: Testing | 4 tasks | 4-5 days |
| Phase 6: Validation | 4 tasks | 3-4 days |
| **Total** | **34 tasks** | **24-31 days** |

**Note**: Effort assumes single developer. With 2-3 developers working in parallel, timeline reduces to 10-15 days.

---

## Risk Mitigation

### High-Risk Tasks

1. **INFRA-002** (Milvus Schema): Complex schema with multi-vector support
   - Mitigation: Test schema thoroughly in development, have rollback plan
   
2. **WORKER-008** (ColPali): GPU dependency, complex model
   - Mitigation: Make optional, graceful degradation to text-only

3. **TEST-003** (Integration Tests): Testcontainers complexity
   - Mitigation: Start with simpler tests, add complexity incrementally

4. **VALIDATE-002** (E2E Validation): Requires full stack working
   - Mitigation: Validate components individually first

### Critical Path

```
INFRA-001 → API-002 → WORKER-002 → WORKER-006 → WORKER-007 → WORKER-009 → VALIDATE-001 → VALIDATE-002
```

Focus on critical path tasks to unblock dependent work.

---

## Success Metrics

### Code Quality
- [ ] Test coverage >80% for all modules
- [ ] All linter checks pass
- [ ] No critical security vulnerabilities
- [ ] Code review completed

### Functionality
- [ ] All 45 functional requirements implemented
- [ ] All 7 user stories satisfied
- [ ] All edge cases handled

### Performance
- [ ] All 21 success criteria met
- [ ] Performance benchmarks pass
- [ ] Scalability validated (50 concurrent uploads)

### Operations
- [ ] All services deployed via Ansible (IaC)
- [ ] Health checks implemented
- [ ] Logging and monitoring in place
- [ ] Documentation complete

---

**Generated**: 2025-11-05  
**Status**: Ready for implementation  
**Next Step**: Begin with Phase 1 (Infrastructure) tasks

