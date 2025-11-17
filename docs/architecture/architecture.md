# Busibox Architecture

**Version**: 1.0.0  
**Last Updated**: 2025-10-14  
**Status**: Active Development

## Overview

Busibox is a local LLM infrastructure platform that provides secure file storage, automated document processing with embeddings, semantic search via RAG (Retrieval Augmented Generation), and AI agent operations—all running on isolated LXC containers with role-based access control.

### Design Principles

The platform is built on 7 core principles defined in [`.specify/memory/constitution.md`](../.specify/memory/constitution.md):

1. **Infrastructure as Code** (NON-NEGOTIABLE) - All infrastructure version-controlled, no manual configuration
2. **Service Isolation & Role-Based Security** - One service per container with RLS and RBAC
3. **Observability & Debuggability** - Structured logs, health endpoints, traceable operations
4. **Extensibility & Modularity** - Easy addition of services, LLM providers, applications
5. **Test-Driven Infrastructure** - Validation before deployment, smoke tests required
6. **Documentation as Contract** - Docs kept in sync with code
7. **Simplicity & Pragmatism** - Boring, proven technologies, no premature optimization

---

## System Architecture

### High-Level Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     Proxmox VE Host                          │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  files-lxc   │  │   pg-lxc     │  │ milvus-lxc   │     │
│  │   (MinIO)    │  │ (PostgreSQL) │  │ (Milvus +    │     │
│  │              │  │              │  │  Search API) │     │
│  │ S3 Storage   │  │ Users/Roles  │  │ Vector DB    │     │
│  │ Webhooks     │  │ File Metadata│  │ Hybrid Search│     │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘     │
│         │                 │                 │              │
│         │                 │                 │              │
│  ┌──────▼─────────────────▼─────────────────▼───────┐     │
│  │              agent-lxc                            │     │
│  │          (Agent API + liteLLM)                    │     │
│  │                                                   │     │
│  │  FastAPI  │  Auth/RBAC  │  Search  │  Agents     │     │
│  └──────┬────────────────────────────────────┬──────┘     │
│         │                                    │            │
│         │                                    │            │
│  ┌──────▼──────────┐              ┌─────────▼────────┐   │
│  │  ingest-lxc     │              │    apps-lxc      │   │
│  │ (Worker+Redis)  │              │  (nginx + apps)  │   │
│  │                 │              │                  │   │
│  │ File Processing │              │ Custom Apps      │   │
│  │ Text Extraction │              │ Reverse Proxy    │   │
│  │ Chunking        │              │                  │   │
│  │ Embedding Gen   │              │                  │   │
│  └─────────────────┘              └──────────────────┘   │
│                                                           │
│  ┌─────────────────┐              ┌──────────────────┐   │
│  │ openwebui-lxc   │              │  deploywatch     │   │
│  │   (OpenWebUI)   │              │ (systemd timer)  │   │
│  └─────────────────┘              └──────────────────┘   │
└───────────────────────────────────────────────────────────┘
```

---

## Container Architecture

### Container Inventory

| Container | CTID | IP | Services | Purpose | Privilege |
|-----------|------|----|----------|---------|-----------|
| **files-lxc** | 205 | 10.96.200.28 | MinIO | S3-compatible file storage | Privileged |
| **pg-lxc** | 203 | 10.96.200.26 | PostgreSQL 15+ | Relational database with RLS | Unprivileged |
| **milvus-lxc** | 204 | 10.96.200.27 | Milvus (Docker), Search API | Vector database + search service | Privileged (Docker-in-LXC) |
| **agent-lxc** | 207 | 10.96.200.30 | FastAPI, liteLLM | API gateway, auth, agent operations | Unprivileged |
| **ingest-lxc** | 206 | 10.96.200.29 | Python worker, Redis | File processing, job queue | Unprivileged |
| **apps-lxc** | 202 | 10.96.200.25 | nginx, Node apps | Application hosting, reverse proxy | Unprivileged |
| **openwebui-lxc** | 201 | 10.96.200.24 | OpenWebUI | LLM chat interface | Unprivileged |

### Network Configuration

- **Bridge**: vmbr0 (Proxmox default bridge)
- **Subnet**: 10.96.200.0/21
- **Gateway**: 10.96.200.1
- **IP Assignment**: Static, configured in `provision/pct/vars.env`

---

## Data Flow

### File Upload Flow

```
1. User → Agent API (POST /files/upload)
   ↓
2. Agent API → Generates presigned URL from MinIO
   ↓
