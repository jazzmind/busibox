# Ingestion Service Specification

**Created**: 2025-11-04  
**Status**: Active  
**Category**: Architecture

## Purpose

A dedicated, production-grade document ingestion service that handles the complete pipeline from file upload to embedded vectors in Milvus. **Separate from agent-server** to allow independent scaling and hardware optimization for CPU/memory intensive processing workloads.

---

## Why Separate from Agent-Server?

### Different Usage Patterns

**Ingestion Service**:
- CPU intensive (PDF parsing, OCR)
- Memory intensive (large file processing)
- Bursty workload (batch uploads)
- Long-running operations (minutes per document)
- Can run on high-CPU, high-memory hardware

**Agent Service** (Mastra):
- Request-response pattern
- Low latency requirements
- Concurrent user requests
- Short-lived operations (seconds)
- Needs fast response times

### Operational Independence

- **Scale separately**: Add ingest workers without affecting agent capacity
- **Hardware optimization**: Run on different instance types
- **Failure isolation**: Ingest failures don't impact agent availability
- **Maintenance**: Update/restart independently
- **Resource allocation**: Different memory/CPU profiles

---

## Service Architecture

### Tech Stack

**Language**: Python 3.11+  
**API Framework**: FastAPI  
**Queue**: Redis Streams  
**Storage**: MinIO (S3-compatible)  
**Vector DB**: Milvus  
**Metadata DB**: PostgreSQL  
**Worker**: Python background process

### Components

```
┌─────────────────────────────────────────────────────┐
│           ingest-lxc (CTID 206)                     │
│                                                      │
│  ┌────────────────────────────────────────────┐   │
│  │  FastAPI API (Port 8000 - INTERNAL)        │   │
│  │                                             │   │
│  │  POST /api/v1/ingest/upload                │   │
│  │  GET  /api/v1/ingest/status/{fileId} (SSE) │   │
│  │  GET  /api/v1/ingest/files/{fileId}        │   │
│  │  DELETE /api/v1/ingest/files/{fileId}      │   │
│  └────────────────────────────────────────────┘   │
│                                                      │
│  ┌────────────────────────────────────────────┐   │
│  │  Redis Streams (Port 6379 - INTERNAL)      │   │
│  │                                             │   │
│  │  Stream: jobs:ingestion                    │   │
│  │  Consumer Group: workers                   │   │
│  └────────────────────────────────────────────┘   │
│                                                      │
│  ┌────────────────────────────────────────────┐   │
│  │  Background Worker (Python Process)        │   │
│  │                                             │   │
│  │  1. Consume from Redis                     │   │
│  │  2. Download from MinIO                    │   │
│  │  3. Parse (PDF/DOCX/TXT/etc)              │   │
│  │  4. Classify document type                 │   │
│  │  5. Extract metadata                       │   │
│  │  6. Chunk text                             │   │
│  │  7. Generate embeddings (liteLLM)         │   │
│  │  8. Store vectors (Milvus)                │   │
│  │  9. Store metadata (PostgreSQL)           │   │
│  │  10. Update status at each stage          │   │
│  └────────────────────────────────────────────┘   │
│                                                      │
└──────────────────────────────────────────────────────┘

External Communication:
  ← apps-lxc (201): API requests (proxied through Next.js)
  → files-lxc (205): MinIO - File storage
  → litellm-lxc (207): Embeddings
  → milvus-lxc (204): Vector storage
  → pg-lxc (203): Metadata & status
```

---

## API Specification

### 1. File Upload

**Endpoint**: `POST /api/v1/ingest/upload`

**Access**: Internal only (from apps-lxc, future scrapers)

**Request**:
```http
POST /api/v1/ingest/upload HTTP/1.1
Content-Type: multipart/form-data

file: [binary]
metadata: {
  "user_id": "uuid",
  "source": "web_upload",
  "tags": ["report", "financial"],
  "permissions": {
    "owner": "user_id",
    "readers": ["group_id"],
    "visibility": "private"
  }
}
```

**Response**:
```json
{
  "fileId": "uuid",
  "filename": "document.pdf",
  "size": 1024000,
  "status": "queued",
  "created_at": "2025-11-04T10:00:00Z"
}
```

**Flow**:
1. Validate file type and size
2. Generate unique fileId (UUID)
3. Store file in MinIO: `s3://documents/{userId}/{fileId}/{filename}`
4. Create record in PostgreSQL with status="queued"
5. Queue job in Redis Streams
6. Return fileId to client

