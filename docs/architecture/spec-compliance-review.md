# Spec Compliance Review: Document Ingestion Service

**Date**: 2025-11-05  
**Branch**: `004-updated-ingestion-service`  
**Spec**: `/specs/004-updated-ingestion-service/spec.md`

## Executive Summary

✅ **Core Architecture**: Implemented and tested  
⚠️ **Missing Features**: Language detection, visual embeddings (ColPali), OCR  
🔧 **Infrastructure**: Needs Milvus schema migration deployment

---

## Functional Requirements Compliance

### ✅ File Upload and Storage (FR-001 to FR-006)

| ID | Requirement | Status | Implementation |
|----|-------------|--------|----------------|
| FR-001 | Multiple format support (PDF, DOCX, TXT, etc.) | ✅ Implemented | `upload.py` accepts all formats |
| FR-002 | Chunked upload with stream processing | ✅ Implemented | FastAPI `UploadFile` with streaming |
| FR-003 | Unique file identifier | ✅ Implemented | UUID generation in `upload.py` |
| FR-004 | SHA-256 content hash & deduplication | ✅ Implemented | `upload.py` line 135-138, PostgreSQL index |
| FR-005 | Secure storage with user ownership | ✅ Implemented | MinIO + PostgreSQL permissions |
| FR-006 | Authorized service-only access | ✅ Implemented | Internal services only (no public endpoint) |

**Test Coverage**: `test_services.py::test_minio_service`, `test_services.py::test_postgres_service`

---

### ✅ Real-Time Status Tracking (FR-007 to FR-011)

| ID | Requirement | Status | Implementation |
|----|-------------|--------|----------------|
| FR-007 | Real-time SSE status streaming | ✅ Implemented | `status.py` with PostgreSQL LISTEN/NOTIFY |
| FR-008 | Stage updates (queued → completed) | ✅ Implemented | `ingestion_status` table with triggers |
| FR-009 | Progress % and metrics | ✅ Implemented | `chunks_processed`, `pages_processed` fields |
| FR-010 | Multiple concurrent connections | ✅ Implemented | SSE supports unlimited connections |
| FR-011 | Auto-close connections on completion | ✅ Implemented | `status.py` timeout handling |

**Test Coverage**: Integration test needed for SSE streaming

---

### ⚠️ Document Processing Pipeline (FR-012 to FR-024)

| ID | Requirement | Status | Implementation |
|----|-------------|--------|----------------|
| FR-012 | Check duplicate via SHA-256, reuse vectors | ✅ Implemented | `worker.py::_check_duplicate()`, line 252-297 |
| FR-013 | Format-appropriate text extraction | ⚠️ Partial | Marker for PDF, needs DOCX/TXT parsers |
| FR-014 | PDF charts/tables + visual extraction | ⚠️ Partial | Page images extracted, visual embeddings TODO |
| FR-015 | OCR for scanned PDFs | ❌ Not Implemented | `pytesseract` installed but not integrated |
| FR-016 | Document type classification | ❌ Not Implemented | Stub in schema, needs implementation |
| FR-017 | Language detection (primary + all) | ❌ Not Implemented | `primary_language`, `detected_languages[]` in schema but not populated |
| FR-018 | Extract embedded metadata | ⚠️ Partial | Schema supports it, extraction not implemented |
| FR-019 | Chunking (400-800 tokens, 10-15% overlap) | ✅ Implemented | `processing/chunking.py` with spaCy |
| FR-020 | Respect semantic boundaries | ✅ Implemented | spaCy sentence-aware chunking |
| FR-021 | Preserve page numbers, headings, offsets | ✅ Implemented | `ingestion_chunks` table stores all metadata |
| FR-022 | Dense semantic embeddings | ✅ Implemented | liteLLM `text-embedding-3-small` |
| FR-023 | Visual page representations (PDF) | ⚠️ Partial | Page images extracted, ColPali embeddings TODO |
| FR-024 | Store vectors linked to content_hash | ✅ Implemented | Milvus `insert_text_chunks()` with `content_hash` |

**Test Coverage**:
- ✅ `test_postgres_service::check_duplicate`
- ✅ `test_services.py::test_redis_service` (job queue)
- ⚠️ `test_services.py::test_milvus_service` (blocked on schema migration)
- ❌ Language detection not tested
- ❌ Document classification not tested
- ❌ OCR not tested

---

### ✅ Metadata and Entity Storage (FR-025 to FR-030)

