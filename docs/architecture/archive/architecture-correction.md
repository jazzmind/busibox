# Architecture Correction: Agent vs Ingest Services

**Created**: 2025-11-04  
**Status**: Analysis  
**Category**: Architecture

## Problem Statement

There is a fundamental architectural inconsistency in the current system design:

### Current (Incorrect) Architecture

1. **agent-lxc container** (202) is specified to run:
   - FastAPI service (`srv/agent/src/main.py`)
   - File upload/download routes
   - Search routes
   - Agent routes
   - Webhook routes

2. **ingest-lxc container** (206) runs:
   - Python worker (Redis Streams consumer)
   - Redis server
   - Background processing only

### What's Actually Working

According to the actual container layout (`provision/pct/vars.env`):

- **apps-lxc** (201): nginx + Next.js applications (web UI)
- **agent-lxc** (202): Agent API/runner (executes agents, talks to liteLLM and Milvus)
- **ingest-lxc** (206): Worker + Redis
- **litellm-lxc** (207): liteLLM gateway (separate container)

### The Conflict

The **FastAPI service** in `srv/agent` is an incorrect specification that:
1. Mixes file upload/ingest responsibilities into the agent runner
2. Should be split: file upload/status → ingest service, agent execution → agent service
3. Creates confusion about responsibilities

## Corrected Architecture

### Container Layout

| Container | CTID | IP | Services | Purpose |
|-----------|------|-----|----------|---------|
| **proxy-lxc** | 200 | 10.96.200.200 | nginx | Reverse proxy |
| **apps-lxc** | 201 | 10.96.200.201 | nginx, Next.js | Web UI |
| **agent-lxc** | 202 | 10.96.200.202 | Agent API | Agent runner |
| **pg-lxc** | 203 | 10.96.200.203 | PostgreSQL | Database |
| **milvus-lxc** | 204 | 10.96.200.204 | Milvus | Vector DB |
| **files-lxc** | 205 | 10.96.200.205 | MinIO | Object storage |
| **ingest-lxc** | 206 | 10.96.200.206 | API + Worker + Redis | Ingestion pipeline |
| **litellm-lxc** | 207 | 10.96.200.30 | liteLLM | LLM gateway |

### Component Responsibilities

#### 1. Apps Container (apps-lxc, CTID 201)

**Services**:
- nginx (reverse proxy for apps)
- Next.js applications (web UI)

**Responsibilities**:
- Serve web interface to users
- Handle user authentication/sessions (browser)
- Display document status
- Provide chat interface
- Call backend APIs (agent, ingest)

**Ports**:
- nginx: 80, 443
- Next.js: 3000 (internal)

**Does NOT include**:
- File upload backend API
- Document processing
- Agent execution

---

#### 2. Agent Container (agent-lxc, CTID 202)

**Services**:
- Agent API (executes AI agents)

**Responsibilities**:
- Execute AI agents (RAG operations)
- Call liteLLM for LLM inference
- Call Milvus for semantic search
- Combine retrieved context with LLM calls
- Return agent responses to web UI

**Ports**:
- Agent API: 8001 (or configured port)

**Communication**:
- → liteLLM (207): LLM inference requests
- → Milvus (204): Vector similarity search
- → PostgreSQL (203): Metadata queries, RLS
- ← Web UI (201): Agent invocation requests

**Does NOT include**:
- File upload/ingest
- Document processing
- Status tracking

---

#### 3. LiteLLM Container (litellm-lxc, CTID 207)

**Services**:
- liteLLM gateway

**Responsibilities**:
- Unified interface to multiple LLM providers
- Route requests to Ollama, vLLM, or other backends
- Handle model selection and load balancing
- Provide OpenAI-compatible API

**Ports**:
- liteLLM: 4000 (or configured port)

**Communication**:
- → Ollama (208): Local model inference
- → vLLM (209): Local model inference
- ← Agent API (202): LLM requests
- ← Ingest Worker (206): Embedding generation

---

#### 4. Ingestion Container (ingest-lxc, CTID 206)

**Services**:
- FastAPI service (API endpoints - **INTERNAL ONLY**)
- Python worker (background processing)
- Redis Streams (job queue)

**Network Access**:
- **Internal only** - Not exposed through proxy
- Accessible from: apps-lxc (201), future scraper containers
- NOT accessible from: public internet, external networks

**Responsibilities**:

##### FastAPI Service Endpoints (Internal API):

**File Upload API**:
```
POST /api/v1/ingest/upload
- Accepts file upload from internal services only
- Validates permissions
- Stores file in MinIO
- Returns fileID
- Queues processing job
- Returns: { fileId, status: "queued" }
```

**Status Tracking API (SSE)**:
```
GET /api/v1/ingest/status/{fileId}
- Server-Sent Events endpoint (internal only)
- Streams processing status updates
- Returns stages: uploading, parsing, transforming, chunking, embedding, indexing
- Real-time progress updates
```

