# OpenAPI Specifications

**Category**: reference  
**Created**: 2025-12-12  
**Updated**: 2025-12-12  
**Status**: active

## Overview

This document provides an index of OpenAPI specifications for Busibox APIs and explains how to use them.

## Available Specifications

### Agent API
- **File**: `docs/reference/agent-api-openapi.yaml`
- **Service**: Agent Server (agent-lxc)
- **Port**: 8000
- **Implementation**: `srv/agent/app/`
- **Description**: Production-grade agent server with Pydantic AI for executing AI agents with tool calls, dynamic agent/workflow management, and token forwarding

**Key Features**:
- Dynamic agent definition management with personal/built-in agents
- Tool and workflow CRUD operations
- Agent run execution with SSE streaming
- Intelligent query routing via dispatcher
- Performance evaluation with scorers
- Schedule management for cron-based runs

**Endpoints**:
- `/agents` - Agent management
- `/agents/tools` - Tool management
- `/agents/workflows` - Workflow management
- `/agents/evals` - Evaluator management
- `/runs` - Run execution and management
- `/streams/runs/{run_id}` - SSE streaming
- `/dispatcher/route` - Query routing
- `/scores` - Performance scoring
- `/health` - Health checks

### Ingest API
- **File**: `docs/reference/ingest-api-openapi.yaml`
- **Service**: Ingestion Service (ingest-lxc)
- **Port**: 8002
- **Implementation**: `srv/ingest/src/api/`
- **Description**: Document ingestion and processing API with role-based access control

**Key Features**:
- File upload with chunked streaming and SHA-256 hashing
- Real-time status via Server-Sent Events (SSE)
- Hybrid search (dense + sparse + ColPali)
- Content deduplication and vector reuse
- Role-based document sharing
- Multi-format export (markdown, HTML, PDF, DOCX)

**Pipeline Stages**:
1. Upload → MinIO storage
2. Parsing → Text extraction (Marker, TATR, OCR)
3. Classification → Document type and language detection
4. Chunking → Semantic chunking (400-800 tokens)
5. Embedding → Dense (bge-large) + BM25 + ColPali
6. Indexing → Milvus with partitioning

**Endpoints**:
- `/upload` - File upload with role assignment
- `/status/{fileId}` - SSE status streaming
- `/search` - Document search
- `/api/embeddings` - Embedding generation
- `/files/{fileId}` - File management (get, delete, download, chunks, reprocess, export)
- `/files/{fileId}/roles` - Role management
- `/extract` - Remote text extraction
- `/health` - Health checks

### Search API
- **File**: `docs/reference/search-api-openapi.yaml`
- **Service**: Search Service (search-lxc)
- **Port**: 8001
- **Implementation**: `srv/search/src/api/`
- **Description**: Sophisticated search API with multiple modes, reranking, and semantic alignment

**Key Features**:
- Keyword search (BM25)
- Semantic search (dense vectors)
- Hybrid search (RRF fusion) - recommended
- Cross-encoder reranking
- Search term highlighting
- Semantic alignment visualization
- MMR for diverse results
- Role-based partition filtering

**Endpoints**:
- `/search` - Main search endpoint (supports all modes)
- `/search/keyword` - Pure BM25 search
- `/search/semantic` - Pure vector search
- `/search/mmr` - Search with diversity
- `/search/explain` - Explain search results
- `/health` - Health checks

## Using the Specifications

### Viewing Documentation

Each service provides interactive API documentation:

**Agent API**:
```bash
# Swagger UI
http://agent-lxc:8000/docs

# ReDoc
http://agent-lxc:8000/redoc

# OpenAPI JSON
http://agent-lxc:8000/openapi.json
```

**Ingest API**:
```bash
# Swagger UI
http://ingest-lxc:8002/docs

# ReDoc
http://ingest-lxc:8002/redoc

# OpenAPI JSON
http://ingest-lxc:8002/openapi.json
```

**Search API**:
```bash
# Swagger UI
http://search-lxc:8001/docs

# ReDoc
http://search-lxc:8001/redoc

# OpenAPI JSON
http://search-lxc:8001/openapi.json
```

### Generating Client SDKs

Use OpenAPI Generator to create client libraries:

