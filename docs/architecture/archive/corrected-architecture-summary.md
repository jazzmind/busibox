# Corrected Architecture Summary

**Created**: 2025-11-04  
**Status**: Reference  
**Category**: Architecture

## Container Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Proxmox Host                                │
│                                                                       │
│   PUBLIC ACCESS                                                       │
│   ┌────────────────┐                                                 │
│   │  proxy-lxc     │  200  10.96.200.200                            │
│   │   (nginx)      │  ← Users connect here                          │
│   └────────┬───────┘                                                 │
│            │                                                          │
│   ┌────────▼───────┐                                                 │
│   │  apps-lxc      │  201  10.96.200.201                            │
│   │  nginx+Next.js │  ← Web UI (public-facing)                      │
│   └────┬───────┬───┘                                                 │
│        │       │                                                      │
│        │       └──────────────────────┐                              │
│        │                               │                              │
│   INTERNAL SERVICES (Not exposed)     │                              │
│        │                               │                              │
│   ┌────▼────────────┐          ┌──────▼──────────┐                  │
│   │  agent-lxc      │          │  ingest-lxc     │                  │
│   │  Agent API      │  202     │  Ingest API     │  206             │
│   │  (RAG runner)   │          │  + Worker       │                  │
│   │                 │          │  + Redis        │                  │
│   │  Port: 8001     │          │  Port: 8000     │                  │
│   └────┬────────────┘          └─────────┬───────┘                  │
│        │                                  │                          │
│        │    ┌────────────────┐           │                          │
│        └────►  litellm-lxc   │◄──────────┘                          │
│             │  LLM Gateway   │  207                                  │
│             │  Port: 4000    │                                       │
│             └────────────────┘                                       │
│                                                                       │
│   DATA LAYER                                                         │
│   ┌────────────┐    ┌────────────┐    ┌────────────┐               │
│   │  pg-lxc    │    │ milvus-lxc │    │ files-lxc  │               │
│   │ PostgreSQL │    │  Milvus    │    │   MinIO    │               │
│   │  203       │    │  204       │    │   205      │               │
│   └────────────┘    └────────────┘    └────────────┘               │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

## Container Responsibilities

### Public-Facing

#### apps-lxc (201) - Web UI
- **Services**: nginx + Next.js applications
- **Access**: Public (through proxy-lxc)
- **Purpose**: 
  - Serve web interface to users
  - Handle browser authentication/sessions
  - Proxy requests to internal APIs
- **Calls**: 
  - → Ingest API (206) for file uploads/status
  - → Agent API (202) for AI agent invocations

### Internal Services

#### agent-lxc (202) - Agent Runner
- **Services**: Agent API (FastAPI from `srv/agent`)
- **Access**: Internal only (from apps-lxc)
- **Purpose**:
  - Execute AI agents
  - Perform RAG operations (search + generation)
  - Combine Milvus search with LLM calls
- **Calls**:
  - → liteLLM (207) for LLM inference
  - → Milvus (204) for vector search
  - → PostgreSQL (203) for metadata + RLS

#### ingest-lxc (206) - Document Processing
- **Services**: Ingest API + Worker + Redis
- **Access**: Internal only (from apps-lxc, future scrapers)
- **Purpose**:
  - Accept file uploads (API)
  - Track processing status (SSE)
  - Process documents (worker)
  - Generate embeddings
- **Calls**:
  - → MinIO (205) for file storage
  - → liteLLM (207) for embeddings
  - → Milvus (204) for vector storage
  - → PostgreSQL (203) for metadata

#### litellm-lxc (207) - LLM Gateway
- **Services**: liteLLM
- **Access**: Internal only
- **Purpose**:
  - Unified interface to LLM providers
  - Route to Ollama, vLLM, etc.
  - OpenAI-compatible API
- **Called by**:
  - ← Agent API (202) for LLM calls
  - ← Ingest Worker (206) for embeddings

### Data Layer

- **pg-lxc (203)**: PostgreSQL with RLS
- **milvus-lxc (204)**: Vector database
- **files-lxc (205)**: MinIO object storage