**Status Query API** (REST alternative):
```
GET /api/v1/ingest/files/{fileId}/status
- Returns current processing status (internal only)
- { fileId, stage, progress, error? }
```

##### Worker Process:
- Consumes jobs from Redis Streams
- Processes documents through pipeline:
  1. Download from MinIO
  2. Text extraction (PDF, DOCX, etc.)
  3. Text transformation/cleaning
  4. Chunking
  5. Embedding generation (via liteLLM)
  6. Store embeddings in Milvus
  7. Store metadata in PostgreSQL
  8. Update status at each stage

**Ports**:
- FastAPI: 8000 (internal network only)
- Redis: 6379 (internal network only)

**Communication**:
- → MinIO (205): File download
- → liteLLM (207): Embedding generation
- → Milvus (204): Vector storage
- → PostgreSQL (203): Metadata storage, status updates
- ← Apps container (201): API requests (file upload, status)
- ← Future scraper containers: API requests (automated ingestion)

---

#### 5. What Happens to srv/agent?

The `srv/agent` directory should be **refactored**:

**Keep (for Agent API in agent-lxc)**:
- Main FastAPI app structure
- Agent execution/invocation logic
- Search/RAG logic (calling Milvus + liteLLM)
- Authentication middleware (if needed for agent API)

**Remove (these go to ingest service)**:
- File upload routes → Moving to ingest service
- Webhook routes → Replaced by SSE status tracking in ingest service
- File download routes → Moving to ingest service

**Result**:
- `srv/agent` becomes the Agent API (agent runner)
- Create new `srv/ingest/src/api/` for Ingest API
- Clear separation: agent execution vs document ingestion

---

## Data Flow: Document Upload with Status Tracking

### 1. Upload Request

```
User (Browser) → Web UI (apps-lxc:201, Next.js)
                      ↓
                Next.js API route/server action
                      ↓
                Ingest API (ingest-lxc:206:8000) - INTERNAL
                      ↓
                Validate file
                      ↓
                Store in MinIO (files-lxc:205)
                      ↓
                Generate fileID
                      ↓
                Queue job in Redis (ingest-lxc:206:6379)
                      ↓
                Return { fileId: "xxx", status: "queued" }
                      ↓
                Next.js returns to browser with fileID
```

**Key Points**:
- Ingest API is **not exposed publicly**
- Web UI (Next.js) proxies requests to ingest API
- Internal container-to-container communication only

### 2. Status Streaming (SSE)

```
Browser establishes SSE connection:
  GET /api/status/{fileId} (Next.js API route)
        ↓
  Next.js proxies to Ingest API (internal):
  GET http://10.96.200.206:8000/api/v1/ingest/status/{fileId}
        ↓
Ingest API streams updates (proxied through Next.js):
  
  data: { stage: "queued", progress: 0 }
  
  data: { stage: "parsing", progress: 20 }
  
  data: { stage: "chunking", progress: 40 }
  
  data: { stage: "embedding", progress: 60, chunks_processed: 10, total_chunks: 25 }
  
  data: { stage: "indexing", progress: 80 }
  
  data: { stage: "completed", progress: 100, vector_count: 25 }
```

**Key Points**:
- Browser connects to Next.js public endpoint
- Next.js proxies SSE stream from internal Ingest API
- Ingest API never exposed to public network

### 3. Worker Processing

```
Worker (background)
  ↓
Consume job from Redis
  ↓
Update status: "parsing"
  ↓
Extract text → Update: "chunking"
  ↓
Chunk text → Update: "embedding" (with progress)
  ↓
Generate embeddings → Update: "indexing"
  ↓
Store in Milvus + PostgreSQL
  ↓
Update status: "completed"
```

### 4. Status Storage

Status updates should be stored in PostgreSQL:

```sql
CREATE TABLE ingestion_status (
  file_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  stage VARCHAR(50) NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  chunks_processed INTEGER,
  total_chunks INTEGER,
  error_message TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ingestion_status_user ON ingestion_status(user_id);
CREATE INDEX idx_ingestion_status_stage ON ingestion_status(stage);
```

---

## Implementation Changes Required

### 1. Remove/Archive srv/agent

```bash
# Archive the incorrect agent API
cd /Users/wessonnenreich/Code/sonnenreich/busibox
mv srv/agent srv/agent.archived
```

### 2. Create New Ingest API Service