```bash
# Install OpenAPI Generator
npm install -g @openapitools/openapi-generator-cli

# Generate TypeScript client for Agent API
openapi-generator-cli generate \
  -i docs/reference/agent-api-openapi.yaml \
  -g typescript-axios \
  -o clients/agent-api-ts

# Generate Python client for Ingest API
openapi-generator-cli generate \
  -i docs/reference/ingest-api-openapi.yaml \
  -g python \
  -o clients/ingest-api-python

# Generate Go client for Search API
openapi-generator-cli generate \
  -i docs/reference/search-api-openapi.yaml \
  -g go \
  -o clients/search-api-go
```

### Importing into API Tools

**Postman**:
1. Open Postman
2. Click "Import"
3. Select OpenAPI file
4. Configure environment variables (base URL, auth tokens)

**Insomnia**:
1. Open Insomnia
2. Click "Create" → "Import From" → "File"
3. Select OpenAPI file
4. Configure environment

**Bruno**:
1. Open Bruno
2. Click "Import Collection"
3. Select OpenAPI file
4. Configure environment variables

### Validation

Validate OpenAPI specs:

```bash
# Install validator
npm install -g @apidevtools/swagger-cli

# Validate specs
swagger-cli validate docs/reference/agent-api-openapi.yaml
swagger-cli validate docs/reference/ingest-api-openapi.yaml
swagger-cli validate docs/reference/search-api-openapi.yaml
```

## Authentication

All APIs use JWT-based authentication:

```bash
# Get JWT token from AI Portal or auth service
TOKEN="your-jwt-token"

# Use in requests
curl -H "Authorization: Bearer $TOKEN" \
  http://agent-lxc:8000/agents

# Legacy X-User-Id header (fallback)
curl -H "X-User-Id: user-uuid" \
  http://ingest-lxc:8002/upload
```

JWT tokens contain:
- User identity (`sub` claim)
- Role memberships with CRUD permissions
- Token expiry

This enables:
- Row-Level Security (RLS) in PostgreSQL
- Partition filtering in Milvus
- Fine-grained access control

## Implementation Details

### FastAPI Integration

All APIs are built with FastAPI, which provides automatic OpenAPI generation:

```python
# Agent API
app = FastAPI(
    title="Busibox Agent Server API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Ingest API
app = FastAPI(
    title="Busibox Ingestion Service API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "Upload", "description": "File upload operations"},
        # ... more tags
    ],
)

# Search API
app = FastAPI(
    title="Busibox Search API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)
```

### Keeping Specs in Sync

The OpenAPI YAML files in `docs/reference/` are maintained manually but reflect the actual implementation. To ensure they stay in sync:

1. **When adding endpoints**: Update the OpenAPI spec
2. **When changing schemas**: Update request/response models
3. **When modifying authentication**: Update security schemes
4. **Validate regularly**: Compare spec with `/openapi.json` endpoint

```bash
# Compare manual spec with generated spec
curl http://agent-lxc:8000/openapi.json > /tmp/agent-generated.json
# Then compare with docs/reference/agent-api-openapi.yaml
```

## Common Patterns

### Pagination

All list endpoints support pagination:

```json
{
  "limit": 50,
  "offset": 0
}
```

### Filtering

Use query parameters or request body filters:

```json
{
  "filters": {
    "file_id": "uuid",
    "date_from": "2025-01-01",
    "date_to": "2025-12-31"
  }
}
```

### Error Handling

All APIs use consistent error responses:

```json
{
  "detail": "Error message",
  "hint": "Helpful hint for resolution"
}
```

HTTP status codes:
- `400` - Bad request (invalid parameters)
- `401` - Unauthorized (missing or invalid auth)
- `403` - Forbidden (insufficient permissions)
- `404` - Not found
- `409` - Conflict (resource in use)
- `500` - Internal server error
- `503` - Service unavailable (dependencies down)

## Related Documentation

- **Architecture**: `docs/architecture/architecture.md` - System design
- **Agent API Implementation**: `srv/agent/README.md`
- **Ingest API Implementation**: `srv/ingest/README.md`
- **Search API Implementation**: `srv/search/README.md`
- **Testing**: `TESTING.md` - API testing procedures
- **Deployment**: `docs/deployment/` - Service deployment guides

## Changelog

### 2025-12-12
- Created comprehensive OpenAPI specifications for all three APIs
- Documented authentication and authorization patterns
- Added usage examples and client generation instructions
- Verified specs match current implementations









