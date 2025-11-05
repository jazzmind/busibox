# Specification Corrections for 001-create-an-initial

**Created**: 2025-11-04  
**Status**: Action Required  
**Category**: Architecture  
**Related**: `architecture-correction.md`

## Overview

This document outlines specific changes needed to `specs/001-create-an-initial/spec.md` to align with the corrected architecture where:
1. Agent container runs Next.js + liteLLM (NOT FastAPI from srv/agent)
2. Ingest container runs FastAPI API + Worker + Redis
3. File upload and status tracking are part of the ingest service

---

## User Story Changes

### User Story 2 - Secure File Upload and Storage

**Current (Incorrect)**:
> "A user with appropriate permissions needs to upload files to the system for processing."

**Correction Needed**:
> "A user with appropriate permissions uploads files through the **ingestion service API**. The API validates permissions, stores files in MinIO, generates a unique fileID, and returns it to the user for tracking processing status."

**Updated Acceptance Scenarios**:

1. **Given** an authenticated user in the Next.js application, **When** user uploads a file through the ingest API, **Then** file is stored in MinIO and user receives fileID and initial status "queued"

2. **Given** a file is uploaded, **When** ingest API stores the file, **Then** user permissions are captured and stored with the file metadata for propagation through the pipeline

3. **Given** a file upload completes, **When** ingest API generates fileID, **Then** processing job is queued in Redis Streams with fileID and user context

---

### NEW User Story 2.5 - Document Processing Status Tracking (Priority: P2.5)

A user who has uploaded a document needs real-time visibility into the processing pipeline. The system provides Server-Sent Events (SSE) endpoint that streams status updates as the document progresses through stages: queued → parsing → transforming → chunking → embedding → indexing → completed.

**Why this priority**: Users need feedback that their upload is being processed. Without status tracking, uploads feel like a black box and users don't know if processing succeeded or how long it will take.

**Independent Test**: Can be tested by uploading a document, connecting to the SSE endpoint with the fileID, and verifying that status updates are received for each processing stage.

**Acceptance Scenarios**:

1. **Given** a file has been uploaded and fileID returned, **When** user connects to SSE status endpoint with fileID, **Then** real-time status updates stream as document progresses through processing stages

2. **Given** document processing is in progress, **When** worker completes each stage (parsing, chunking, embedding, etc.), **Then** status update is written to database and pushed to any connected SSE clients

3. **Given** document processing fails at any stage, **When** error occurs, **Then** SSE stream sends error status with details and processing stops gracefully

4. **Given** user is viewing status in UI, **When** status updates arrive via SSE, **Then** UI displays current stage, progress percentage, and details (e.g., chunks processed)

---

### User Story 3 - Automated File Processing and Embedding

**Updated Acceptance Scenarios**:

1. **Given** a file upload is queued in Redis, **When** ingest worker receives the job, **Then** worker updates status to "parsing", retrieves file from MinIO, and extracts text

2. **Given** text is extracted, **When** worker chunks the text, **Then** status updates to "chunking" with progress information (chunks created)

3. **Given** chunks are created, **When** worker generates embeddings via liteLLM, **Then** status updates to "embedding" with progress (chunks processed / total chunks)

4. **Given** embeddings are generated, **When** worker stores them in Milvus, **Then** status updates to "indexing"

5. **Given** all data is stored, **When** worker completes successfully, **Then** status updates to "completed" with final metadata (vector count, chunk count)

---

## Functional Requirements Changes

### New Requirements (Ingestion Service API)

**FR-006A**: Ingestion service MUST provide HTTP API endpoint for file upload that accepts authenticated requests

**FR-006B**: File upload endpoint MUST validate user permissions before accepting files

**FR-006C**: File upload endpoint MUST generate unique fileID (UUID) for each uploaded file

**FR-006D**: File upload endpoint MUST store files in MinIO with user permissions metadata

**FR-006E**: File upload endpoint MUST queue processing job in Redis Streams with fileID and user context

**FR-006F**: File upload endpoint MUST return fileID and initial status to client

**FR-007A**: Ingestion service MUST provide Server-Sent Events (SSE) endpoint for real-time status tracking

**FR-007B**: SSE status endpoint MUST stream updates when processing stage changes

**FR-007C**: SSE status endpoint MUST include progress information (percentage, chunks processed, etc.)

**FR-007D**: SSE status endpoint MUST handle multiple concurrent clients for same fileID

**FR-007E**: SSE status endpoint MUST send final "completed" or "failed" event when processing finishes

**FR-007F**: Ingestion service MUST provide REST endpoint for polling status as fallback to SSE

### Updated Requirements (Worker Process)

**FR-011A**: Ingest worker MUST update status to "parsing" before text extraction

**FR-012A**: Ingest worker MUST update status to "chunking" with chunk count after text extraction