**Directory Structure**:
```
srv/ingest/
├── src/
│   ├── api/
│   │   ├── main.py           # FastAPI app
│   │   ├── routes/
│   │   │   ├── upload.py     # File upload endpoint
│   │   │   ├── status.py     # SSE status endpoint
│   │   │   └── health.py     # Health checks
│   │   ├── middleware/
│   │   │   ├── auth.py       # Auth middleware
│   │   │   └── cors.py       # CORS config
│   │   └── services/
│   │       ├── minio.py      # MinIO client
│   │       ├── redis.py      # Redis client
│   │       ├── status.py     # Status tracking
│   │       └── permissions.py # Permission handling
│   ├── worker/
│   │   ├── main.py           # Worker entry point (existing)
│   │   ├── processors/       # (existing)
│   │   └── services/         # (existing)
│   └── shared/
│       ├── models.py         # Shared data models
│       └── config.py         # Shared config
├── requirements.txt
└── README.md
```

### 3. Update Ansible Roles

**Create new role**: `provision/ansible/roles/ingest_api/`

```yaml
# tasks/main.yml
- name: Install FastAPI and dependencies
  pip:
    name:
      - fastapi
      - uvicorn
      - sse-starlette
      - python-multipart
    virtualenv: /srv/ingest/venv

- name: Create systemd service for ingest API
  template:
    src: ingest-api.service.j2
    dest: /etc/systemd/system/ingest-api.service

- name: Enable and start ingest API
  systemd:
    name: ingest-api
    enabled: yes
    state: started
```

**Update**: `provision/ansible/roles/agent_api/` → **DELETE** or rename to archive

### 4. Update Architecture Documentation

Update `docs/architecture/architecture.md`:
- Remove references to agent API FastAPI service
- Clarify agent-lxc runs Next.js + liteLLM only
- Document ingest service API endpoints
- Update data flow diagrams

### 5. Update Spec Document

Update `specs/001-create-an-initial/spec.md`:
- Correct FR-006 to FR-010 (file storage requirements) → Point to ingest service
- Add FR for SSE status tracking
- Remove agent API FastAPI references
- Update success criteria

---

## Benefits of Corrected Architecture

### 1. Clear Separation of Concerns
- **Agent container**: User interface and LLM gateway
- **Ingest container**: Document processing pipeline (API + worker)

### 2. Permission Propagation
- File upload through ingest API captures user context
- Permissions stored with fileID
- Permissions propagate through entire pipeline

### 3. Observability
- SSE provides real-time status updates
- Each processing stage is visible to users
- No need for polling or webhooks

### 4. Scalability
- API and worker are separate processes
- Can scale workers independently
- Redis Streams provides reliable job queue

### 5. Simpler Deployment
- Fewer services per container
- Clear responsibilities
- No conflicting FastAPI services

---

## Migration Path

### Phase 1: Create Ingest API (Priority: HIGH)
1. Create FastAPI service in `srv/ingest/src/api/`
2. Implement file upload endpoint
3. Implement SSE status endpoint
4. Test locally with Docker Compose

### Phase 2: Update Worker (Priority: HIGH)
1. Add status update calls at each processing stage
2. Test status updates flow to PostgreSQL
3. Verify SSE clients receive updates

### Phase 3: Remove Agent API (Priority: MEDIUM)
1. Archive `srv/agent` directory
2. Remove `agent_api` Ansible role
3. Remove agent-api systemd service from deployments
4. Verify agent-lxc still runs Next.js + liteLLM

### Phase 4: Update Documentation (Priority: MEDIUM)
1. Update architecture.md
2. Update spec.md
3. Update data flow diagrams
4. Create API documentation for ingest service

### Phase 5: Update Next.js App (Priority: LOW)
1. Update API client to call ingest service
2. Implement SSE status display component
3. Add progress indicators for uploads

---

## Questions for Resolution

1. **Authentication Strategy**: How should internal APIs authenticate requests from Next.js?
   - Option A: JWT tokens passed from Next.js
   - Option B: Internal API keys (container-to-container)
   - Option C: No auth (trust internal network)
   - **Recommended**: Option B or C for internal services, given network isolation

2. **Search API**: Where should semantic search live? ✅ **RESOLVED**
   - ✅ Lives in **Agent API (agent-lxc)**
   - Agent API handles RAG operations (search + LLM calls)
   - Next.js calls Agent API for agent invocations

3. **Agent API Authentication**: How should Next.js authenticate to Agent API?
   - Same options as ingest API
   - Consider: Agent API may need user context for RLS

4. **File Download**: Where should file download endpoint live?
   - Option A: Ingest API (since it handles upload)
   - Option B: Separate endpoint in Agent API
   - Option C: Next.js generates presigned URLs from MinIO directly

---

## Recommendation

**Immediate Action**: 

1. Confirm this architectural correction aligns with your vision
2. Start Phase 1 (Create Ingest API) 
3. Archive `srv/agent` to prevent confusion
4. Update spec.md with corrected architecture

**Rules to Follow**:
- `.cursor/rules/001-documentation-organization.md` (this doc)
- `.cursor/rules/002-script-organization.md` (for any new scripts)