---

### 2. Status Tracking (SSE)

**Endpoint**: `GET /api/v1/ingest/status/{fileId}`

**Access**: Internal only

**Response** (Server-Sent Events):
```
event: status
data: {"fileId": "uuid", "stage": "queued", "progress": 0}

event: status
data: {"fileId": "uuid", "stage": "parsing", "progress": 20}

event: status
data: {"fileId": "uuid", "stage": "classifying", "progress": 30}

event: status
data: {"fileId": "uuid", "stage": "chunking", "progress": 40, "chunks_created": 25}

event: status
data: {"fileId": "uuid", "stage": "embedding", "progress": 60, "chunks_processed": 10, "total_chunks": 25}

event: status
data: {"fileId": "uuid", "stage": "indexing", "progress": 80}

event: status
data: {"fileId": "uuid", "stage": "completed", "progress": 100, "vector_count": 25, "duration_seconds": 45}

event: close
data: {"message": "Processing complete"}
```

**Implementation**:
- PostgreSQL LISTEN/NOTIFY for real-time updates
- Keep-alive every 30 seconds
- Automatic close on completion or failure
- Supports multiple concurrent clients per fileId

---

### 3. File Metadata

**Endpoint**: `GET /api/v1/ingest/files/{fileId}`

**Access**: Internal only

**Response**:
```json
{
  "fileId": "uuid",
  "filename": "document.pdf",
  "original_filename": "Q4 Report.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 1024000,
  "storage_path": "s3://documents/user123/uuid/document.pdf",
  "user_id": "user123",
  "status": {
    "stage": "completed",
    "progress": 100,
    "chunks_created": 25,
    "vector_count": 25,
    "error": null
  },
  "classification": {
    "document_type": "report",
    "language": "en",
    "confidence": 0.95
  },
  "metadata": {
    "tags": ["financial", "quarterly"],
    "extracted": {
      "title": "Q4 Financial Report",
      "author": "Finance Team",
      "created_date": "2025-10-01"
    }
  },
  "permissions": {
    "owner": "user123",
    "readers": ["group456"]
  },
  "created_at": "2025-11-04T10:00:00Z",
  "updated_at": "2025-11-04T10:01:30Z",
  "processing_duration_seconds": 45
}
```

---

### 4. Delete File

**Endpoint**: `DELETE /api/v1/ingest/files/{fileId}`

**Access**: Internal only (requires owner permission)

**Response**:
```json
{
  "message": "File deleted successfully",
  "fileId": "uuid",
  "vectors_deleted": 25,
  "storage_freed_bytes": 1024000
}
```

**Flow**:
1. Verify ownership/permissions
2. Delete vectors from Milvus
3. Delete metadata from PostgreSQL
4. Delete file from MinIO
5. Remove any pending jobs from Redis

---

## Processing Pipeline

### Stage 1: Parsing (20%)

**Input**: File from MinIO  
**Output**: Raw text content

**Supported Formats**:
- **PDF** (dual-track processing):
  - **Text extraction**: `Marker` or `Unstructured.io` for high-quality markdown
  - **Table extraction**: `TATR` (Table Transformer) for complex tables
  - **Page images**: Extract as PNG for ColPali embeddings
  - **Fallback**: `pdfplumber` or `pypdf2` for simple PDFs
- **DOCX**: `python-docx`
- **TXT**: Direct read
- **HTML**: `beautifulsoup4`
- **Markdown**: Direct read with metadata extraction
- **CSV**: `pandas` (for structured data)
- **JSON**: Direct parse

**PDF Processing Strategy** (per ai-search.md):
1. **Parse to text/markdown**: Unstructured/Marker + TATR for tables → Text chunks
2. **Extract page images**: One image per page → ColPali embeddings
3. **Fuse both**: Text chunks (BM25 + dense) + page images (ColPali multi-vector)

**OCR** (if needed):
- Tesseract for scanned PDFs
- Image preprocessing for better accuracy
- **Note**: ColPali handles visual content without OCR

**Error Handling**:
- Corrupted files → Status: "failed" with error message
- Unsupported format → Return error immediately
- Partial success → Process what's extractable, log warnings
- PDF parsing failure → Fall back to simpler parser

---

### Stage 2: Classification (30%)

**Input**: Raw text  
**Output**: Document type, language, confidence

