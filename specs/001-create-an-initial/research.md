# Research & Technology Decisions: Local LLM Infrastructure Platform

**Feature**: 001-create-an-initial  
**Created**: 2025-10-14  
**Status**: Complete

This document captures research findings and technical decisions for implementing the busibox local LLM infrastructure platform.

## 1. Structured Logging Format

### Decision

Use **JSON-formatted structured logging** with the following schema:

```json
{
  "timestamp": "2025-10-14T12:34:56.789Z",
  "level": "INFO|DEBUG|WARNING|ERROR|CRITICAL",
  "service": "agent-api|ingest-worker|...",
  "message": "Human-readable message",
  "trace_id": "uuid-for-request-tracing",
  "user_id": "uuid-if-applicable",
  "file_id": "uuid-if-applicable",
  "duration_ms": 123,
  "error": {
    "type": "ExceptionClassName",
    "message": "Error details",
    "stack": "Stack trace if available"
  },
  "context": {
    "key": "value"
  }
}
```

### Rationale

- **JSON format**: Machine-parseable, supports nested structures, standard for log aggregation tools
- **Consistent fields**: Required fields (timestamp, level, service, message) ensure all logs have minimum viable information
- **Optional fields**: trace_id for request correlation, user_id for audit trails, duration_ms for performance tracking
- **Error structure**: Separate error object makes exception handling searchable
- **Context**: Flexible object for service-specific metadata

### Implementation

**Python services** (agent-api, ingest-worker):
```python
import structlog

logger = structlog.get_logger()
logger.info("event_name", user_id=user_id, file_id=file_id, duration_ms=123)
```

**Configuration**:
- Use `python-json-logger` or `structlog` library
- Configure formatters in each service's logging config
- Output to stdout (captured by systemd journalctl)

**Node.js services** (app-server):
```javascript
const winston = require('winston');
const logger = winston.createLogger({
  format: winston.format.json(),
  defaultMeta: { service: 'app-server' }
});
```

### Alternatives Considered

- **ECS (Elastic Common Schema)**: Too heavyweight for initial implementation, future migration path if Elasticsearch adopted
- **Plain text**: Not machine-parseable, difficult to aggregate and search
- **Custom binary format**: Overcomplicated, no tooling support

---

## 2. Database Migration Strategy

### Decision

Use **versioned SQL scripts** with manual execution and documented rollback procedures.

```
provision/ansible/roles/postgres/files/migrations/
├── 001_initial_schema.sql
├── 001_rollback.sql
├── 002_add_rls_policies.sql
├── 002_rollback.sql
├── ...
└── README.md  # Migration execution instructions
```

### Rationale

- **Simplicity**: No additional tooling dependencies (Alembic, Flyway), aligns with Constitution Principle VII (Simplicity)
- **Infrastructure as Code**: Migration scripts are versioned alongside Ansible roles
- **Explicit rollback**: Each migration has corresponding rollback script, reviewed together
- **Ansible integration**: Migrations applied via Ansible playbook task with version tracking in database

### Implementation

**Migration table** (tracks applied migrations):
```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Ansible task**:
```yaml
- name: Apply database migrations
  postgresql_query:
    db: busibox
    query: "{{ lookup('file', 'migrations/{{ item }}.sql') }}"
  loop: "{{ migrations_to_apply }}"
  when: item not in applied_migrations
```

**Execution process**:
1. Check `schema_migrations` table for applied versions
2. Apply unapplied migrations in numerical order
3. Record version in `schema_migrations`
4. On failure, run corresponding rollback script

### Alternatives Considered

- **Alembic** (Python): Adds dependency, autogeneration can be unreliable, overkill for infrastructure DB
- **Flyway** (Java): Requires JVM, heavyweight for simple schema management
- **sqitch**: Better than above but still additional tooling, manual approach is more transparent

---

## 3. Health Check Implementation

### Decision

Standard health check endpoint contract:

**Endpoint**: `GET /health`

**Response (healthy)**:
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
**HTTP Status**: 200 OK

**Response (unhealthy)**:
```json
{
  "status": "unhealthy",
  "service": "agent-api",
  "version": "1.0.0",
  "timestamp": "2025-10-14T12:34:56Z",
  "checks": {
    "database": "ok",
    "milvus": "error: connection refused",
    "minio": "ok",
    "redis": "ok"
  }
}
```
**HTTP Status**: 503 Service Unavailable

### Rationale

- **Liveness vs Readiness**: Single `/health` endpoint serves both (service process running + dependencies available)
- **Dependency checks**: Each health check tests connections to dependent services (DB, vector DB, etc.)
- **Fast response**: Health checks should complete in <1 second (don't test deep operations)
- **Standard codes**: 200 = healthy, 503 = unhealthy (enables load balancer integration)

### Implementation

**Python FastAPI**:
```python
@app.get("/health")
async def health_check():
    checks = {
        "database": await check_postgres(),
        "milvus": await check_milvus(),
        "minio": await check_minio(),
    }
    status = "healthy" if all(c == "ok" for c in checks.values()) else "unhealthy"
    status_code = 200 if status == "healthy" else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": status, "service": "agent-api", "checks": checks}
    )
