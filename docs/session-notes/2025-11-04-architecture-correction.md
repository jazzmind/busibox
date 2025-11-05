# Session: Architecture Correction - Agent vs Ingest Services

**Date**: 2025-11-04  
**Status**: Analysis Complete  
**Category**: Session Notes

## Problem Identified

The system specification contains a logical inconsistency with **two agent systems**:

1. **Correct**: `agent-lxc` container running Next.js application (working)
2. **Incorrect**: `srv/agent` FastAPI service with file upload, search, and webhook routes (conflicts with architecture)

## Root Cause

Multiple misunderstandings about the agent container:
1. **Incorrect assumption**: `srv/agent` directory contains agent API code
2. **Reality**: Agent API is deployed from **separate agent-server repository** (Mastra-based)
3. **Discovery**: Agent-server already has RAG document upload capabilities
4. **Conflict**: Spec calls for separate ingest service, but agent-server overlaps 40% of functionality

## Solution

### Corrected Architecture

#### Container Layout
- **proxy-lxc** (200): Main reverse proxy
- **apps-lxc** (201): nginx + Next.js web UI
- **agent-lxc** (202): Agent API/runner (RAG operations)
- **pg-lxc** (203): PostgreSQL database
- **milvus-lxc** (204): Milvus vector database
- **files-lxc** (205): MinIO object storage
- **ingest-lxc** (206): Ingest API + Worker + Redis
- **litellm-lxc** (207): liteLLM gateway (separate container)

#### Apps Container (apps-lxc, CTID 201)
- **Services**: nginx + Next.js applications
- **Purpose**: Web UI for users
- **Ports**: 80/443 (nginx), 3000 (Next.js internal)
- **Publicly accessible** through proxy

#### Agent Container (agent-lxc, CTID 202)
- **Services**: Agent API (FastAPI)
- **Purpose**: Execute AI agents, RAG operations
- **Ports**: 8001 (internal only)
- **Calls**: liteLLM (207), Milvus (204)

#### LiteLLM Container (litellm-lxc, CTID 207)
- **Services**: liteLLM gateway
- **Purpose**: Unified LLM interface
- **Ports**: 4000 (internal only)
- **Calls**: Ollama, vLLM, other LLM providers

#### Ingestion Container (ingest-lxc, CTID 206)
- **Services**: Ingest API + Python Worker + Redis
- **Purpose**: Document ingestion pipeline with status tracking
- **Ports**: 8000 (FastAPI), 6379 (Redis) - **INTERNAL ONLY**
- **Accessible from**: apps-lxc, future scraper containers
- **NOT exposed** through proxy

### Key Changes

1. **File Upload**:
   - Goes through **Ingest API** (`POST /api/v1/ingest/upload`)
   - Returns `fileID` for tracking
   - Captures user permissions

2. **Status Tracking**:
   - **Server-Sent Events (SSE)** endpoint: `GET /api/v1/ingest/status/{fileId}`
   - Real-time updates as document progresses through stages:
     - `queued` → `parsing` → `chunking` → `embedding` → `indexing` → `completed`
   - Progress information (%, chunks processed, etc.)

3. **Worker Updates**:
   - Worker updates status at each stage
   - Status persisted in PostgreSQL
   - PostgreSQL LISTEN/NOTIFY pushes updates to SSE clients

## Documents Created

1. **`docs/architecture/architecture-correction.md`** ⚠️ (needs revision)
   - Initial analysis based on incorrect srv/agent assumption
   - Still useful for container layout and data flows

2. **`docs/architecture/spec-corrections.md`** ⚠️ (needs revision)  
   - Based on assumption of separate ingest service
   - Needs update for agent-server extension approach

3. **`docs/architecture/corrected-architecture-summary.md`** ⚠️ (needs revision)
   - Quick reference based on initial incorrect assumptions

4. **`docs/architecture/agent-server-vs-ingest-gap-analysis.md`** ✅ **PRIMARY**
   - Correct understanding of agent-server
   - Functional gap analysis
   - Recommendation: Extend agent-server
   - Implementation plan with phases

5. **`docs/session-notes/2025-11-04-architecture-correction.md`** (this file)
   - Session summary with corrected understanding

## Next Steps

### Immediate (Before Any Implementation)

