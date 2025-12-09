# Ingestion Service

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Architecture  
**Related Docs**:  
- `architecture/01-containers.md`  
- `architecture/02-ai.md`  
- `architecture/03-authentication.md`  
- `architecture/05-search.md`

## Service Placement
- **Container**: `ingest-lxc` (CT 206)
- **Code**: `srv/ingest`
- **Ports**: `8000` (FastAPI), `6379` (Redis Streams)
- **Exposure**: Internal-only; apps proxy requests.

## Key Endpoints (prefix `/`)
- `POST /upload` — multipart upload; streams to MinIO; dedupe via SHA-256; supports metadata, processing config, visibility `personal|shared`, role list.
- `GET /status/{fileId}` — SSE status stream per file.
- `GET /files/{fileId}` — metadata lookup; markdown retrieval under `/files/{fileId}/markdown`.
- `DELETE /files/{fileId}` — delete file (enforces role permissions).
- `POST /files/{fileId}/roles` — share/unshare with roles.
- `POST /search` — hybrid search (semantic + BM25) using ingest-held embeddings.
- `POST /api/embeddings` — text embedding endpoint (FastEmbed), used by search when configured.
- `POST /authz/*` — token/audit passthrough helpers.
- `GET /health` — health checks.

## Auth & RLS
- JWT middleware (`Authorization: Bearer`) required; legacy `X-User-Id` supported when enabled.
- Sets PostgreSQL session vars per request: `app.user_id`, `app.user_role_ids_*`.
- Upload to shared roles requires `create` permission on each role (`role_ids_create`).

## Pipeline
1. **Upload**: MinIO store at `userId/fileId/filename`; dedupe via content hash.
2. **Metadata**: PostgreSQL record with visibility + role IDs.
3. **Queue**: Redis Streams entry (`jobs:ingestion`).
4. **Processing worker** (`srv/ingest/src/worker.py`):
   - Extraction: Marker (GPU) with remote override; fallbacks (pdfplumber, etc.).
   - Classification and metadata enrichment.
   - Chunking: configurable 400–800 tokens, ~12% overlap.
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
- Chunking: `CHUNK_SIZE_MIN`, `CHUNK_SIZE_MAX`, `CHUNK_OVERLAP_PCT`
- Auth: `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE`, `ALLOW_LEGACY_AUTH`

## Notes vs Prior Docs
- Upload, webhook, and status logic previously described for agent-lxc now lives here.
- SSE status streaming is internal-only and proxied by apps; no public ingest endpoints.
- Video/image uploads are stored but not processed; return `status=completed` without queuing.
