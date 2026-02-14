# Data Model

**Feature**: Production-Grade Document Ingestion Service  
**Date**: 2025-11-05

## Overview

This document defines the data entities for the ingestion service, including database schemas (PostgreSQL), vector storage (Milvus), and object storage (MinIO) layouts.

---

## PostgreSQL Schema

### Table: ingestion_files

Stores metadata for all uploaded files.

```sql
CREATE TABLE ingestion_files (
  -- Primary key
  file_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  
  -- User ownership
  user_id UUID NOT NULL,
  
  -- File information
  filename VARCHAR(255) NOT NULL,
  original_filename VARCHAR(255) NOT NULL,
  mime_type VARCHAR(100) NOT NULL,
  size_bytes BIGINT NOT NULL,
  storage_path TEXT NOT NULL,  -- S3 path in MinIO
  
  -- Document classification
  document_type VARCHAR(50),  -- report, article, email, code, etc.
  language VARCHAR(10),        -- ISO 639-1 code (en, es, fr, etc.)
  classification_confidence REAL CHECK (classification_confidence >= 0 AND classification_confidence <= 1),
  
  -- Processing metrics
  chunk_count INTEGER DEFAULT 0,
  vector_count INTEGER DEFAULT 0,
  processing_duration_seconds INTEGER,
  
  -- Extracted metadata
  extracted_title VARCHAR(500),
  extracted_author VARCHAR(255),
  extracted_date DATE,
  extracted_keywords TEXT[],
  metadata JSONB DEFAULT '{}',  -- Additional extracted metadata
  
  -- Permissions
  permissions JSONB NOT NULL DEFAULT '{"visibility": "private"}',
  
  -- Timestamps
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_ingestion_files_user_id ON ingestion_files(user_id);
CREATE INDEX idx_ingestion_files_document_type ON ingestion_files(document_type);
CREATE INDEX idx_ingestion_files_created_at ON ingestion_files(created_at DESC);
CREATE INDEX idx_ingestion_files_language ON ingestion_files(language);

-- Full-text search on extracted metadata
CREATE INDEX idx_ingestion_files_metadata_gin ON ingestion_files USING gin(metadata jsonb_path_ops);
```

**Field Descriptions**:
- `file_id`: Unique identifier for file (UUID v4)
- `user_id`: Reference to user who uploaded file
- `storage_path`: S3 path in MinIO (e.g., `documents/user123/file456/document.pdf`)
- `document_type`: Automated classification result
- `classification_confidence`: Confidence score from classifier (0.0 to 1.0)
- `metadata`: Flexible JSONB for additional extracted info (summary, custom fields)
- `permissions`: JSONB with owner, readers, visibility (for future RLS)

---

### Table: ingestion_status

Tracks real-time processing status for files. Separate table for frequent updates.

```sql
CREATE TABLE ingestion_status (
  file_id UUID PRIMARY KEY REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
  
  -- Current processing state
  stage VARCHAR(50) NOT NULL CHECK (stage IN (
    'queued', 'parsing', 'classifying', 'extracting_metadata', 
    'chunking', 'embedding', 'indexing', 'completed', 'failed'
  )),
  progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
  
  -- Stage-specific metrics
  chunks_processed INTEGER,
  total_chunks INTEGER,
  pages_processed INTEGER,
  total_pages INTEGER,
  
  -- Error handling
  error_message TEXT,
  retry_count INTEGER DEFAULT 0,
  
  -- Timestamps
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Index for status lookups
CREATE INDEX idx_ingestion_status_stage ON ingestion_status(stage);
CREATE INDEX idx_ingestion_status_updated_at ON ingestion_status(updated_at DESC);

-- Trigger to update parent table timestamp
CREATE OR REPLACE FUNCTION update_file_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  UPDATE ingestion_files SET updated_at = NOW() WHERE file_id = NEW.file_id;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_file_timestamp
AFTER UPDATE ON ingestion_status
FOR EACH ROW
EXECUTE FUNCTION update_file_timestamp();
```

**Status Stages**:
1. `queued`: Job added to Redis, waiting for worker
2. `parsing`: Extracting text from file
3. `classifying`: Determining document type and language
4. `extracting_metadata`: Pulling title, author, keywords
5. `chunking`: Splitting text into chunks
6. `embedding`: Generating vector embeddings
7. `indexing`: Storing vectors in Milvus
8. `completed`: All processing done successfully
9. `failed`: Processing encountered permanent error

---

### Table: ingestion_chunks

Stores chunk-level metadata for debugging and reference.

