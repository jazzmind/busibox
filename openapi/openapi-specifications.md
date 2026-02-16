# OpenAPI Specifications

**Category**: reference  
**Created**: 2025-12-12  
**Updated**: 2025-12-15  
**Status**: active

## Overview

This document provides an index of OpenAPI specifications for Busibox APIs and explains how to use them.

## Available Specifications

### AuthZ API
- **File**: `openapi/authz-api.yaml`
- **Service**: Authorization Service (authz-lxc)
- **Port**: 8010
- **Implementation**: `srv/authz/src/`
- **Description**: OAuth2-compliant authorization and authentication service with RBAC management

**Key Features**:
- OAuth2 token issuance (client_credentials and token-exchange grants)
- JWKS endpoint for token validation
- RBAC management (roles, users, permissions)
- OAuth client registry
- Audit logging
- User synchronization for first-party services

**Endpoints**:
- `/.well-known/jwks.json` - Public keys for JWT validation
- `/oauth/token` - OAuth2 token endpoint
- `/admin/roles` - Role management
- `/admin/user-roles` - User-role bindings
- `/admin/oauth-clients` - OAuth client management
- `/admin/users/{user_id}/roles` - User role queries
- `/internal/sync/user` - User sync from busibox-portal
- `/authz/audit` - Audit logging
- `/health` - Health checks

### Agent API
- **File**: `openapi/agent-api.yaml`
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
- **File**: `openapi/ingest-api.yaml`
- **Service**: Ingestion Service (ingest-lxc)
- **Port**: 8002
- **Implementation**: `srv/data/src/api/`
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
- **File**: `openapi/search-api.yaml`
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

**AuthZ API**:
```bash
# Swagger UI
http://authz-lxc:8010/docs

# ReDoc
http://authz-lxc:8010/redoc

# OpenAPI JSON
http://authz-lxc:8010/openapi.json
```

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
  -i openapi/agent-api.yaml \
  -g typescript-axios \
  -o clients/agent-api-ts

# Generate Python client for Ingest API
openapi-generator-cli generate \
  -i openapi/ingest-api.yaml \
  -g python \
  -o clients/ingest-api-python

# Generate Go client for Search API
openapi-generator-cli generate \
  -i openapi/search-api.yaml \
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
swagger-cli validate openapi/authz-api.yaml
swagger-cli validate openapi/agent-api.yaml
swagger-cli validate openapi/ingest-api.yaml
swagger-cli validate openapi/search-api.yaml
```

## Authentication

### OAuth2 Token Exchange

Busibox uses OAuth2 token exchange (RFC 8693) for service authentication:

**1. Get service token from AuthZ**:
```bash
curl -X POST http://authz-lxc:8010/oauth/token \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
    "client_id": "ai-portal",
    "client_secret": "your-secret",
    "audience": "ingest-api",
    "requested_subject": "user-uuid",
    "scope": "ingest.write"
  }'
```

**2. Use token in service requests**:
```bash
TOKEN="eyJhbGc..."

curl -H "Authorization: Bearer $TOKEN" \
  http://ingest-lxc:8002/upload \
  -F "file=@document.pdf"
```

**3. Legacy X-User-Id header** (deprecated, fallback only):
```bash
curl -H "X-User-Id: user-uuid" \
  http://ingest-lxc:8002/upload
```

### JWT Token Structure

JWT tokens issued by AuthZ contain:
- **Standard claims**: `iss`, `sub`, `aud`, `exp`, `iat`, `nbf`, `jti`
- **OAuth2 scopes**: `scope` (space-separated string)
- **RBAC roles**: `roles` array with `{id, name, permissions: [read|create|update|delete]}`
- **Optional IdP metadata**: `idp: {provider, tenantId, objectId}`

This enables:
- Row-Level Security (RLS) in PostgreSQL
- Partition filtering in Milvus
- Fine-grained access control
- Audit trail with user context

### Token Validation

Services validate tokens using AuthZ's JWKS endpoint:

```bash
# Get public keys
curl http://authz-lxc:8010/.well-known/jwks.json

# Services cache JWKS for 5 minutes
# Tokens are validated locally without calling AuthZ
```

See `docs/guides/oauth2-token-exchange-implementation.md` for architecture details.

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

The OpenAPI YAML files in `openapi/` are maintained manually but reflect the actual implementation. To ensure they stay in sync:

1. **When adding endpoints**: Update the OpenAPI spec
2. **When changing schemas**: Update request/response models
3. **When modifying authentication**: Update security schemes
4. **Validate regularly**: Compare spec with `/openapi.json` endpoint

```bash
# Compare manual spec with generated spec
curl http://agent-lxc:8000/openapi.json > /tmp/agent-generated.json
# Then compare with openapi/agent-api.yaml
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
- **Ingest API Implementation**: `srv/data/README.md`
- **Search API Implementation**: `srv/search/README.md`
- **Testing**: `TESTING.md` - API testing procedures
- **Deployment**: `docs/deployment/` - Service deployment guides

## Changelog

### 2025-12-15
- Added AuthZ API specification (`authz-api.yaml`)
- Updated authentication section with OAuth2 token exchange details
- Added JWKS validation documentation
- Consolidated guide references

### 2025-12-12
- Created comprehensive OpenAPI specifications for Agent, Ingest, and Search APIs
- Documented authentication and authorization patterns
- Added usage examples and client generation instructions
- Verified specs match current implementations

