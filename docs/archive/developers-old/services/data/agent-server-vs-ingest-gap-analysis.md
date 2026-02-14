---
title: "Agent Server vs Ingest Gap Analysis"
category: "developer"
order: 60
description: "Analysis of extending agent-server vs creating separate ingest service"
published: true
---

# Gap Analysis: Agent-Server vs Ingest Service

**Created**: 2025-11-04  
**Status**: Analysis  
**Category**: Architecture

## Executive Summary

The **agent-server** (Mastra-based) is deployed to `agent-lxc` and already has RAG document upload capabilities, but with several limitations. The question is: **should we extend agent-server or create a separate ingest service?**

**Recommendation**: **Extend agent-server** with missing capabilities rather than creating a separate ingest service.

---

## Current Agent-Server Capabilities

### Repository: `agent-server`
- **Framework**: Mastra (@mastra/core)
- **Language**: TypeScript/Node.js
- **Authentication**: OAuth 2.0 + JWT
- **Database**: PostgreSQL (via @mastra/pg)

### Existing RAG Features

#### ✅ RAG Database Management
```typescript
// API Endpoints (in /api/rag-routes.ts)
GET    /rag/databases           - List RAG databases
POST   /rag/databases           - Create RAG database
GET    /rag/databases/:id       - Get specific database
DELETE /rag/databases/:id       - Delete database
```

#### ✅ Document Upload
```typescript
// API Endpoint
POST /rag/databases/:id/documents
- Accepts multipart/form-data
- Stores document metadata in PostgreSQL
- Returns document ID
- Triggers processing (async, but not tracked)
```

#### ✅ Document Metadata Storage
```sql
-- Table: rag_documents
- id (UUID)
- rag_database_id (foreign key)
- filename
- original_filename
- file_type
- file_size
- chunk_count
- embedding_status (pending, processing, completed, failed)
- error_message
- metadata (JSON)
- uploaded_by
- created_at, updated_at
```

#### ✅ Basic Document Processing
```typescript
// In RAGService.processDocument()
- Accepts buffer (file in memory)
- Basic text extraction (buffer.toString())
- Chunking with configurable size/overlap
- Updates document status
```

#### ⚠️ Search (Mock Implementation)
```typescript
// API Endpoint
POST /rag/databases/:id/search
- Query parameter
- Returns mock results
- No actual vector search yet
```

---

## Functional Gaps in Agent-Server

### ❌ Gap 1: No MinIO Integration
**Current**: Files stored as buffers in memory during processing  
**Need**: Store files in MinIO for persistence and scalability

**Impact**: 
- Files not persisted after processing
- Memory limits file sizes
- No file download capability

### ❌ Gap 2: No Worker Queue (Redis Streams)
**Current**: Processing happens synchronously in API request  
**Need**: Queue jobs in Redis, process asynchronously with worker

**Impact**:
- API request blocks during processing
- Timeout issues for large files
- Can't scale processing independently

### ❌ Gap 3: No Real-Time Status Tracking
**Current**: `embedding_status` field in database  
**Need**: SSE endpoint for real-time status updates

**Impact**:
- Users can't see processing progress
- Must poll database for status
- No visibility into which stage is running

### ❌ Gap 4: Mock Embedding Generation
**Current**: No actual embeddings generated  
**Need**: Call liteLLM for embedding generation

**Impact**:
- Search doesn't work
- RAG functionality incomplete

### ❌ Gap 5: No Vector Database Integration
**Current**: No Milvus integration (mock only)  
**Need**: Store embeddings in Milvus, query for similarity

**Impact**:
- Search doesn't work
- RAG functionality incomplete

### ❌ Gap 6: Basic Text Extraction
**Current**: `buffer.toString('utf-8')` - only works for text files  
**Need**: PDF parser (pdfplumber), DOCX parser (mammoth), etc.

**Impact**:
- Can't process PDF or Word documents
- Limited to text files only

---

## Original Ingest Service Specification

The spec called for a **separate ingest service** with:

1. FastAPI service (Python)
2. File upload to MinIO
3. Redis Streams for job queue
4. Background worker for processing
5. SSE status tracking
6. Text extraction (PDF, DOCX)
7. Chunking
8. Embedding generation (via liteLLM)
9. Vector storage (Milvus)
10. Metadata storage (PostgreSQL)

**Problem**: This duplicates much of what agent-server already has!

---

## Comparison: Agent-Server vs Ingest Spec