```sql
CREATE TABLE ingestion_chunks (
  chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
  
  -- Chunk metadata
  chunk_index INTEGER NOT NULL,  -- Position in document (0-indexed)
  text TEXT NOT NULL,             -- Actual chunk text
  char_offset INTEGER,            -- Character offset in original document
  token_count INTEGER,            -- Number of tokens (for validation)
  
  -- Document structure
  page_number INTEGER,            -- PDF page number (null for non-PDFs)
  section_heading VARCHAR(500),   -- Section/chapter heading (if detected)
  
  -- Additional metadata
  metadata JSONB DEFAULT '{}',
  
  -- Timestamp
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_ingestion_chunks_file_id ON ingestion_chunks(file_id);
CREATE INDEX idx_ingestion_chunks_chunk_index ON ingestion_chunks(file_id, chunk_index);
CREATE INDEX idx_ingestion_chunks_page_number ON ingestion_chunks(file_id, page_number);

-- Unique constraint to prevent duplicate chunks
CREATE UNIQUE INDEX idx_unique_chunk ON ingestion_chunks(file_id, chunk_index);
```

**Field Descriptions**:
- `chunk_index`: Sequential position in document (0, 1, 2, ...)
- `char_offset`: Starting character position in original text
- `page_number`: PDF page (1-indexed), enables linking to ColPali page embeddings
- `section_heading`: Extracted from document structure (h1, h2, etc.)

---

### PostgreSQL Notifications

For real-time SSE updates, use PostgreSQL LISTEN/NOTIFY:

```sql
-- Trigger function to notify on status updates
CREATE OR REPLACE FUNCTION notify_status_update()
RETURNS TRIGGER AS $$
DECLARE
  payload JSON;
BEGIN
  payload = json_build_object(
    'file_id', NEW.file_id,
    'stage', NEW.stage,
    'progress', NEW.progress,
    'chunks_processed', NEW.chunks_processed,
    'total_chunks', NEW.total_chunks,
    'error_message', NEW.error_message
  );
  
  PERFORM pg_notify('status_updates', payload::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_notify_status_update
AFTER INSERT OR UPDATE ON ingestion_status
FOR EACH ROW
EXECUTE FUNCTION notify_status_update();
```

**Usage in API**:
```python
async with conn.listen('status_updates') as listener:
    async for notification in listener:
        update = json.loads(notification.payload)
        if update['file_id'] == file_id:
            yield update
```

---

## Milvus Schema

### Collection: documents

Single collection containing all vector types (dense, sparse, visual).

```python
from pymilvus import (
    CollectionSchema, FieldSchema, DataType, 
    Function, FunctionType, Collection
)

# Define schema
schema = CollectionSchema(
    fields=[
        # Primary key and references
        FieldSchema(
            name="id",
            dtype=DataType.VARCHAR,
            is_primary=True,
            max_length=64,
            description="Unique identifier (UUID)"
        ),
        FieldSchema(
            name="file_id",
            dtype=DataType.VARCHAR,
            max_length=64,
            description="Reference to ingestion_files.file_id"
        ),
        FieldSchema(
            name="chunk_index",
            dtype=DataType.INT64,
            description="Chunk position in document (-1 for page images)"
        ),
        FieldSchema(
            name="page_number",
            dtype=DataType.INT64,
            description="PDF page number (1-indexed)"
        ),
        
        # Modality indicator
        FieldSchema(
            name="modality",
            dtype=DataType.VARCHAR,
            max_length=20,
            description="'text' for text chunks, 'page_image' for PDF pages"
        ),
        
        # Text content (for BM25 and retrieval)
        FieldSchema(
            name="text",
            dtype=DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,  # Enable BM25 tokenization
            description="Chunk text or page placeholder"
        ),
        
        # Dense semantic embeddings
        FieldSchema(
            name="text_dense",
            dtype=DataType.FLOAT_VECTOR,
            dim=1536,  # text-embedding-3-small dimension
            description="Dense text embeddings"
        ),
        
        # Sparse BM25 embeddings (auto-generated)
        FieldSchema(
            name="text_sparse",
            dtype=DataType.SPARSE_FLOAT_VECTOR,
            description="BM25 sparse embeddings (auto-generated by Milvus)"
        ),
        
        # ColPali multi-vector embeddings (PDF pages only)
        FieldSchema(
            name="page_vectors",
            dtype=DataType.FLOAT_VECTOR,
            dim=128,  # ColPali patch dimension
            description="ColPali patch embeddings (128 patches per page)"
        ),
        
        # User ownership for filtering
        FieldSchema(
            name="user_id",
            dtype=DataType.VARCHAR,
            max_length=64,
            description="User who owns this document"
        ),
        
        # Additional metadata
        FieldSchema(
            name="metadata",
            dtype=DataType.JSON,
            description="Chunk-level metadata (section, keywords, etc.)"
        ),
    ],
    description="Hybrid search collection with dense, sparse, and visual vectors"
)

# Add BM25 function to auto-generate sparse embeddings
schema.add_function(
    Function(
        name="text_bm25_emb",
        input_field_names=["text"],
        output_field_names=["text_sparse"],
        function_type=FunctionType.BM25
    )
)

# Create collection
collection = Collection(
    name="documents",
    schema=schema,
    consistency_level="Strong"  # Ensure writes visible immediately
)

# Create indexes
collection.create_index(
    field_name="text_dense",
    index_params={
        "index_type": "HNSW",
        "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 200}
    }
)

collection.create_index(
    field_name="text_sparse",
    index_params={
        "index_type": "SPARSE_INVERTED_INDEX",
        "metric_type": "IP"  # Inner product for sparse
    }
)

collection.create_index(
    field_name="page_vectors",
    index_params={
        "index_type": "HNSW",
        "metric_type": "L2",
        "params": {"M": 16, "efConstruction": 200}
    }
)

# Create index on user_id for filtering
collection.create_index(
    field_name="user_id",
    index_params={"index_type": "STL_SORT"}
)

# Load collection into memory
collection.load()
```