**FR-013A**: Ingest worker MUST update status to "embedding" with progress during embedding generation

**FR-013B**: Embedding progress MUST include chunks_processed and total_chunks

**FR-014A**: Ingest worker MUST update status to "indexing" before storing in Milvus

**FR-015A**: Ingest worker MUST update status to "completed" with final counts after successful storage

**FR-016A**: Status updates MUST be written to PostgreSQL for persistence

**FR-016B**: Status updates MUST trigger notifications to connected SSE clients

### Requirements to Remove

**REMOVE FR-022 to FR-025** (Agent Operations):
- These describe agent API functionality that should be in Next.js application
- Agent operations should be handled by Next.js server actions calling liteLLM
- Not part of infrastructure platform—part of application layer

### Updated Requirements (Agent Container)

**FR-022 (Revised)**: Agent container MUST run Next.js application for user interface

**FR-023 (Revised)**: Agent container MUST run liteLLM gateway for LLM model access

**FR-024 (Revised)**: Next.js application MUST authenticate users and manage sessions

**FR-025 (Revised)**: Next.js application MUST call ingest service API for file uploads

**FR-026 (Revised)**: Next.js application MUST connect to SSE endpoints for status tracking

**FR-027 (Revised)**: Next.js application MUST call liteLLM for LLM operations

---

## Key Entities Changes

### New Entity: Ingestion Status

**Ingestion Status**: Represents the current processing state of an uploaded file
- **file_id** (UUID, PK): Unique identifier for the file
- **user_id** (UUID, FK): User who uploaded the file
- **stage** (enum): Current processing stage (queued, parsing, chunking, embedding, indexing, completed, failed)
- **progress** (integer): Progress percentage (0-100)
- **chunks_processed** (integer, nullable): Number of chunks processed (during embedding)
- **total_chunks** (integer, nullable): Total number of chunks
- **error_message** (text, nullable): Error details if stage = failed
- **created_at** (timestamp): When file was uploaded
- **updated_at** (timestamp): Last status update time

### Updated Entity: File

**File**: Represents an uploaded document
- **file_id** (UUID, PK): Unique identifier
- **user_id** (UUID, FK): Owner of the file
- **filename** (string): Original filename
- **mime_type** (string): Content type
- **size_bytes** (bigint): File size
- **storage_path** (string): Path in MinIO
- **permissions** (jsonb): Permission metadata
- **created_at** (timestamp): Upload timestamp
- **processing_status** (FK): Reference to ingestion_status

### Remove Entity: Agent

The "Agent" entity as specified is not part of infrastructure platform. Agents are:
- Configured in Next.js application
- Or defined in separate agent-server (Mastra-based)
- Not stored in infrastructure database

---

## Success Criteria Changes

### New Success Criteria (Status Tracking)

**SC-006A**: Users receive fileID within 1 second of file upload completion

**SC-006B**: SSE status endpoint establishes connection within 500ms

**SC-006C**: Status updates appear in SSE stream within 2 seconds of worker stage change

**SC-006D**: Status updates persist in PostgreSQL with 100% accuracy

**SC-006E**: Multiple SSE clients can track same fileID simultaneously without errors

**SC-006F**: Failed processing includes actionable error message in status

### Updated Success Criteria (File Operations)

**SC-005**: Users can upload files up to 100MB through ingest API without errors or timeouts

**SC-007**: Uploaded files are queued in Redis within 2 seconds of upload completion (changed from 5 seconds)

**SC-008**: Status updates to "parsing" within 5 seconds of worker picking up job

**SC-009**: Embedding stage shows progress updates at least every 5 chunks processed

### Remove Success Criteria

**REMOVE SC-014 to SC-016** (Agent Operations):
- These measure agent API that doesn't exist in infrastructure
- Application-level metrics, not infrastructure metrics

---

## Container Architecture Updates

### Agent Container (agent-lxc)

**Current Description** (Incorrect):
> "FastAPI, liteLLM | API gateway, auth, agent operations"

**Corrected Description**:
> "Next.js app, liteLLM | User interface, LLM gateway"

**Services**:
- Next.js application (port 3000)
- liteLLM gateway (port 4000)

**Deployed by Ansible roles**:
- `nextjs_app`
- `litellm`

**NOT deployed**:
- `agent_api` role (should be removed)

### Ingestion Container (ingest-lxc)

**Current Description** (Incomplete):
> "Python worker, Redis | File processing, job queue"

**Corrected Description**:
> "FastAPI API, Python worker, Redis | File upload, status tracking, document processing pipeline, job queue"

**Services**:
- FastAPI application (port 8000)
- Python worker process (background)
- Redis Streams (port 6379)

**Deployed by Ansible roles**:
- `ingest_api` (NEW - needs to be created)
- `ingest_worker` (exists, may need updates)
- Redis (part of ingest_worker or separate role)