| Feature | Agent-Server | Ingest Spec | Gap? |
|---------|-------------|-------------|------|
| **File Upload API** | ✅ Has | ✅ Specified | ✅ Exists |
| **MinIO Storage** | ❌ Missing | ✅ Specified | **GAP** |
| **Document Metadata** | ✅ PostgreSQL | ✅ PostgreSQL | ✅ Exists |
| **Worker Queue** | ❌ Missing | ✅ Redis Streams | **GAP** |
| **Status Tracking** | ⚠️ Basic | ✅ SSE | **GAP** |
| **Text Extraction** | ⚠️ Basic | ✅ PDF/DOCX | **GAP** |
| **Chunking** | ✅ Has | ✅ Specified | ✅ Exists |
| **Embedding Gen** | ❌ Mock | ✅ liteLLM | **GAP** |
| **Vector Storage** | ❌ Mock | ✅ Milvus | **GAP** |
| **Search API** | ⚠️ Mock | ✅ Vector search | **GAP** |
| **Agent Execution** | ✅ Has | ❌ N/A | - |
| **Authentication** | ✅ OAuth + JWT | ✅ Required | ✅ Exists |

**Overlap**: 40% of functionality already exists in agent-server!

---

## Decision: Extend Agent-Server vs Separate Service

### Option A: Extend Agent-Server (RECOMMENDED)

**Pros**:
- ✅ Avoid code duplication
- ✅ Single service = simpler deployment
- ✅ Existing auth, database, API routes
- ✅ Mastra provides abstractions for RAG
- ✅ Already has document upload flow
- ✅ Can use Mastra's RAG packages (@mastra/rag, @mastra/chroma, etc.)

**Cons**:
- ⚠️ Mix TypeScript (agent-server) with potential Python dependencies?
- ⚠️ Adds complexity to agent-server
- ⚠️ Worker needs to run alongside API server

**Implementation**:
1. Add MinIO SDK (@mastra packages or native Node.js client)
2. Add Redis client for job queue
3. Add background worker process (can run in same container)
4. Add SSE endpoint for status tracking
5. Integrate Milvus (@mastra/pg has pgvector support, or use @mastra/chroma)
6. Add PDF/DOCX parsers (pdf-parse, mammoth)
7. Integrate liteLLM for embeddings (HTTP calls)

### Option B: Separate Ingest Service

**Pros**:
- ✅ Clear separation of concerns
- ✅ Can use Python for better ML/data libraries
- ✅ Independent scaling

**Cons**:
- ❌ Duplicates 40% of existing functionality
- ❌ Another service to deploy/maintain
- ❌ Duplicate PostgreSQL schema for documents
- ❌ Need to keep both services in sync
- ❌ More complex architecture

**Implementation**:
- Create new Python FastAPI service
- Implement all features from scratch
- Duplicate document tables or share database
- Agent-server would still have RAG routes (confusion)

---

## Recommended Approach: Extend Agent-Server

### Phase 1: Add MinIO Storage

**Where**: `agent-server/src/mastra/services/rag.ts`

```typescript
// Add MinIO client
import { S3Client, PutObjectCommand } from '@aws-sdk/client-s3';

class RAGService {
  private minioClient: S3Client;

  constructor() {
    this.minioClient = new S3Client({
      endpoint: process.env.MINIO_ENDPOINT,
      credentials: {
        accessKeyId: process.env.MINIO_ACCESS_KEY,
        secretAccessKey: process.env.MINIO_SECRET_KEY,
      },
      forcePathStyle: true,
    });
  }

  async uploadDocument(...) {
    // Store file in MinIO instead of just buffer
    const key = `rag/${databaseId}/${filename}`;
    await this.minioClient.send(new PutObjectCommand({
      Bucket: 'documents',
      Key: key,
      Body: file.buffer,
    }));

    // Store key in document record
    const document = await this.pgStore.db.one(`
      INSERT INTO rag_documents (...)
      VALUES (..., $storage_key)
    `, [..., key]);

    // Queue job instead of processing directly
    await this.queueProcessingJob(document);
  }
}
```

### Phase 2: Add Redis Streams Queue

**Where**: `agent-server/src/mastra/services/queue.ts` (new)

```typescript
import { createClient } from 'redis';

export class QueueService {
  private redisClient;

  async queueJob(jobType: string, data: any) {
    await this.redisClient.xAdd('jobs:ingestion', '*', {
      job_id: data.documentId,
      job_type: jobType,
      data: JSON.stringify(data),
      created_at: new Date().toISOString(),
    });
  }
}
```

### Phase 3: Add Background Worker

**Where**: `agent-server/src/worker/index.ts` (new)