1. **Review and confirm** the corrected architecture aligns with your vision
2. **Answer key questions**:
   - How should ingest API authenticate requests? (JWT from Next.js? API keys?)
   - Should ingest API be accessible only from agent-lxc or more broadly?
   - Where should semantic search endpoint live?

### Implementation Phases

#### Phase 1: Specification Updates (PRIORITY: IMMEDIATE)
- [ ] Update `specs/001-create-an-initial/spec.md` with corrections
- [ ] Archive `srv/agent` directory to prevent confusion
- [ ] Create API contract for ingest service

#### Phase 2: Ingest API Development (PRIORITY: HIGH)
- [ ] Create FastAPI service in `srv/ingest/src/api/`
- [ ] Implement file upload endpoint
- [ ] Implement SSE status endpoint
- [ ] Create `ingest_api` Ansible role
- [ ] Test locally with Docker Compose

#### Phase 3: Worker Updates (PRIORITY: HIGH)
- [ ] Add status update calls at each processing stage
- [ ] Implement PostgreSQL LISTEN/NOTIFY for real-time updates
- [ ] Test status flow end-to-end

#### Phase 4: Next.js Integration (PRIORITY: MEDIUM)
- [ ] Update Next.js app to call ingest API
- [ ] Implement SSE status display component
- [ ] Add upload progress UI

#### Phase 5: Deployment Cleanup (PRIORITY: MEDIUM)
- [ ] Remove `agent_api` Ansible role
- [ ] Deploy `ingest_api` to ingest-lxc
- [ ] Verify agent-lxc runs only Next.js + liteLLM
- [ ] Update architecture diagrams

## Benefits

1. **Clear separation of concerns**
   - Agent container: UI + LLM gateway
   - Ingest container: Document processing

2. **Permission propagation**
   - File upload captures user context at ingestion
   - Permissions flow through entire pipeline

3. **Real-time observability**
   - SSE provides live status updates
   - Each stage is visible to users
   - No polling required

4. **Scalability**
   - API and worker are separate processes
   - Workers can scale independently
   - Redis Streams provides reliable queue

## Files to Update/Remove

### Update
- `specs/001-create-an-initial/spec.md`
- `docs/architecture/architecture.md`
- `provision/ansible/site.yml`
- `provision/ansible/inventory/*/group_vars/all.yml`

### Remove/Archive
- `srv/agent/` → Archive to `srv/agent.archived/`
- `provision/ansible/roles/agent_api/` → Remove or archive

### Create
- `srv/ingest/src/api/` (FastAPI service)
- `provision/ansible/roles/ingest_api/` (Ansible role)
- Database migration for `ingestion_status` table

## Rules Applied

- `.cursor/rules/001-documentation-organization.md` - Placed documentation in appropriate categories
- `.cursor/rules/002-script-organization.md` - Identified need for Ansible role changes
- Project architecture principles - Maintained clear separation of concerns

## Critical Discovery

**Agent container uses agent-server repository** (not `srv/agent`):
- Mastra-based TypeScript application
- Already has RAG database management
- Already has document upload endpoint
- Already has basic processing (chunking)
- Missing: MinIO, Redis queue, SSE status, real embeddings, Milvus integration

## Key Decision Point

**Should we extend agent-server or create separate ingest service?**

### Option A: Extend Agent-Server (RECOMMENDED)
- ✅ Avoids 40% code duplication
- ✅ Single service = simpler
- ✅ Uses existing auth, database, API routes
- ✅ Mastra has RAG abstractions
- ⚠️ Adds complexity to agent-server

### Option B: Separate Ingest Service
- ❌ Duplicates existing functionality
- ❌ Another service to maintain
- ❌ Schema duplication
- ✅ Clear separation
- ✅ Could use Python

**See**: `docs/architecture/agent-server-vs-ingest-gap-analysis.md` for full analysis

## User Confirmation Needed

1. ✅ **Confirmed**: Agent uses agent-server repo (Mastra-based)
2. ❓ **Decision**: Extend agent-server vs separate ingest service?
3. ❓ **If extending**: Worker as separate systemd service or same process?
4. ❓ **If extending**: Redis inside agent-lxc or separate?
5. ❓ **Vector store**: Milvus vs pgvector?
6. ❓ **Authentication**: How should apps-lxc auth to agent API?