## API Flow Examples

### Document Upload

```
1. User uploads file in browser
   ↓
2. Next.js (apps-lxc:201) receives upload
   ↓
3. Next.js calls Ingest API (internal):
   POST http://10.96.200.206:8000/api/v1/ingest/upload
   ↓
4. Ingest API validates, stores in MinIO, queues job
   ↓
5. Returns fileID to Next.js
   ↓
6. Next.js returns fileID to browser
   ↓
7. Browser establishes SSE connection for status:
   GET /api/status/{fileId} (Next.js)
   ↓
8. Next.js proxies SSE from Ingest API (internal):
   GET http://10.96.200.206:8000/api/v1/ingest/status/{fileId}
   ↓
9. Status updates stream to browser through Next.js
```

**Key**: Ingest API is NEVER exposed publicly

### AI Agent Invocation (RAG)

```
1. User asks question in chat interface
   ↓
2. Next.js (apps-lxc:201) receives request
   ↓
3. Next.js calls Agent API (internal):
   POST http://10.96.200.202:8001/api/v1/agent/invoke
   ↓
4. Agent API performs semantic search:
   - Calls Milvus (204) for vector similarity
   - Applies RLS via PostgreSQL (203)
   ↓
5. Agent API calls liteLLM (207) with context:
   - Passes retrieved chunks + user question
   - liteLLM routes to appropriate model
   ↓
6. Returns generated response to Next.js
   ↓
7. Next.js streams response to browser
```

**Key**: Agent API is NEVER exposed publicly

## srv Directory Structure

### srv/agent → Agent API

**Refactor to keep**:
- `src/main.py` - FastAPI app for agent runner
- `src/routes/agent.py` - Agent invocation endpoints
- `src/routes/search.py` - Semantic search (RAG)
- `src/middleware/auth.py` - Auth if needed
- `src/services/` - Milvus, PostgreSQL, liteLLM clients

**Remove** (move to ingest):
- `src/routes/files.py` - File upload/download
- `src/routes/webhooks.py` - Webhook handling

### srv/ingest → Ingest API + Worker

**Create new**:
- `src/api/main.py` - FastAPI app for ingest API
- `src/api/routes/upload.py` - File upload endpoint
- `src/api/routes/status.py` - SSE status endpoint
- `src/api/services/minio.py` - MinIO client
- `src/api/services/redis.py` - Redis client
- `src/api/services/status.py` - Status tracking

**Keep existing**:
- `src/worker/` - Background processing
- `src/processors/` - Text extraction, chunking, embedding

## Key Architectural Principles

1. **Public vs Internal**:
   - Only apps-lxc (web UI) is public-facing
   - All backend APIs are internal-only
   - Next.js proxies all backend requests

2. **Separation of Concerns**:
   - **Ingest**: Document upload and processing
   - **Agent**: AI agent execution (RAG)
   - **LiteLLM**: LLM gateway
   - **Web UI**: User interface

3. **Security**:
   - Backend APIs not exposed to internet
   - Next.js handles user authentication
   - Internal APIs can use simpler auth (API keys or trust network)
   - PostgreSQL RLS enforces data access

4. **Scalability**:
   - Workers can scale independently
   - Agent API can scale independently
   - Redis Streams provides reliable queue

## Implementation Status

### Currently Working
- ✅ apps-lxc deployed with Next.js
- ✅ litellm-lxc deployed
- ✅ Data layer containers (pg, milvus, files)

### Needs Correction
- ⚠️ agent-lxc: Remove file/webhook routes, keep agent runner
- ⚠️ ingest-lxc: Add Ingest API (currently only has worker)

### Needs Creation
- ❌ Ingest API endpoints (upload, status SSE)
- ❌ Status tracking infrastructure
- ❌ Next.js proxy routes to internal APIs

## Related Documents

- `docs/architecture/architecture-correction.md` - Full analysis
- `docs/architecture/spec-corrections.md` - Spec changes needed
- `docs/session-notes/2025-11-04-architecture-correction.md` - Session notes