3. User → Uploads file to MinIO (PUT presigned URL)
   ↓
4. MinIO → Sends webhook to Agent API (/webhooks/minio)
   ↓
5. Agent API → Creates file metadata in PostgreSQL
   ↓
6. Agent API → Enqueues job in Redis Streams (jobs:ingestion)
   ↓
7. Ingest Worker → Consumes job from Redis
   ↓
8. Worker → Downloads file from MinIO
   ↓
9. Worker → Extracts text (pdfplumber/PyPDF2/python-docx)
   ↓
10. Worker → Chunks text (spaCy, 512 tokens, 50 overlap)
   ↓
11. Worker → Generates embeddings (liteLLM)
   ↓
12. Worker → Stores embeddings in Milvus
   ↓
13. Worker → Stores metadata in PostgreSQL
   ↓
14. Worker → Updates job status to 'completed'
```

### Semantic Search Flow

```
1. User → Agent API (POST /search with query)
   ↓
2. Agent API → Converts query to embedding (liteLLM)
   ↓
3. Agent API → Searches Milvus (vector similarity)
   ↓
4. Milvus → Returns top K similar chunks
   ↓
5. Agent API → Filters results by user permissions (PostgreSQL RLS)
   ↓
6. Agent API → Enriches with file metadata
   ↓
7. Agent API → Returns ranked results to user
```

### AI Agent Flow (RAG)

```
1. User → Agent API (POST /agent/invoke with question)
   ↓
2. Agent API → Performs semantic search (steps 2-6 above)
   ↓
3. Agent API → Formats context from top chunks
   ↓
4. Agent API → Calls LLM (liteLLM) with context + question
   ↓
5. LLM → Generates response using retrieved context
   ↓