```typescript
// Separate process that can run in same container
import { QueueService } from '../mastra/services/queue';
import { RAGService } from '../mastra/services/rag';

class DocumentProcessor {
  async start() {
    while (true) {
      // Consume jobs from Redis Streams
      const jobs = await this.redis.xReadGroup(...);
      for (const job of jobs) {
        await this.processJob(job);
      }
    }
  }

  async processJob(job: any) {
    // Update status: processing
    await this.updateStatus(job.documentId, 'processing', 'Extracting text');

    // Download from MinIO
    const file = await this.downloadFromMinIO(job.storageKey);

    // Extract text (PDF/DOCX)
    const text = await this.extractText(file, job.fileType);

    // Chunk text
    await this.updateStatus(job.documentId, 'processing', 'Chunking text');
    const chunks = this.chunkText(text);

    // Generate embeddings
    await this.updateStatus(job.documentId, 'processing', 'Generating embeddings');
    const embeddings = await this.generateEmbeddings(chunks);

    // Store in Milvus
    await this.updateStatus(job.documentId, 'processing', 'Indexing vectors');
    await this.storeInMilvus(embeddings);

    // Complete
    await this.updateStatus(job.documentId, 'completed', 'Processing complete');
  }
}
```

### Phase 4: Add SSE Status Endpoint

**Where**: `agent-server/src/api/rag-routes.ts`

```typescript
export const documentStatusRoute = registerApiRoute('/rag/databases/:id/documents/:docId/status', {
  method: 'GET',
  handler: async (c) => {
    const documentId = c.req.param('docId');

    // Set up SSE
    c.header('Content-Type', 'text/event-stream');
    c.header('Cache-Control', 'no-cache');
    c.header('Connection', 'keep-alive');

    // Stream status updates
    const stream = new ReadableStream({
      async start(controller) {
        // Send current status
        const doc = await ragService.getDocument(documentId);
        controller.enqueue(`data: ${JSON.stringify(doc)}\n\n`);

        // Listen for updates (PostgreSQL NOTIFY or polling)
        const interval = setInterval(async () => {
          const updated = await ragService.getDocument(documentId);
          controller.enqueue(`data: ${JSON.stringify(updated)}\n\n`);
          
          if (updated.embedding_status === 'completed' || updated.embedding_status === 'failed') {
            clearInterval(interval);
            controller.close();
          }
        }, 1000);
      },
    });

    return new Response(stream);
  },
});
```

### Phase 5: Add Text Extraction

**Where**: `agent-server/src/mastra/services/parsers.ts` (new)

```typescript
import pdfParse from 'pdf-parse';
import mammoth from 'mammoth';

export class TextExtractor {
  async extractText(buffer: Buffer, fileType: string): Promise<string> {
    switch (fileType) {
      case 'application/pdf':
        const pdfData = await pdfParse(buffer);
        return pdfData.text;
      case 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        const result = await mammoth.extractRawText({ buffer });
        return result.value;
      case 'text/plain':
        return buffer.toString('utf-8');
      default:
        throw new Error(`Unsupported file type: ${fileType}`);
    }
  }
}
```

### Phase 6: Integrate Milvus & Embeddings

**Where**: Extend `RAGService`

```typescript
// Use Mastra's existing RAG packages
import { RAG } from '@mastra/rag';
import { PostgresVectorDatabase } from '@mastra/pg'; // pgvector
// OR
import { Chroma } from '@mastra/chroma';

class RAGService {
  async initializeRAGInstance(database: RAGDatabase) {
    switch (database.vector_store_type) {
      case 'postgres':
        return new RAG({
          vector: new PostgresVectorDatabase(/* config */),
          embeddings: {
            provider: 'litellm',
            endpoint: process.env.LITELLM_URL,
            model: database.embedding_model,
          },
        });
      case 'chroma':
        return new RAG({
          vector: new Chroma(/* config */),
          embeddings: { /* ... */ },
        });
    }
  }
}
```

---

## Modified Architecture with Extended Agent-Server