```

**Testing**:
```bash
# Makefile verify target
make verify:
    @echo "Checking service health..."
    @curl -f http://10.96.200.24:3001/health || exit 1
    @curl -f http://10.96.200.25:3002/health || exit 1
    @echo "All services healthy"
```

### Alternatives Considered

- **Separate liveness/readiness**: Overengineering for current scale, add if Kubernetes deployment later
- **Minimal health check (always 200)**: Doesn't detect dependency failures, not useful for monitoring
- **Deep health checks**: Testing actual operations (e.g., write to DB) slows down health checks, use separate integration tests

---

## 4. LLM Provider Integration

### Decision

Use **liteLLM** as unified gateway with configuration-driven provider routing.

**Configuration** (`/etc/litellm/config.yaml`):
```yaml
model_list:
  - model_name: llama2-7b
    litellm_params:
      model: ollama/llama2
      api_base: http://10.96.200.30:11434
  
  - model_name: codellama-13b
    litellm_params:
      model: ollama/codellama
      api_base: http://10.96.200.30:11434
  
  - model_name: mistral-7b
    litellm_params:
      model: vllm/mistral-7b-instruct
      api_base: http://10.96.200.31:8000
```

### Rationale

- **Unified interface**: All services use OpenAI-compatible API regardless of backend (Ollama, vLLM, etc.)
- **Configuration-based routing**: Adding new models/providers requires only config changes, no code changes
- **Local-only**: No cloud dependencies, all providers run locally
- **Fallback support**: liteLLM supports fallback models if primary unavailable

### Implementation

**Client usage** (Python):
```python
import openai

openai.api_base = "http://localhost:8000"  # liteLLM proxy
openai.api_key = "dummy"  # Not used for local

response = openai.Completion.create(
    model="llama2-7b",
    prompt="Hello, how are you?"
)
```

**Deployment**:
- liteLLM runs as systemd service on agent-lxc or dedicated llm-gateway-lxc container
- Configuration file mounted from Ansible-managed location
- Health check endpoint: `GET /health` returns available models

**Model discovery**:
```bash
curl http://localhost:8000/models
```
Returns list of available models with status.

### Alternatives Considered

- **Direct provider APIs**: Requires code changes for each provider, tight coupling
- **Custom gateway**: Reinventing the wheel, liteLLM is battle-tested
- **Cloud APIs (OpenAI, Anthropic)**: Violates on-premises requirement

---

## 5. Webhook Event Handling

### Decision

MinIO bucket notifications trigger webhook to agent API, which enqueues ingestion jobs in Redis Streams.

**Flow**:
1. File uploaded to MinIO bucket
2. MinIO sends webhook POST to `http://agent-api:3001/webhooks/minio`
3. Agent API validates event, creates IngestionJob, pushes to Redis Stream `jobs:ingestion`
4. Ingest worker consumes from stream, processes file

**MinIO Configuration** (via Ansible):
```bash
mc admin config set myminio notify_webhook:1 \
  endpoint="http://10.96.200.24:3001/webhooks/minio" \
  queue_limit="100" \
  queue_dir="/tmp/minio-events"

mc event add myminio/documents arn:minio:sqs::1:webhook --event put
```

### Rationale

- **Asynchronous processing**: File upload completes immediately, processing happens in background
- **Reliability**: MinIO queues events to disk if webhook endpoint unavailable
- **Decoupling**: Agent API doesn't process files, just enqueues for worker
- **Idempotency**: Webhook endpoint checks if file already queued before creating duplicate job

### Implementation

**Webhook endpoint** (FastAPI):
```python
@app.post("/webhooks/minio")
async def minio_webhook(event: MinIOEvent):
    file_key = event.Records[0].s3.object.key
    bucket = event.Records[0].s3.bucket.name
    
    # Check if already queued
    existing = await db.query("SELECT id FROM files WHERE object_key = ?", file_key)
    if existing:
        return {"status": "already_queued"}
    
    # Create file record and job
    file_id = uuid.uuid4()
    await db.execute("INSERT INTO files (...) VALUES (...)", file_id, file_key, ...)
    
    # Enqueue in Redis
    await redis.xadd("jobs:ingestion", {"file_id": str(file_id), "bucket": bucket, "key": file_key})
    
    return {"status": "queued", "file_id": str(file_id)}
```