**Record Types**:

1. **Text Chunks** (modality='text'):
   - `text_dense`: Populated with embeddings
   - `text_sparse`: Auto-generated by BM25 function
   - `page_vectors`: NULL
   - `chunk_index`: Sequential position (0, 1, 2, ...)

2. **PDF Page Images** (modality='page_image'):
   - `text_dense`: NULL
   - `text_sparse`: NULL (or minimal from placeholder text)
   - `page_vectors`: ColPali 128-patch embeddings
   - `chunk_index`: -1 (not a text chunk)

---

## MinIO Object Storage

### Bucket Structure

```
documents/
├── {user_id}/
│   ├── {file_id}/
│   │   ├── {original_filename}        # Original uploaded file
│   │   ├── metadata.json              # Extraction metadata (optional)
│   │   └── pages/                     # PDF page images (optional)
│   │       ├── page_001.png
│   │       ├── page_002.png
│   │       └── ...
```

**Example Path**:
```
s3://documents/user-123e4567/file-98f6g789/Q4_Report.pdf
s3://documents/user-123e4567/file-98f6g789/pages/page_001.png
```

**Bucket Policy** (future RLS integration):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"AWS": "arn:aws:iam::ingest-service"},
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::documents/*"
    }
  ]
}
```

---

## Redis Streams

### Stream: jobs:ingestion

Queue for processing jobs.

**Message Format**:
```json
{
  "job_id": "uuid",
  "file_id": "uuid",
  "user_id": "uuid",
  "storage_path": "s3://documents/user123/file456/document.pdf",
  "mime_type": "application/pdf",
  "original_filename": "Q4 Report.pdf",
  "created_at": "2025-11-05T10:00:00Z"
}
```

**Consumer Group**: `workers`  
**Consumer Name**: `worker-{worker_id}-{pid}`

**Commands**:
```bash
# Add job to stream
XADD jobs:ingestion * job_id {uuid} file_id {uuid} ...

# Create consumer group (once)
XGROUP CREATE jobs:ingestion workers 0 MKSTREAM

# Read messages (worker)
XREADGROUP GROUP workers worker-1 COUNT 1 BLOCK 5000 STREAMS jobs:ingestion >

# Acknowledge processing
XACK jobs:ingestion workers {message_id}
```

---

## Data Relationships

```
ingestion_files (PostgreSQL)
  ├─> ingestion_status (PostgreSQL) - 1:1
  ├─> ingestion_chunks (PostgreSQL) - 1:many
  ├─> documents (Milvus) - 1:many (via file_id)
  └─> MinIO bucket - 1:1 (via storage_path)

jobs:ingestion (Redis)
  └─> ingestion_files - many:1 (via file_id)
```

---

## State Transitions

### Ingestion Status

```
              ┌──────────┐
              │  queued  │
              └────┬─────┘
                   │
                   v
              ┌──────────┐
              │ parsing  │
              └────┬─────┘
                   │
                   v
           ┌───────────────┐
           │ classifying   │
           └───────┬───────┘
                   │
                   v
      ┌─────────────────────────┐
      │ extracting_metadata     │
      └─────────────┬───────────┘
                    │
                    v
              ┌───────────┐
              │ chunking  │
              └─────┬─────┘
                    │
                    v
              ┌───────────┐
              │ embedding │
              └─────┬─────┘
                    │
                    v
              ┌───────────┐
              │ indexing  │
              └─────┬─────┘
                    │
              ┌─────┴──────┐
              │            │
              v            v
        ┌───────────┐  ┌─────────┐
        │ completed │  │ failed  │
        └───────────┘  └─────────┘
```

**State Invariants**:
- `queued → parsing`: Job picked up by worker
- `parsing → failed`: File corrupted or unsupported format
- `embedding → failed`: Embedding service unavailable (after retries)
- `*→ completed`: All processing successful
- `failed`: Terminal state, requires manual intervention

---

## Validation Rules

### File Upload
- `mime_type` must be in supported list (PDF, DOCX, TXT, etc.)
- `size_bytes` <= 100MB (enforced before upload)
- `user_id` must be valid UUID

### Chunks
- `token_count` must be 400-800 for normal chunks
- `chunk_index` must be sequential per file_id
- `page_number` >= 1 for PDFs

### Embeddings
- `text_dense` dimension must match 1536 (text-embedding-3-small)
- `page_vectors` dimension must be 128 (ColPali patches)
- At least one vector type must be populated per record

### Status
- `progress` must be 0-100
- `chunks_processed` <= `total_chunks`
- `completed_at` must be > `started_at`

---

## Migration Scripts

### Initial Schema Creation

```sql
-- Run as superuser or database owner

BEGIN;

-- Create tables
\i 001_create_ingestion_tables.sql

-- Create indexes
\i 002_create_indexes.sql

-- Create triggers and functions
\i 003_create_triggers.sql

-- Create Milvus collection
-- (Run via Python script: python scripts/create_milvus_collection.py)

-- Create MinIO bucket
-- (Run via mc command: mc mb minio/documents)

COMMIT;
```

### Rollback Procedure

```sql
BEGIN;

-- Drop triggers
DROP TRIGGER IF EXISTS trigger_notify_status_update ON ingestion_status;
DROP TRIGGER IF EXISTS trigger_update_file_timestamp ON ingestion_status;

-- Drop functions
DROP FUNCTION IF EXISTS notify_status_update();
DROP FUNCTION IF EXISTS update_file_timestamp();

-- Drop tables (cascades to dependent objects)
DROP TABLE IF EXISTS ingestion_chunks CASCADE;
DROP TABLE IF EXISTS ingestion_status CASCADE;
DROP TABLE IF EXISTS ingestion_files CASCADE;

COMMIT;

-- Drop Milvus collection
-- collection.drop()

-- Delete MinIO bucket
-- mc rb --force minio/documents
```

---

## Example Data

### PostgreSQL: ingestion_files

```sql
INSERT INTO ingestion_files (
  file_id, user_id, filename, original_filename, mime_type, size_bytes, storage_path,
  document_type, language, classification_confidence,
  chunk_count, vector_count, processing_duration_seconds,
  extracted_title, extracted_author, permissions
) VALUES (
  'file-123',
  'user-456',
  '2025-11-05_report.pdf',
  'Q4 Financial Report.pdf',
  'application/pdf',
  2048000,
  's3://documents/user-456/file-123/2025-11-05_report.pdf',
  'report',
  'en',
  0.95,
  42,
  42,
  67,
  'Q4 Financial Report',
  'Finance Team',
  '{"owner": "user-456", "readers": ["group-789"], "visibility": "private"}'::jsonb
);
```

### PostgreSQL: ingestion_status

```sql
INSERT INTO ingestion_status (
  file_id, stage, progress, chunks_processed, total_chunks, started_at, completed_at
) VALUES (
  'file-123',
  'completed',
  100,
  42,
  42,
  '2025-11-05 10:00:00',
  '2025-11-05 10:01:07'
);
```

### Milvus: documents (text chunk)

```python
{
    "id": "chunk-123-0",
    "file_id": "file-123",
    "chunk_index": 0,
    "page_number": 1,
    "modality": "text",
    "text": "Q4 Financial Report\n\nThis quarter showed strong growth...",
    "text_dense": [0.123, -0.456, 0.789, ...],  # 1536 dims
    "text_sparse": {"42": 0.5, "137": 0.3, ...},  # Auto-generated BM25
    "page_vectors": None,
    "user_id": "user-456",
    "metadata": {"section": "Introduction", "keywords": ["growth", "revenue"]}
}
```

### Milvus: documents (PDF page)

```python
{
    "id": "page-123-1",
    "file_id": "file-123",
    "chunk_index": -1,
    "page_number": 1,
    "modality": "page_image",
    "text": "Page 1",
    "text_dense": None,
    "text_sparse": None,
    "page_vectors": [[0.1, 0.2, ...], [0.3, 0.4, ...], ...],  # 128 patches × 128 dims
    "user_id": "user-456",
    "metadata": {"page_width": 8.5, "page_height": 11, "has_charts": True}
}
```