6. Agent API → Returns response + source citations to user
```

---

## Technology Stack

### Infrastructure Layer

- **Hypervisor**: Proxmox VE (LXC containers)
- **Provisioning**: Shell scripts (`provision/pct/`)
- **Configuration Management**: Ansible 2.15+ (`provision/ansible/`)
- **Service Management**: systemd
- **Monitoring**: journalctl (logs), future: Prometheus/Grafana

### Storage Layer

- **Object Storage**: MinIO (S3-compatible)
  - Buckets: `documents` (default)
  - Webhook notifications for file events
  
- **Relational Database**: PostgreSQL 15+
  - Row-Level Security (RLS) for multi-tenancy
  - Tables: users, roles, user_roles, files, chunks, ingestion_jobs
  
- **Vector Database**: Milvus 2.3+ (Standalone mode)
  - Collection: `document_embeddings`
  - Index: HNSW (Hierarchical Navigable Small World)
  - Metric: L2 (Euclidean distance)
  
- **Queue**: Redis 7+ Streams
  - Stream: `jobs:ingestion`
  - Consumer group: `workers`

### Application Layer

- **Agent API** (Python 3.11+)
  - Framework: FastAPI
  - Auth: JWT-based
  - Dependencies: psycopg2, pymilvus, redis-py, minio, structlog
  
- **Ingest Worker** (Python 3.11+)
  - Text Extraction: pdfplumber, PyPDF2, python-docx
  - Chunking: spaCy (en_core_web_sm)
  - Embedding: liteLLM client
  
- **LLM Gateway**: liteLLM
  - Unified interface to local LLM providers
  - Supports: Ollama, vLLM, custom servers
  - OpenAI-compatible API
  
- **App Server** (Node.js 18+)
  - Framework: Express.js
  - Reverse Proxy: nginx
  - Process Manager: PM2 or systemd

### Deployment Layer

- **Auto-deployment**: deploywatch (systemd timer)
  - Polls GitHub releases every 5 minutes
  - Pulls new versions and restarts services
  - Health check validation with rollback

---

## Security Architecture

### Authentication & Authorization

**Authentication**:
- JWT tokens issued by Agent API (`POST /auth/login`)
- Token includes user_id, roles, expiration
- Tokens passed in Authorization header: `Bearer <token>`

**Authorization (RBAC)**:
- Users assigned to Roles
- Roles have JSONB permissions object:
  ```json
  {
    "file": {"upload": true, "read": true, "delete": true},
    "search": {"query": true},
    "agent": {"invoke": true},
    "admin": {"manage_users": false, "manage_roles": false}
  }
  ```
- Agent API checks permissions before operations

**Row-Level Security (RLS)**:
- PostgreSQL RLS policies on `files` and `chunks` tables
- Policy: Users see only files they own (owner_id match) or files shared with their role_id
- RLS context set via `current_setting('app.user_id')`

### Network Security

- **Container Isolation**: Each service in dedicated LXC container
- **Firewall**: ufw configured on each container (allow only necessary ports)
- **Internal Communication**: Containers communicate via internal network (10.96.200.0/21)
- **External Access**: nginx reverse proxy for public-facing services
- **TLS**: Required for production (nginx with Let's Encrypt)

### Data Security

- **Secrets Management**: 
  - Environment files (`.env` per service)
  - Ansible vault for sensitive variables
  
- **Encryption**:
  - TLS for data in transit (production)
  - Future: Encryption at rest (MinIO buckets, PostgreSQL tablespaces)
  
- **File Access**:
  - Presigned URLs with expiration (default: 15 minutes)
  - No direct access to MinIO from users

---

## Scalability & Performance

### Current Scale Targets

- **Users**: 10-100 concurrent users
- **Host**: Single Proxmox host
- **Containers**: 5-7 LXC containers initially
- **Storage**: TB-scale (limited by host disk)
- **Ingestion**: 100s of files per hour

### Performance Goals

| Operation | Target | Success Criterion |
|-----------|--------|-------------------|
| Infrastructure Provisioning | <30 minutes | SC-001 |
| File Upload (100MB) | No errors/timeouts | SC-005 |
| Processing Queue Latency | <5 seconds | SC-007 |
| Text Extraction & Chunking | <1 minute (10-50 pages) | SC-008 |
| Embedding Generation | ≥100 chunks/minute | SC-009 |
| Semantic Search | <2 seconds | SC-010 |
| Agent Response (RAG) | <10 seconds | SC-014 |
| Concurrent Searches | 50 without degradation | SC-013 |
| Concurrent File Uploads | 100 without errors | SC-017 |

### Scaling Strategies

**Horizontal Scaling**:
- Add more ingest workers (multiple containers consuming same Redis stream)
- Add more app server containers (nginx load balancing)

**Vertical Scaling**:
- Increase container resources (CPU, RAM) in `provision/pct/vars.env`
- Add GPU passthrough for LLM acceleration

**Storage Scaling**:
- Milvus: Distributed mode (multiple nodes)
- PostgreSQL: Read replicas, connection pooling
- MinIO: Distributed mode (multiple nodes)

**Future Enhancements**:
- Multi-host Proxmox clustering
- Service mesh for inter-container communication
- CDN for file delivery

---

## Observability

### Logging

**Format**: Structured JSON logs

**Schema**:
```json
{
  "timestamp": "2025-10-14T12:34:56.789Z",
  "level": "INFO",
  "service": "agent-api",
  "message": "User authenticated",
  "trace_id": "uuid",
  "user_id": "uuid",
  "file_id": "uuid",
  "duration_ms": 123,
  "context": {"key": "value"}
}
```

**Libraries**:
- Python: structlog
- Node.js: winston

**Aggregation**: journalctl per-container (future: Loki/Elasticsearch)

### Health Checks

**Endpoint**: `GET /health` on all services

**Response** (healthy):
```json
{
  "status": "healthy",
  "service": "agent-api",
  "version": "1.0.0",
  "timestamp": "2025-10-14T12:34:56Z",
  "checks": {
    "database": "ok",
    "milvus": "ok",
    "minio": "ok",
    "redis": "ok"
  }
}
```

**HTTP Status**:
- 200 OK: Healthy
- 503 Service Unavailable: Unhealthy

### Tracing

**Trace ID Propagation**:
- Generated in agent API middleware (UUID per request)
- Passed to all service calls (header: `X-Trace-ID`)
- Logged with every operation
- Enables end-to-end request tracing

### Metrics (Future)

- **Prometheus exporters** on each service
- **Grafana dashboards** for visualization
- **Key metrics**:
  - Request rate, latency, errors (RED method)
  - Queue depth, job processing rate
  - Embedding generation throughput
  - Search query performance
  - Resource utilization (CPU, RAM, disk)

---

## Deployment

### Initial Provisioning

1. **Proxmox Host Setup**:
   ```bash
   cd /root/busibox/provision/pct
   vim vars.env  # Configure IPs, CTIDs, template
   bash create_lxc_base.sh
   ```

2. **Service Configuration** (from admin workstation):
   ```bash
   cd provision/ansible
   vim inventory/hosts.yml  # Verify IPs
   make all
   ```

3. **Vector DB Initialization**:
   ```bash
   python tools/milvus_init.py
   ```

4. **Verification**:
   ```bash
   make verify  # Health checks
   ```

### Service Updates

**Automated** (via deploywatch):
- Monitors GitHub releases every 5 minutes
- Pulls new code and restarts services
- Validates health checks, rolls back on failure

**Manual**:
```bash
# Update specific service
cd provision/ansible
make agent  # Or: minio, pg, milvus, ingest