**Classifier**:
- Document type (report, email, article, code, etc.)
- Language detection
- Sensitivity classification (public, internal, confidential)

**Uses**:
- Simple heuristics for common patterns
- Optional: Call LLM for complex classification

---

### Stage 3: Metadata Extraction (35%)

**Input**: Raw text + file metadata  
**Output**: Structured metadata

**Extract**:
- Title (from first heading or PDF metadata)
- Author (from PDF metadata or document)
- Created date (from PDF metadata or content)
- Keywords (frequency analysis or LLM extraction)
- Summary (first paragraph or LLM-generated)

---

### Stage 4: Chunking (40%)

**Input**: Parsed text  
**Output**: Text chunks

**Strategy** (per ai-search.md recommendations):
- **Chunk size**: 400-800 tokens (optimized for hybrid retrieval)
- **Overlap**: 10-15% (40-120 tokens)
- **Semantic boundaries**: Prefer paragraph/section breaks when possible
- **Keep context**: Store page numbers, section headings with each chunk

**Libraries**:
- `tiktoken` for token counting (primary)
- `spacy` for semantic boundaries (secondary)

**Metadata per chunk**:
- Chunk index
- Character offset in original
- Token count
- Section heading (if available)
- Page number (for PDFs) - **critical for ColPali alignment**
- Parent document ID

---

### Stage 5: Embedding Generation (60-80%)

**Input**: Text chunks + (for PDFs) page images  
**Output**: Dense embeddings, sparse embeddings (BM25), and ColPali multi-vectors

**Process - Multi-Vector Approach**:

#### 5a. Dense Text Embeddings (60-70%)
1. Batch chunks (e.g., 10 at a time)
2. Call liteLLM for dense embeddings
3. Update progress per batch
4. Retry with exponential backoff on failures

**Embedding Model**:
- Default: `text-embedding-3-small` (OpenAI via liteLLM)
- Alternative: `bge-m3` for multilingual
- Dimension: 1536 (OpenAI) or 768 (BGE)

#### 5b. Sparse Embeddings - BM25 (70-75%)
**Generated by Milvus BM25 Function** (not during ingestion)
- Text stored in Milvus with `enable_analyzer=True`
- Milvus generates BM25 sparse vectors automatically
- No external API call needed

#### 5c. PDF Page Images - ColPali (75-80%, if PDF)
**For PDFs with charts/tables/visual content**:
1. Extract page images from PDF
2. Generate ColPali embeddings per page (late-interaction multi-vector)
3. Store multi-vector representation (128 vectors × 128 dims per page)
4. Link to corresponding text chunks by page number

**ColPali Model**:
- `vidore/colpali-v1.2` or similar
- Multi-vector output: 128 patch embeddings per page
- Captures layout, charts, tables without OCR

**Progress Tracking**:
```python
progress = 60 + (15 * text_chunks_processed / total_chunks) + \
           (5 * pages_processed / total_pages)
```

---

### Stage 6: Vector Storage (80-90%)

**Input**: Dense embeddings, text (for BM25), page images (for ColPali)  
**Output**: Multi-vector records in Milvus

**Milvus Collection Structure** (Hybrid + Multi-Vector):
```python
from pymilvus import DataType, Function, FunctionType

schema = CollectionSchema([
    # Primary key and metadata
    FieldSchema("id", DataType.VARCHAR, is_primary=True, max_length=64),
    FieldSchema("file_id", DataType.VARCHAR, max_length=64),
    FieldSchema("chunk_index", DataType.INT64),
    FieldSchema("page_number", DataType.INT64),  # For ColPali alignment
    FieldSchema("modality", DataType.VARCHAR, max_length=20),  # "text" or "page_image"
    
    # Text content (for BM25)
    FieldSchema("text", DataType.VARCHAR, max_length=65535, enable_analyzer=True),
    
    # Dense embeddings (text)
    FieldSchema("text_dense", DataType.FLOAT_VECTOR, dim=1536),
    
    # Sparse embeddings (BM25) - auto-generated by Milvus
    FieldSchema("text_sparse", DataType.SPARSE_FLOAT_VECTOR),
    
    # Multi-vector for ColPali (PDF pages)
    FieldSchema("page_vectors", DataType.FLOAT_VECTOR, dim=128, 
                is_partition_key=False),  # 128 patch embeddings per page
    
    # Metadata
    FieldSchema("metadata", DataType.JSON),
])

# Add BM25 function to auto-generate sparse vectors
schema.add_function(
    Function(
        name="text_bm25_emb",
        input_field_names=["text"],
        output_field_names=["text_sparse"],
        function_type=FunctionType.BM25
    )
)
```

