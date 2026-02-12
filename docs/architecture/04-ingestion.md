---
title: "Data / Ingestion Service"
category: "developer"
order: 5
description: "Document upload, processing pipeline, embedding generation, and data storage architecture"
published: true
---

# Data / Ingestion Service

**Created**: 2025-12-09  
**Last Updated**: 2026-02-12  
**Status**: Active  
**Category**: Architecture  
**Related Docs**:  
- `architecture/01-containers.md`  
- `architecture/02-ai.md`  
- `architecture/03-authentication.md`  
- `architecture/05-search.md`

## Service Placement
- **Container**: `data-lxc` (CT 206)
- **Code**: `srv/data`
- **Ports**: `8002` (FastAPI Data API), `6379` (Redis Streams)
- **Additional services**: Embedding API (`8005`), Data Worker
- **Exposure**: Internal-only; apps proxy requests.

## Pipeline Overview

```mermaid
graph LR
    subgraph upload [Upload]
        API[Data API :8002]
    end

    subgraph store [Initial Storage]
        MinIO[(MinIO)]
        PG[(PostgreSQL)]
        Redis[(Redis Queue)]
    end

    subgraph worker [Data Worker]
        Extract[Text Extraction]
        Cleanup[LLM Cleanup]
        Chunk[Semantic Chunking]
        Embed[Embedding Generation]
    end

    subgraph index [Indexing]
        Milvus[(Milvus)]
    end

    API --> MinIO
    API --> PG
    API --> Redis
    Redis --> Extract
    Extract --> Cleanup
    Cleanup --> Chunk
    Chunk --> Embed
    Embed --> Milvus
    Embed --> PG
```

## Key Endpoints (prefix `/`)
- `POST /upload` -- multipart upload; streams to MinIO; dedupe via SHA-256; supports metadata, processing config, visibility `personal|shared`, role list.
- `GET /status/{fileId}` -- SSE status stream per file.
- `GET /files/{fileId}` -- metadata lookup; markdown retrieval under `/files/{fileId}/markdown`.
- `DELETE /files/{fileId}` -- delete file (enforces role permissions).
- `POST /files/{fileId}/roles` -- share/unshare with roles.
- `POST /search` -- hybrid search (semantic + BM25) using data-held embeddings.
- `POST /api/embeddings` -- text embedding endpoint (FastEmbed), used by search when configured.
- `POST /authz/*` -- token/audit passthrough helpers.
- `GET /health` -- health checks.

## Auth & RLS
- JWT middleware (`Authorization: Bearer`) validates RS256 tokens from AuthZ service.
- Validates audience claim is `ingest-api` and issuer is `busibox-authz`.
- Sets PostgreSQL session vars per request: `app.user_id`, `app.user_role_ids_*`.
- Role membership (from JWT `roles` claim) determines data access via RLS.
- **Note**: OAuth2 scope-based operation authorization (e.g., `ingest.write`) is designed but not yet enforced. See `architecture/03-authentication.md` for current status.

## Pipeline
1. **Upload**: MinIO store at `userId/fileId/filename`; dedupe via content hash.
2. **Metadata**: PostgreSQL record with visibility + role IDs.
3. **Queue**: Redis Streams entry (`jobs:ingestion`).
4. **Processing worker** (`srv/data/src/worker.py`):
   - **PDF Splitting**: Large PDFs (>5 pages) automatically split into 5-page chunks before processing to prevent memory issues and timeouts. Configurable via `PDF_SPLIT_PAGES` and `PDF_SPLIT_ENABLED`.
   - Extraction: Marker (GPU) with remote override; fallbacks (pdfplumber, etc.).
   - Classification and metadata enrichment.
   - Chunking: configurable 400-800 tokens, ~12% overlap.
   - Embeddings: FastEmbed (`bge-large-en-v1.5`); optional ColPali for visual.
   - Indexing: Milvus collection `documents`, partitions per user/role.
   - Status: Updates persisted for SSE/polling.
5. **Outputs**: Milvus vectors, PostgreSQL metadata/status, MinIO originals + markdown.

## Visibility Model
- **Personal**: Indexed into `personal_{userId}` partition.
- **Shared**: Indexed into `role_{roleId}` partitions for each supplied role; upload requires `create` on those roles.
- **Search compatibility**: Partition scheme consumed by Search API for filtering.

## Configuration Highlights (authoritative in `shared/config.py`)
- Redis: `REDIS_HOST`, `REDIS_PORT`, `REDIS_STREAM`, `REDIS_CONSUMER_GROUP`
- Postgres: `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- Milvus: `MILVUS_HOST`, `MILVUS_COLLECTION`
- MinIO: `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`
- Embeddings: `FASTEMBED_MODEL`, `EMBEDDING_BATCH_SIZE`
- Visual: `COLPALI_BASE_URL`, `COLPALI_ENABLED`
- Extraction: `MARKER_ENABLED`, `MARKER_SERVICE_URL`, `MARKER_USE_GPU`
- **PDF Splitting**: `PDF_SPLIT_ENABLED` (default: true), `PDF_SPLIT_PAGES` (default: 5)
- Chunking: `CHUNK_SIZE_MIN`, `CHUNK_SIZE_MAX`, `CHUNK_OVERLAP_PCT`
- Auth: `AUTHZ_JWKS_URL`, `JWT_ISSUER`, `JWT_AUDIENCE`, `ALLOW_LEGACY_AUTH`

## Notes vs Prior Docs
- Service code is in `srv/data` (formerly `srv/ingest`). Container is `data-lxc` (formerly `ingest-lxc`). Port is `8002` (formerly `8000`).
- Upload, webhook, and status logic previously described for agent-lxc now lives here.
- SSE status streaming is internal-only and proxied by apps; no public ingest endpoints.
- Video/image uploads are stored but not processed; return `status=completed` without queuing.
- Embedding API runs as a separate service on the same container (port `8005`).