**Error handling**:
- Webhook returns 200 even if job already exists (idempotent)
- Returns 500 on errors, MinIO retries with exponential backoff
- Failed events logged for manual investigation

### Alternatives Considered

- **Polling**: Inefficient, adds latency, wastes resources
- **Direct worker invocation**: Tight coupling, no queue buffering for load spikes
- **SNS/SQS-style queue**: Overkill for current scale, Redis Streams sufficient

---

## 6. Text Extraction Libraries

### Decision

Use **multi-library approach** with fallback chain based on file type.

**PDF**:
1. Primary: `pdfplumber` (best table/layout handling)
2. Fallback: `PyPDF2` (faster, simpler)

**DOCX**:
1. Primary: `python-docx` (standard library)

**TXT/MD**:
1. Direct file read (no library needed)

### Rationale

- **pdfplumber**: Superior text extraction quality, handles tables and multi-column layouts well
- **PyPDF2**: Simpler, faster for basic PDFs; fallback if pdfplumber fails
- **python-docx**: Industry standard for DOCX, well-maintained
- **Fallback strategy**: Increases reliability—if primary extraction fails, try fallback before marking job as failed

### Implementation

```python
def extract_text(file_path: str, content_type: str) -> str:
    if content_type == "application/pdf":
        try:
            return extract_pdf_pdfplumber(file_path)
        except Exception as e:
            logger.warning("pdfplumber failed, trying PyPDF2", error=str(e))
            return extract_pdf_pypdf2(file_path)
    
    elif content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return extract_docx(file_path)
    
    elif content_type.startswith("text/"):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    
    else:
        raise UnsupportedFileTypeError(content_type)
```

**Dependencies** (`requirements.txt`):
```
pdfplumber==0.10.3
PyPDF2==3.0.1
python-docx==1.1.0
```

### Alternatives Considered

- **textract**: Swiss-army-knife library but heavyweight, requires system dependencies (anticonv, etc.)
- **Apache Tika**: Java-based, adds JVM dependency, overkill for current needs
- **OCR (Tesseract)**: Not needed initially—assume text-based PDFs, add OCR later if needed for scanned documents

---

## 7. Chunking Strategy

### Decision

Use **hybrid chunking** approach:
- **Base unit**: Sentences (using spaCy or NLTK sentence tokenizer)
- **Target chunk size**: 512 tokens (~400 words)
- **Overlap**: 50 tokens (~40 words)
- **Boundary preservation**: Don't split sentences across chunks