**Insert Strategy**:

#### Text Chunks (with dense + BM25)
```python
# Batch insert text chunks
entities = {
    "id": [chunk_ids],
    "file_id": [file_id] * len(chunks),
    "chunk_index": range(len(chunks)),
    "page_number": [chunk.page for chunk in chunks],
    "modality": ["text"] * len(chunks),
    "text": [chunk.text for chunk in chunks],
    "text_dense": [embeddings],  # From liteLLM
    # text_sparse auto-generated by BM25 function
    "page_vectors": [None] * len(chunks),  # Not applicable for text
    "metadata": [chunk.metadata for chunk in chunks],
}
collection.insert(entities)
```

#### PDF Page Images (with ColPali)
```python
# Insert page-level multi-vectors
for page_num, page_image in enumerate(pdf_pages):
    page_vectors = colpali_model.encode(page_image)  # 128x128 multi-vector
    
    entities = {
        "id": f"{file_id}_page_{page_num}",
        "file_id": file_id,
        "chunk_index": -1,  # Not a text chunk
        "page_number": page_num,
        "modality": "page_image",
        "text": f"Page {page_num}",  # Minimal text for BM25
        "text_dense": None,  # Not applicable
        "text_sparse": None,
        "page_vectors": page_vectors,  # 128 patch embeddings
        "metadata": {"page_width": w, "page_height": h},
    }
    collection.insert(entities)
```

**Indexing**:
- **Dense vectors**: HNSW index (M=16, efConstruction=200)
- **Sparse vectors**: SPARSE_INVERTED_INDEX (for BM25)
- **Multi-vectors**: HNSW per patch (for ColPali)

**Batch Insert**:
- Text chunks: Batch size 100
- Page images: Batch size 10 (larger multi-vectors)
- Index creation after all inserts complete

---

### Stage 7: Metadata Storage (90%)

**Input**: Document metadata + processing results  
**Output**: Records in PostgreSQL

**Tables**:
```sql
-- Main file record
CREATE TABLE ingestion_files (
  file_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  filename VARCHAR(255) NOT NULL,
  original_filename VARCHAR(255) NOT NULL,
  mime_type VARCHAR(100) NOT NULL,
  size_bytes BIGINT NOT NULL,
  storage_path TEXT NOT NULL,
  
  -- Classification
  document_type VARCHAR(50),
  language VARCHAR(10),
  classification_confidence REAL,
  
  -- Processing metadata
  chunk_count INTEGER,
  vector_count INTEGER,
  processing_duration_seconds INTEGER,
  
  -- Extracted metadata
  metadata JSONB,
  
  -- Permissions
  permissions JSONB NOT NULL,
  
  -- Timestamps
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  
  -- Indexes
  INDEX idx_user_id (user_id),
  INDEX idx_document_type (document_type),
  INDEX idx_created_at (created_at)
);

-- Processing status (separate for real-time updates)
CREATE TABLE ingestion_status (
  file_id UUID PRIMARY KEY REFERENCES ingestion_files(file_id),
  stage VARCHAR(50) NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  chunks_processed INTEGER,
  total_chunks INTEGER,
  error_message TEXT,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Chunks (for reference and debugging)
CREATE TABLE ingestion_chunks (
  chunk_id UUID PRIMARY KEY,
  file_id UUID NOT NULL REFERENCES ingestion_files(file_id),
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  char_offset INTEGER,
  metadata JSONB,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  
  INDEX idx_file_id (file_id)
);
```

---

### Stage 8: Completion (100%)

**Actions**:
1. Update status to "completed"
2. Record final metrics (duration, vector count)
3. Send completion event via SSE
4. Log success with file_id

**Cleanup**:
- Remove job from Redis (ack message)
- Close any open SSE connections
- Free resources

---

## Worker Implementation

### Technology

**Language**: Python 3.11+  
**Concurrency**: Multiple worker processes (configurable)  
**Queue**: Redis Streams with consumer groups

### Worker Process