| ID | Requirement | Status | Implementation |
|----|-------------|--------|----------------|
| FR-025 | File metadata storage | ✅ Implemented | `ingestion_files` table |
| FR-026 | Classification, language, metadata | ⚠️ Partial | Schema ready, population incomplete |
| FR-027 | Processing metrics | ✅ Implemented | `chunk_count`, `vector_count`, `processing_duration_seconds` |
| FR-028 | Chunk-level metadata | ✅ Implemented | `ingestion_chunks` table with all fields |
| FR-029 | Link files to shared content_hash | ✅ Implemented | `content_hash` column with index |
| FR-030 | User ownership and permissions | ✅ Implemented | `user_id`, `permissions` JSONB |

**Test Coverage**: `test_postgres_service` validates all table operations

---

### ✅ Error Handling and Recovery (FR-031 to FR-036)

| ID | Requirement | Status | Implementation |
|----|-------------|--------|----------------|
| FR-031 | Detect corrupted files, descriptive errors | ✅ Implemented | `worker.py` exception handling |
| FR-032 | Auto-retry transient failures (3x, exponential backoff) | ✅ Implemented | `worker.py::_is_transient_error()`, line 232-250 |
| FR-033 | No retry for permanent failures | ✅ Implemented | Distinguishes transient vs permanent |
| FR-034 | Save progress, resume from checkpoint | ⚠️ Partial | Chunk progress tracked, resume not implemented |
| FR-035 | Comprehensive error logging | ✅ Implemented | structlog throughout |
| FR-036 | Dynamic timeouts (5/10/20 min by size) | ✅ Implemented | `worker.py::_calculate_timeout()`, line 186-208 |

**Test Coverage**: Error scenarios need explicit integration tests

---

### ⚠️ Concurrent Processing and Scalability (FR-037 to FR-041)

| ID | Requirement | Status | Implementation |
|----|-------------|--------|----------------|
| FR-037 | Reliable job queue with multiple workers | ✅ Implemented | Redis Streams with consumer groups |
| FR-038 | Distribute jobs across workers | ✅ Implemented | Redis consumer group automatic distribution |
| FR-039 | Handle 50 concurrent uploads | ⚠️ Not Tested | Need load test |
| FR-040 | Batch processing for efficiency | ⚠️ Partial | Embeddings batched, could optimize further |
| FR-041 | Horizontal scaling support | ✅ Implemented | Stateless workers, scale via container count |

**Test Coverage**: Need concurrent upload load tests

---

### ✅ Security and Access Control (FR-042 to FR-045)

| ID | Requirement | Status | Implementation |
|----|-------------|--------|----------------|
| FR-042 | Internal services only | ✅ Implemented | No authentication middleware (internal network) |
| FR-043 | Validate user ownership | ✅ Implemented | `user_id` in all operations |
| FR-044 | Propagate permissions to vectors | ✅ Implemented | `user_id` stored in Milvus vectors |
| FR-045 | Redact sensitive info from logs | ✅ Implemented | structlog filters sensitive fields |

**Test Coverage**: Security tests needed

---

## Success Criteria Compliance

### Upload and Status Tracking (SC-001 to SC-004)

| ID | Criterion | Status | Notes |
|----|-----------|--------|-------|
| SC-001 | File ID + status within 2s | ✅ Expected | FastAPI async upload |
| SC-002 | Status updates within 2s | ✅ Expected | PostgreSQL LISTEN/NOTIFY |
| SC-003 | 100% final status (no stuck jobs) | ⚠️ Not Verified | Need monitoring |
| SC-004 | 100 concurrent status connections | ✅ Expected | SSE is lightweight |

---

### Processing Performance (SC-005 to SC-008)

| ID | Criterion | Status | Notes |
|----|-----------|--------|-------|
| SC-005 | Small docs: 2min / duplicates: 10s | ⚠️ Not Measured | Need benchmarks |
| SC-006 | Medium docs: 5min / duplicates: 10s | ⚠️ Not Measured | Need benchmarks |
| SC-007 | Large docs: 15min / duplicates: 10s | ⚠️ Not Measured | Need benchmarks |
| SC-008 | <50% degradation at 50 concurrent | ⚠️ Not Measured | Need load tests |

**Action Required**: Performance benchmarking suite

---

### Processing Quality (SC-009 to SC-013)

| ID | Criterion | Status | Notes |
|----|-----------|--------|-------|
| SC-009 | >95% text extraction accuracy | ⚠️ Not Measured | Marker is high-quality |
| SC-010 | >80% classification confidence | ❌ Not Implemented | Classification not done |
| SC-011 | >95% language detection accuracy | ❌ Not Implemented | Language detection not done |
| SC-012 | 80% semantic boundary alignment | ⚠️ Not Measured | spaCy should achieve this |
| SC-013 | 95% success rate | ⚠️ Not Measured | Need production metrics |

---

### Error Handling (SC-014 to SC-017)