**Algorithm**:
1. Split document into sentences
2. Group sentences until reaching ~512 tokens
3. Create chunk with last 50 tokens overlapping into next chunk
4. Respect paragraph boundaries where possible (don't merge across paragraphs if near target size)

### Rationale

- **512 tokens**: Good balance between context (enough for semantic meaning) and embedding efficiency (most models support 512+ token contexts)
- **Sentence-based**: Preserves semantic coherence better than arbitrary token splits
- **Overlap**: Ensures boundary context isn't lost, improves retrieval of concepts spanning chunk boundaries
- **Paragraph awareness**: Topical coherence—paragraphs usually contain single ideas

### Implementation

```python
import spacy

nlp = spacy.load("en_core_web_sm")

def chunk_text(text: str, max_tokens: int = 512, overlap_tokens: int = 50) -> List[str]:
    doc = nlp(text)
    sentences = [sent.text for sent in doc.sents]
    
    chunks = []
    current_chunk = []
    current_tokens = 0
    
    for sent in sentences:
        sent_tokens = len(sent.split())  # Rough token count
        
        if current_tokens + sent_tokens > max_tokens:
            # Save current chunk
            chunks.append(" ".join(current_chunk))
            
            # Start new chunk with overlap (last N tokens from previous)
            overlap_text = " ".join(current_chunk[-2:])  # Last ~2 sentences for overlap
            current_chunk = [overlap_text, sent]
            current_tokens = len(overlap_text.split()) + sent_tokens
        else:
            current_chunk.append(sent)
            current_tokens += sent_tokens
    
    # Add final chunk
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    
    return chunks
```

**Configuration** (environment variables):
```
CHUNK_MAX_TOKENS=512
CHUNK_OVERLAP_TOKENS=50
```

### Alternatives Considered

- **Fixed token split**: Breaks semantic units, poor retrieval quality
- **Paragraph-only**: Highly variable chunk sizes (some paragraphs are 3000+ words), inefficient embeddings
- **LangChain text splitters**: Good library but adds dependency, manual approach sufficient for current needs
- **Recursive character splitting**: More complex, no clear benefit over sentence-based for current use case

---

## 8. Ansible Testing & Verification

### Decision

Implement `make verify` target that runs **health checks and smoke tests** after provisioning.

**Makefile** (`provision/ansible/Makefile`):
```makefile
.PHONY: all verify

all:
	ansible-playbook -i inventory/hosts.yml site.yml

verify:
	@echo "==> Verifying infrastructure health..."
	@ansible-playbook -i inventory/hosts.yml playbooks/verify.yml

verify-quick:
	@echo "==> Quick health check..."
	@./scripts/health-check.sh
```

**Verification playbook** (`playbooks/verify.yml`):
```yaml
---
- name: Verify busibox infrastructure
  hosts: all
  gather_facts: no
  tasks:
    - name: Check service health endpoints
      uri:
        url: "http://{{ inventory_hostname }}:{{ health_port }}/health"
        status_code: 200
      register: health_check
      retries: 3
      delay: 5
    
    - name: Check systemd service status
      systemd:
        name: "{{ service_name }}"
      register: service_status
      failed_when: service_status.status.ActiveState != "active"

- name: Verify PostgreSQL
  hosts: pg
  tasks:
    - name: Test PostgreSQL connection
      postgresql_query:
        db: busibox
        query: "SELECT 1"
    
    - name: Verify schema_migrations table exists
      postgresql_query:
        db: busibox
        query: "SELECT * FROM schema_migrations"

- name: Verify MinIO
  hosts: files
  tasks:
    - name: Check MinIO buckets
      shell: mc ls myminio/documents
      register: minio_buckets
    
    - name: Verify webhook configuration
      shell: mc admin config get myminio notify_webhook:1
      register: webhook_config

- name: Verify Milvus
  hosts: milvus
  tasks:
    - name: Test Milvus connection
      shell: |
        python3 -c "
        from pymilvus import connections
        connections.connect('default', host='localhost', port='19530')
        print('Connected')
        "
```

**Quick health check script** (`scripts/health-check.sh`):
```bash
#!/bin/bash
set -e

SERVICES=(
    "10.96.200.21:9000:minio"
    "10.96.200.22:5432:postgres"
    "10.96.200.23:19530:milvus"
    "10.96.200.24:3001:agent-api"
    "10.96.200.25:3002:ingest-worker"
)

echo "Checking service health..."
for service in "${SERVICES[@]}"; do
    IFS=: read -r ip port name <<< "$service"
    if curl -sf "http://$ip:$port/health" >/dev/null 2>&1; then
        echo "✓ $name ($ip:$port) is healthy"
    else
        echo "✗ $name ($ip:$port) is UNHEALTHY"
        exit 1
    fi
done

echo "All services are healthy!"
```

### Rationale

- **Two-tier testing**: Full Ansible verification for thorough checks, quick bash script for rapid validation
- **Health endpoint reuse**: Leverages standard `/health` endpoints from Research Item 3
- **Integration tests**: Verify inter-service communication (not just individual service health)
- **CI/CD ready**: Scripts can be integrated into GitHub Actions or deploywatch

### Implementation

**Usage**:
```bash
# After initial provisioning
make all
make verify

# Quick check during development
make verify-quick
```

**Exit codes**:
- 0: All checks passed
- 1: One or more checks failed

**CI integration** (future):
```yaml
# .github/workflows/deploy.yml
- name: Provision infrastructure
  run: make all
- name: Verify deployment
  run: make verify
```

### Alternatives Considered

- **Molecule**: Ansible testing framework, but heavyweight for infrastructure validation (designed for role development, not deployment verification)
- **serverspec**: Ruby-based, adds language dependency
- **Inspec**: Chef ecosystem, not aligned with current stack
- **Manual testing**: Not repeatable, error-prone, doesn't scale

---

## Summary of Decisions

| Research Area | Decision | Primary Tool/Pattern |
|---------------|----------|---------------------|
| Logging | JSON structured logs | structlog (Python), winston (Node) |
| DB Migrations | Versioned SQL scripts | Manual scripts + Ansible execution |
| Health Checks | Standard `/health` endpoint | FastAPI route with dependency checks |
| LLM Gateway | Configuration-driven routing | liteLLM proxy |
| Webhooks | MinIO → Agent API → Redis | MinIO bucket notifications + Redis Streams |
| Text Extraction | Multi-library with fallbacks | pdfplumber + PyPDF2 + python-docx |
| Chunking | Sentence-based hybrid (512 tokens, 50 overlap) | spaCy sentence tokenizer |
| Testing | Health checks + smoke tests | Makefile targets + Ansible playbooks |

All decisions align with Constitution principles:
- ✅ Infrastructure as Code (versioned migrations, Ansible-managed config)
- ✅ Simplicity & Pragmatism (standard tools, no overengineering)
- ✅ Observability (structured logging, health endpoints)
- ✅ Test-Driven Infrastructure (automated verification)

**Next Phase**: Generate data-model.md, contracts/, and quickstart.md (Phase 1).