```python
# srv/ingest/src/worker.py

import structlog
from redis import Redis
from processors import (
    TextExtractor,
    Classifier,
    MetadataExtractor,
    Chunker,
    Embedder
)
from services import (
    MinIOService,
    PostgresService,
    MilvusService,
    StatusService
)

logger = structlog.get_logger()

class IngestionWorker:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.redis = Redis(...)
        self.minio = MinIOService()
        self.postgres = PostgresService()
        self.milvus = MilvusService()
        self.status = StatusService()
        
        # Processors
        self.extractor = TextExtractor()
        self.classifier = Classifier()
        self.metadata_extractor = MetadataExtractor()
        self.chunker = Chunker()
        self.embedder = Embedder()
    
    def run(self):
        """Main worker loop"""
        logger.info("Worker started", worker_id=self.worker_id)
        
        while True:
            # Consume from Redis Streams
            messages = self.redis.xreadgroup(
                groupname='workers',
                consumername=self.worker_id,
                streams={'jobs:ingestion': '>'},
                count=1,
                block=5000  # 5 second timeout
            )
            
            if not messages:
                continue
            
            for stream, message_list in messages:
                for message_id, job_data in message_list:
                    try:
                        self.process_job(job_data)
                        self.redis.xack('jobs:ingestion', 'workers', message_id)
                    except Exception as e:
                        logger.error("Job failed", error=str(e), job=job_data)
                        # Don't ack - will retry
    
    def process_job(self, job_data: dict):
        """Process a single ingestion job"""
        file_id = job_data['file_id']
        
        logger.info("Processing job", file_id=file_id)
        
        # Stage 1: Parse (20%)
        self.status.update(file_id, 'parsing', 20)
        file_content = self.minio.download(job_data['storage_path'])
        text = self.extractor.extract(file_content, job_data['mime_type'])
        
        # Stage 2: Classify (30%)
        self.status.update(file_id, 'classifying', 30)
        classification = self.classifier.classify(text)
        
        # Stage 3: Extract metadata (35%)
        self.status.update(file_id, 'extracting_metadata', 35)
        metadata = self.metadata_extractor.extract(text, file_content)
        
        # Stage 4: Chunk (40%)
        self.status.update(file_id, 'chunking', 40)
        chunks = self.chunker.chunk(text, chunk_size=512, overlap=50)
        
        # Stage 5: Generate embeddings (60-80%)
        self.status.update(file_id, 'embedding', 60, 
                          total_chunks=len(chunks))
        embeddings = []
        for i, chunk in enumerate(chunks):
            emb = self.embedder.embed(chunk.text)
            embeddings.append(emb)
            progress = 60 + int(20 * (i + 1) / len(chunks))
            self.status.update(file_id, 'embedding', progress,
                             chunks_processed=i+1, total_chunks=len(chunks))
        
        # Stage 6: Store vectors (80%)
        self.status.update(file_id, 'indexing', 80)
        self.milvus.insert(file_id, chunks, embeddings)
        
        # Stage 7: Store metadata (90%)
        self.status.update(file_id, 'storing_metadata', 90)
        self.postgres.store_file_metadata(
            file_id=file_id,
            classification=classification,
            metadata=metadata,
            chunk_count=len(chunks),
            vector_count=len(embeddings)
        )
        
        # Stage 8: Complete (100%)
        self.status.update(file_id, 'completed', 100,
                          vector_count=len(embeddings))
        
        logger.info("Job completed", file_id=file_id)
```

---

## Deployment

### Container Setup

**Location**: `ingest-lxc` (CTID 206, IP 10.96.200.206)

**Services**:
- FastAPI (systemd service: `ingest-api`)
- Worker (systemd service: `ingest-worker`)
- Redis (systemd service: `redis-server`)

### Ansible Role

**Location**: `provision/ansible/roles/ingest_service/`

```yaml
# tasks/main.yml
- name: Install Python and dependencies
  apt:
    name:
      - python3.11
      - python3-pip
      - python3-venv
      - redis-server
    state: present

- name: Create ingest service directory
  file:
    path: /srv/ingest
    state: directory

- name: Copy source code
  copy:
    src: "{{ playbook_dir }}/../../srv/ingest/"
    dest: /srv/ingest/

- name: Install Python dependencies
  pip:
    requirements: /srv/ingest/requirements.txt
    virtualenv: /srv/ingest/venv

- name: Configure Redis
  template:
    src: redis.conf.j2
    dest: /etc/redis/redis.conf

- name: Create systemd service for API
  template:
    src: ingest-api.service.j2
    dest: /etc/systemd/system/ingest-api.service

- name: Create systemd service for worker
  template:
    src: ingest-worker.service.j2
    dest: /etc/systemd/system/ingest-worker.service

- name: Enable and start services
  systemd:
    name: "{{ item }}"
    enabled: yes
    state: started
  loop:
    - redis-server
    - ingest-api
    - ingest-worker
```