| ID | Criterion | Status | Notes |
|----|-----------|--------|-------|
| SC-014 | 90% retry success within 30s | ⚠️ Not Measured | Exponential backoff implemented |
| SC-015 | Permanent errors detected in <1min | ✅ Expected | Immediate detection |
| SC-016 | Failures preserve files/metadata | ✅ Implemented | Files never deleted |
| SC-017 | Actionable error messages | ✅ Implemented | Descriptive error messages |

---

### System Reliability (SC-018 to SC-021)

| ID | Criterion | Status | Notes |
|----|-----------|--------|-------|
| SC-018 | 99.9% uptime | ⚠️ Not Measured | Depends on infrastructure |
| SC-019 | No data loss during failures | ✅ Implemented | All data persisted immediately |
| SC-020 | Queue depth <1000 | ⚠️ Not Monitored | Need alerts |
| SC-021 | Graceful resource exhaustion handling | ✅ Implemented | Jobs queue, no rejections |

---

## Architecture Compliance

### Data Model ✅

**PostgreSQL Schema**:
- ✅ `ingestion_files` - Fully implemented per spec
- ✅ `ingestion_status` - Fully implemented with LISTEN/NOTIFY
- ✅ `ingestion_chunks` - Fully implemented
- ✅ Indexes: content_hash, user_id, primary_language, detected_languages (GIN)

**Milvus Schema**:
- ✅ Collection defined: `documents` (hybrid_schema.py)
- ✅ Fields: id, file_id, chunk_index, page_number, modality, text, text_dense, text_sparse, page_vectors, user_id, metadata
- ✅ BM25 function for sparse vectors
- ⚠️ **ISSUE**: Existing test collection has wrong schema, needs migration

**MinIO Storage**:
- ✅ Path structure: `{user_id}/{file_id}/{filename}`
- ✅ Content-based deduplication via SHA-256

---

## Critical Gaps

### 1. Language Detection (FR-017, SC-011) - Priority: HIGH

**Status**: ❌ Not Implemented  
**Impact**: Mixed-language documents won't be handled correctly; search quality degraded

**Required**:
- Integrate `langdetect` for primary language identification
- Detect all languages in document for `detected_languages[]` array
- Store in `ingestion_files.primary_language` and `.detected_languages`

**Implementation**:
```python
# srv/ingest/src/processing/language_detection.py
from langdetect import detect, detect_langs

def detect_languages(text: str) -> tuple[str, list[str]]:
    """Detect primary language and all languages in text."""
    primary = detect(text)
    all_langs = [lang.lang for lang in detect_langs(text) if lang.prob > 0.1]
    return primary, all_langs
```

---

### 2. Document Classification (FR-016, SC-010) - Priority: HIGH

**Status**: ❌ Not Implemented  
**Impact**: No document type filtering/organization; metadata incomplete

**Required**:
- Classify documents as: report, article, email, code, presentation, etc.
- Store in `ingestion_files.document_type` with confidence
- Use LLM-based classification via liteLLM

**Implementation**:
```python
# srv/ingest/src/processing/classification.py
async def classify_document(text_sample: str, litellm_client) -> tuple[str, float]:
    """Classify document type using LLM."""
    prompt = f"Classify this document: {text_sample[:1000]}\nType (report/article/email/code/presentation):"
    response = await litellm_client.completion(...)
    return doc_type, confidence
```

---

### 3. Visual Embeddings - ColPali (FR-014, FR-023) - Priority: MEDIUM

**Status**: ⚠️ Partial (page images extracted, embeddings not generated)  
**Impact**: Cannot search visual content (charts, tables, diagrams)

**Required**:
- Integrate ColPali model for visual embeddings
- Generate 128-patch embeddings (16,384 dims) for each PDF page
- Store in Milvus `page_vectors` field

**Implementation**:
```python
# srv/ingest/src/processing/visual_embeddings.py
async def generate_colpali_embeddings(page_images: list) -> list:
    """Generate ColPali multi-vector embeddings for PDF pages."""
    # Call ColPali service
    # Returns list of 128x128 dimensional vectors per page
    pass
```

---

### 4. OCR for Scanned PDFs (FR-015) - Priority: LOW

**Status**: ❌ Not Implemented (pytesseract installed but not integrated)  
**Impact**: Scanned PDFs fail processing

**Required**:
- Detect when PDF has no extractable text
- Run OCR using pytesseract
- Fallback to OCR on extraction failure

**Implementation**:
```python
# srv/ingest/src/processing/ocr.py
def ocr_pdf_page(image: PIL.Image) -> str:
    """Extract text from scanned page using OCR."""
    return pytesseract.image_to_string(image)
```

---

### 5. Milvus Schema Migration - Priority: CRITICAL

**Status**: 🔧 Ready to deploy  
**Impact**: **BLOCKS MILVUS TESTING**