```
┌─────────────────────────────────────────────────────────────┐
│                     agent-lxc (CTID 202)                     │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Mastra Agent Server (TypeScript)                    │   │
│  ├─────────────────────────────────────────────────────┤   │
│  │                                                       │   │
│  │  API Server                                           │   │
│  │  ├─ /agents/* (agent execution)                      │   │
│  │  ├─ /rag/databases/* (RAG DB management)             │   │
│  │  ├─ /rag/databases/:id/documents (upload)  ← NEW     │   │
│  │  ├─ /rag/databases/:id/documents/:id/status ← NEW    │   │
│  │  │     (SSE status streaming)                        │   │
│  │  └─ /rag/databases/:id/search (vector search) ← FIX  │   │
│  │                                                       │   │
│  ├─────────────────────────────────────────────────────┤   │
│  │                                                       │   │
│  │  Background Worker (separate Node process)   ← NEW   │   │
│  │  ├─ Consumes Redis Streams                           │   │
│  │  ├─ Downloads from MinIO                             │   │
│  │  ├─ Extracts text (PDF/DOCX)                         │   │
│  │  ├─ Chunks text                                      │   │
│  │  ├─ Generates embeddings (liteLLM)                   │   │
│  │  ├─ Stores in Milvus/pgvector                        │   │
│  │  └─ Updates PostgreSQL status                        │   │
│  │                                                       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                               │
│  Communication:                                              │
│  ← apps-lxc: API requests                                   │
│  → litellm-lxc: Embeddings + LLM calls                      │
│  → milvus-lxc: Vector storage/search                        │
│  → pg-lxc: Metadata, status                                 │
│  → files-lxc: File storage (MinIO)                          │
│  ↔ Internal: Redis Streams (in-memory or external)         │
└─────────────────────────────────────────────────────────────┘
```

**Key Change**: Instead of creating separate `ingest-lxc` service, extend agent-lxc with worker capability.

---

## Implementation Plan

### Immediate Steps

1. ✅ **Gap Analysis** (this document)
2. **Decide on approach** (extend vs separate)
3. **Update spec.md** with corrected architecture

### If Extending Agent-Server (Recommended)

#### Phase 1: Storage & Queue (Week 1)
- [ ] Add MinIO SDK to agent-server
- [ ] Update `uploadDocument` to store in MinIO
- [ ] Add Redis client for job queue
- [ ] Queue jobs instead of processing synchronously
- [ ] Test: Upload → stored in MinIO + job queued

#### Phase 2: Worker & Status (Week 2)
- [ ] Create worker process in agent-server
- [ ] Implement Redis Streams consumer
- [ ] Add status table/fields for tracking
- [ ] Add PostgreSQL NOTIFY for status changes
- [ ] Test: Job processed, status updated

#### Phase 3: Processing Pipeline (Week 2-3)
- [ ] Add PDF parser (pdf-parse)
- [ ] Add DOCX parser (mammoth)
- [ ] Test text extraction for various formats
- [ ] Test chunking with real documents

#### Phase 4: Embeddings & Vector Storage (Week 3-4)
- [ ] Integrate liteLLM for embeddings
- [ ] Choose vector store (pgvector vs Milvus)
- [ ] Implement vector storage
- [ ] Test embedding generation + storage

#### Phase 5: Search & SSE (Week 4)
- [ ] Implement real vector search
- [ ] Add SSE status endpoint
- [ ] Test end-to-end: upload → process → search

#### Phase 6: Deployment (Week 5)
- [ ] Update Ansible role for agent-lxc
- [ ] Add systemd service for worker
- [ ] Update environment variables
- [ ] Deploy to test environment
- [ ] Deploy to production

---

## Files to Update

### In agent-server repository

**New files**:
- `src/worker/index.ts` - Worker entry point
- `src/worker/processor.ts` - Document processing logic
- `src/mastra/services/queue.ts` - Redis Streams wrapper
- `src/mastra/services/minio.ts` - MinIO client wrapper
- `src/mastra/services/parsers.ts` - PDF/DOCX parsers
- `src/mastra/services/embeddings.ts` - liteLLM integration

**Modified files**:
- `src/mastra/services/rag.ts` - Add MinIO, queue, real Milvus integration
- `src/api/rag-routes.ts` - Add SSE status endpoint
- `package.json` - Add dependencies (aws-sdk, redis, pdf-parse, mammoth)

### In busibox repository

**Update**:
- `provision/ansible/roles/agent_api/` - Add worker service, Redis, environment variables
- `specs/001-create-an-initial/spec.md` - Remove separate ingest service, document extended agent-server
- `docs/architecture/architecture.md` - Update with corrected architecture

**Remove/Archive**:
- `srv/agent/` - This was incorrect, agent is from separate repo
- References to separate ingest-lxc service API

---

## Questions for Confirmation

1. ✅ **Confirmed**: Agent container uses agent-server repo (Mastra), not `srv/agent`
2. ❓ **Decision needed**: Extend agent-server vs separate ingest service?
3. ❓ **If extending**: Run worker as separate systemd service or same process?
4. ❓ **If extending**: Redis inside agent-lxc or separate container?
5. ❓ **Vector store**: Milvus (separate container) or pgvector (in PostgreSQL)?
6. ❓ **Authentication**: How should apps-lxc authenticate to agent API?