---

## Data Flow Updates

### File Upload Flow (Corrected)

```
1. User (Browser) → Next.js App (agent-lxc)
   ↓
2. Next.js → Ingest API (ingest-lxc:8000) POST /api/v1/ingest/upload
   ↓
3. Ingest API validates auth & permissions
   ↓
4. Ingest API stores file in MinIO (files-lxc)
   ↓
5. Ingest API generates fileID (UUID)
   ↓
6. Ingest API writes initial status to PostgreSQL (pg-lxc)
   ↓
7. Ingest API queues job in Redis Streams (ingest-lxc)
   ↓
8. Ingest API returns to Next.js: { fileId, status: "queued" }
   ↓
9. Next.js returns to user with fileID
```

### Status Tracking Flow (NEW)

```
1. User (Browser) establishes SSE connection
   ↓
2. Next.js proxies to Ingest API SSE endpoint
   GET /api/v1/ingest/status/{fileId}
   ↓
3. Ingest API checks PostgreSQL for current status
   ↓
4. Ingest API sends current status as SSE event
   ↓
5. Ingest API keeps connection open, listens for updates
   ↓
6. Worker updates status in PostgreSQL
   ↓
7. PostgreSQL notifies Ingest API (via LISTEN/NOTIFY)
   ↓
8. Ingest API pushes SSE event to all connected clients
   ↓
9. Browser receives and displays status update
```

### Processing Flow (Updated with Status)

```
1. Worker consumes job from Redis
   ↓
2. Worker updates status: "parsing"
   ↓
3. Worker downloads file from MinIO
   ↓
4. Worker extracts text
   ↓
5. Worker updates status: "chunking"
   ↓
6. Worker chunks text
   ↓
7. Worker updates status: "embedding", progress: 0%
   ↓
8. Worker generates embeddings (updates progress per batch)
   ↓ (progress: 20%, 40%, 60%, 80%)
9. Worker updates status: "indexing"
   ↓
10. Worker stores embeddings in Milvus
    ↓
11. Worker stores metadata in PostgreSQL
    ↓
12. Worker updates status: "completed", vector_count: N
```

---

## Assumptions Updates

### Add:
- Ingest service API uses JWT tokens from Next.js application for authentication
- SSE connections have 5-minute timeout, clients reconnect automatically
- Status updates use PostgreSQL LISTEN/NOTIFY for real-time push to SSE clients
- Progress updates during embedding happen every 5 chunks (configurable)

### Remove:
- "Default authentication uses JWT or session-based tokens" (too vague, specify in ingest API)

### Clarify:
- "LLM providers (Ollama, etc.) are installed and configured separately" → Add: "liteLLM gateway in agent-lxc provides unified interface to all LLM providers"

---

## Implementation Priority

### Phase 1: Core Correction (IMMEDIATE)
1. Update spec.md with corrected architecture
2. Archive srv/agent directory
3. Document ingest API specification
4. Create API contract for ingest service

### Phase 2: Ingest API Implementation (HIGH)
1. Create FastAPI service in srv/ingest/src/api/
2. Implement file upload endpoint
3. Implement SSE status endpoint  
4. Create Ansible role for ingest API
5. Test locally with Docker Compose

### Phase 3: Worker Updates (HIGH)
1. Add status update calls to worker
2. Implement PostgreSQL LISTEN/NOTIFY
3. Test status flow end-to-end

### Phase 4: Next.js Integration (MEDIUM)
1. Update Next.js app to call ingest API
2. Implement SSE status display component
3. Add upload progress UI

### Phase 5: Deployment (MEDIUM)
1. Remove agent_api Ansible role
2. Deploy ingest_api role to ingest-lxc
3. Verify agent-lxc runs only Next.js + liteLLM
4. Update architecture diagrams

---

## Files to Update

1. `specs/001-create-an-initial/spec.md` - Incorporate all changes above
2. `docs/architecture/architecture.md` - Update container descriptions and data flows
3. `provision/ansible/roles/agent_api/` - **REMOVE or ARCHIVE**
4. `provision/ansible/site.yml` - Remove agent_api role reference
5. `provision/ansible/inventory/*/group_vars/all.yml` - Update service URLs

---

## Questions Requiring Decisions

1. **Authentication**: Should ingest API validate JWT tokens from Next.js or use API keys?
2. **Network isolation**: Should ingest API be accessible only from agent-lxc or more broadly?
3. **SSE vs WebSocket**: SSE is simpler for one-way status updates. Confirm this is acceptable.
4. **Status retention**: How long should ingestion_status records be kept in PostgreSQL?
5. **Search API**: Where should semantic search endpoint live? (Options: Next.js API routes, separate service, part of ingest API)