**Required**:
- Deploy migration script to test environment
- Drop incompatible `document_embeddings` collection
- Create correct `documents` collection with hybrid schema

**Action**:
```bash
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --tags milvus
```

See: `srv/ingest/tests/integration/MILVUS_SCHEMA_FIX.md`

---

### 6. Resume from Checkpoint (FR-034) - Priority: LOW

**Status**: ⚠️ Partial (progress tracked, resume not implemented)  
**Impact**: Failed jobs restart from beginning

**Required**:
- Check `ingestion_status.chunks_processed` on retry
- Skip already-processed chunks
- Resume from last successful chunk

---

### 7. Performance Benchmarks (SC-005 to SC-008) - Priority: MEDIUM

**Status**: ⚠️ Not Measured  
**Impact**: Cannot verify performance targets

**Required**:
- Benchmark suite for small/medium/large documents
- Concurrent upload load tests (50 simultaneous)
- Duplicate processing speed test (<10s target)

---

### 8. Metadata Extraction (FR-018) - Priority: LOW

**Status**: ⚠️ Partial (schema ready, extraction not implemented)  
**Impact**: No automatic title/author/date/keywords extraction

**Required**:
- Extract PDF metadata (title, author, creation date)
- Extract keywords using LLM or TF-IDF
- Store in `extracted_title`, `extracted_author`, `extracted_date`, `extracted_keywords`

---

## Integration Test Status

### ✅ Passing Tests

1. **MinIO Service** (`test_minio_service`)
   - File upload/download
   - Health check
   - Path structure

2. **PostgreSQL Service** (`test_postgres_service`)
   - File record creation
   - Status updates
   - Duplicate detection
   - Metadata retrieval

3. **Redis Service** (`test_redis_service`)
   - Job queue operations
   - Consumer group management
   - Job addition with all parameters

4. **Service Integration** (`test_service_integration`)
   - End-to-end: MinIO → PostgreSQL → Redis
   - Cross-service data flow

### ❌ Blocked Tests

1. **Milvus Service** (`test_milvus_service`)
   - **Blocker**: Collection schema mismatch
   - **Action**: Deploy schema migration
   - **ETA**: User needs to run Ansible playbook

### ⚠️ Missing Tests

1. **SSE Status Streaming** - Need to test real-time updates
2. **Duplicate Content Reuse** - Need end-to-end test with duplicate upload
3. **Error Retry Logic** - Need to inject failures and verify retries
4. **Concurrent Upload Load** - Need 50 simultaneous uploads
5. **Language Detection** - Not implemented yet
6. **Document Classification** - Not implemented yet
7. **Visual Embeddings** - Not implemented yet

---

## Recommendations

### Immediate Actions (This Sprint)

1. **Deploy Milvus Migration** (CRITICAL)
   - User action required
   - Unblocks Milvus testing
   - Command: `ansible-playbook -i inventory/test/hosts.yml site.yml --tags milvus`

2. **Implement Language Detection** (HIGH)
   - Estimated: 4 hours
   - Required for spec compliance
   - Unblocks SC-011

3. **Implement Document Classification** (HIGH)
   - Estimated: 6 hours
   - Required for spec compliance
   - Unblocks SC-010

### Next Sprint

4. **Add SSE Status Tests**
   - Estimated: 2 hours
   - Validates FR-007 to FR-011

5. **Implement Visual Embeddings (ColPali)**
   - Estimated: 1-2 days
   - Required for complete FR-014, FR-023

6. **Performance Benchmarks**
   - Estimated: 1 day
   - Validates SC-005 to SC-008

### Future Enhancements

7. **OCR Integration**
   - Estimated: 4 hours
   - Handles edge case (scanned PDFs)

8. **Metadata Extraction**
   - Estimated: 6 hours
   - Quality-of-life improvement

9. **Resume from Checkpoint**
   - Estimated: 4 hours
   - Efficiency improvement

---

## Summary

**Overall Compliance**: ~75% of functional requirements implemented

**Core Infrastructure**: ✅ Solid foundation
- Upload, storage, queueing, status tracking all working
- Database schemas complete
- Integration tests validate critical paths

**Missing Features**: 3 high-priority items
1. Language detection (FR-017)
2. Document classification (FR-016)
3. Milvus schema migration deployment (blocking tests)

**Test Coverage**: Good for implemented features
- 4/5 integration test suites passing
- 1 suite blocked on infrastructure (Milvus schema)
- Missing: performance benchmarks, load tests, error injection tests

**Recommendation**: Focus on language detection and document classification (both ~10 hours combined) before moving to visual embeddings (1-2 days). This will achieve ~85% spec compliance and unblock most user stories.