# Update all services
make all
```

### Database Migrations

```bash
# Migrations stored in: provision/ansible/roles/postgres/files/migrations/
# Applied via Ansible task (check schema_migrations table for status)

# Manual execution (if needed)
psql -h 10.96.200.26 -U postgres -d busibox -f migrations/001_initial_schema.sql
```

---

## Development Workflow

### Adding a New Service

1. Create LXC container in `provision/pct/vars.env`:
   ```bash
   CT_NEWSERVICE=208
   IP_NEWSERVICE=10.96.200.31
   ```

2. Update `create_lxc_base.sh`:
   ```bash
   create_ct "$CT_NEWSERVICE" "$IP_NEWSERVICE" newservice-lxc unpriv
   ```

3. Create Ansible role:
   ```
   provision/ansible/roles/newservice/
   ├── tasks/
   │   └── main.yml
   └── files/
       └── (service files)
   ```

4. Add to `site.yml`:
   ```yaml
   - hosts: newservice
     roles:
       - newservice
   ```

5. Update inventory:
   ```yaml
   newservice:
     hosts:
       10.96.200.31:
   ```

### Adding a New API Endpoint

1. Update OpenAPI spec: `specs/001-create-an-initial/contracts/agent-api.yaml`
2. Implement route: `/srv/agent/src/routes/<name>.py`
3. Register in `main.py`: `app.include_router(router)`
4. Add tests: `/srv/agent/tests/integration/test_<name>.py`
5. Deploy: `make agent`

---

## Testing Strategy

### Smoke Tests (Required)

Automated via `make verify`:
- All health endpoints return 200
- PostgreSQL accepts connections
- MinIO console accessible
- Milvus accepts connections
- Agent API responds to authenticated requests
- Ingest worker can process test file

### Integration Tests (Recommended)

End-to-end workflows:
- File upload → webhook → job queue → processing → embeddings → search
- RBAC: Verify permission boundaries
- Agent RAG: Query → search → LLM → response with citations

### Performance Tests

- Load testing: 50 concurrent search requests (locust/k6)
- Stress testing: 100 concurrent file uploads
- Fault injection: Stop services, verify graceful degradation

---

## Troubleshooting

### Common Issues

**Container won't start**:
```bash
pct status <CTID>
journalctl -xe  # Inside container
```

**Service not responding**:
```bash
systemctl status <service>
journalctl -u <service> -n 50
```

**Database connection errors**:
```bash
psql -h 10.96.200.26 -U postgres -d busibox -c "SELECT version();"
# Check connection pool limits
```

**Milvus not accessible**:
```bash
# Check Docker status (milvus runs in Docker)
docker ps
docker logs milvus-standalone
```

### Debugging Tools

- **Logs**: `journalctl -u <service> -f`
- **Health checks**: `curl http://<container-ip>:<port>/health`
- **Database**: `psql`, `pgAdmin`
- **Object storage**: MinIO console (http://10.96.200.28:9001)
- **Vector DB**: `pymilvus` client, Milvus dashboard
- **Network**: `ping`, `telnet`, `nc`, `tcpdump`

---

## References

- **Constitution**: [`.specify/memory/constitution.md`](../.specify/memory/constitution.md)
- **Specification**: [`specs/001-create-an-initial/spec.md`](../specs/001-create-an-initial/spec.md)
- **Implementation Plan**: [`specs/001-create-an-initial/plan.md`](../specs/001-create-an-initial/plan.md)
- **Task List**: [`specs/001-create-an-initial/tasks.md`](../specs/001-create-an-initial/tasks.md)
- **Data Model**: [`specs/001-create-an-initial/data-model.md`](../specs/001-create-an-initial/data-model.md)
- **API Contracts**: [`specs/001-create-an-initial/contracts/`](../specs/001-create-an-initial/contracts/)
- **Quickstart Guide**: [`specs/001-create-an-initial/quickstart.md`](../specs/001-create-an-initial/quickstart.md)
- **Research Decisions**: [`specs/001-create-an-initial/research.md`](../specs/001-create-an-initial/research.md)

---

**Document Version**: 1.0.0  
**Last Review**: 2025-10-14  
**Next Review**: TBD (after MVP deployment)