### Environment Variables

```bash
# API Configuration
INGEST_API_HOST=0.0.0.0
INGEST_API_PORT=8000

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# MinIO
MINIO_ENDPOINT=http://10.96.200.205:9000
MINIO_ACCESS_KEY={{ vault_minio_access_key }}
MINIO_SECRET_KEY={{ vault_minio_secret_key }}
MINIO_BUCKET=documents

# PostgreSQL
POSTGRES_HOST=10.96.200.203
POSTGRES_PORT=5432
POSTGRES_DB=busibox
POSTGRES_USER={{ vault_postgres_user }}
POSTGRES_PASSWORD={{ vault_postgres_password }}

# Milvus
MILVUS_HOST=10.96.200.204
MILVUS_PORT=19530

# liteLLM
LITELLM_BASE_URL=http://10.96.200.30:4000
LITELLM_API_KEY={{ vault_litellm_api_key }}

# Worker Configuration
WORKER_COUNT=2
WORKER_CONCURRENCY=1
```

---

## Scalability

### Horizontal Scaling

**Add More Workers**:
```bash
# On ingest-lxc
systemctl start ingest-worker@2
systemctl start ingest-worker@3
```

**Multiple Containers**:
- Deploy to `ingest-lxc-2`, `ingest-lxc-3`
- All consume from same Redis Streams
- Consumer groups ensure no duplicate processing

### Vertical Scaling

**Increase Resources**:
- More CPU for PDF parsing
- More memory for large files
- SSD storage for temp files

### Performance Tuning

**Batch Processing**:
- Process multiple chunks per embedding call
- Batch inserts to Milvus

**Caching**:
- Cache parsed results for same files
- Cache embeddings for duplicate chunks

---

## Monitoring

### Metrics to Track

- Files processed per minute
- Average processing time per file
- Queue depth (pending jobs)
- Error rate by stage
- Storage usage (MinIO)
- Vector count in Milvus
- Worker CPU/memory usage

### Health Checks

```python
# Health endpoint
@app.get("/health")
def health():
    return {
        "api": "healthy",
        "redis": check_redis_connection(),
        "workers": get_active_workers(),
        "queue_depth": get_queue_depth()
    }
```

---

## Security

### Authentication

**Internal Only**:
- Not exposed through proxy
- Only accessible from apps-lxc
- Apps-lxc passes user context in headers

**User Context Headers**:
```
X-User-ID: uuid
X-User-Roles: [admin, user]
```

### Authorization

- Verify user owns file before operations
- Apply permissions on file metadata
- RLS in PostgreSQL for multi-tenancy

### Data Security

- Files encrypted at rest in MinIO (optional)
- SSL/TLS for all external connections
- Sensitive metadata redacted in logs

---

## Error Handling

### Retry Strategy

**Transient Errors** (network, service unavailable):
- Retry with exponential backoff
- Max 3 retries
- Don't ack Redis message (automatic retry)

**Permanent Errors** (corrupted file, unsupported format):
- Mark status as "failed"
- Store error message
- Ack Redis message (don't retry)
- Alert user via SSE

### Partial Success

**Scenario**: 20 of 25 chunks embedded, then failure

**Handling**:
- Save progress in database
- Resume from last successful chunk
- Don't re-process completed chunks

---

## Testing

### Unit Tests

- Test each processor independently
- Mock external services (MinIO, Milvus, liteLLM)
- Test error handling

### Integration Tests

- End-to-end file upload to vector storage
- Test with various file formats
- Test SSE status streaming
- Test worker failure recovery

### Performance Tests

- Measure processing time for different file sizes
- Test concurrent uploads
- Verify queue doesn't back up

---

## Future Enhancements

1. **Batch Upload**: Upload multiple files at once
2. **OCR**: Extract text from images and scanned PDFs
3. **Incremental Updates**: Re-process only changed sections
4. **Smart Chunking**: Use LLM to determine optimal chunks
5. **Multi-language**: Better support for non-English documents
6. **Webhooks**: Notify external services on completion
7. **Priority Queue**: VIP users get faster processing


