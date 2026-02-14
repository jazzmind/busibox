# Architecture Documentation Validation Report

**Generated**: 2026-02-14  
**Scope**: docs/developers/architecture/00-overview.md through 05-search.md  
**Verified against**: provision/pct/vars.env, provision/ansible/inventory, docker-compose.yml, srv/* services

---

## File: docs/developers/architecture/00-overview.md

### Inconsistency 1
- **Line/Section**: Data Plane (lines 107-112)
- **Doc says**: PostgreSQL databases include `agent_server`, `authz`, `files`, `ai_portal` and test databases `test_agent_server`, `test_authz`, `test_files`
- **Code says**: init-databases.sql creates `agent`, `authz`, `data`, `ai_portal`, `busibox`, `litellm` and test databases `test_agent`, `test_authz`, `test_data`
- **Recommendation**: Fix doc: use `agent` (not `agent_server`), `data` (not `files`), and test names `test_agent`, `test_data` (not `test_agent_server`, `test_files`)

### Inconsistency 2
- **Line/Section**: Trust Boundaries diagram (lines 558-563)
- **Doc says**: `ingest-lxc :8020`, `search-lxc :8030`
- **Code says**: Data API runs on data-lxc:8002, Search API runs on milvus-lxc:8003. No ingest-lxc or search-lxc containers; no ports 8020 or 8030.
- **Recommendation**: Fix doc: use `data-lxc :8002`, `milvus-lxc :8003`

---

## File: docs/developers/architecture/01-containers.md

No inconsistencies found. Container names, CTIDs, IPs (10.96.200.x), and ports match vars.env and production inventory.

---

## File: docs/developers/architecture/02-ai.md

### Inconsistency 1
- **Line/Section**: Embeddings (line 78)
- **Doc says**: ColPali defaults to `COLPALI_BASE_URL` at vLLM host 10.96.200.208:9006
- **Code says**: srv/data/src/shared/config.py defaults to `COLPALI_BASE_URL` = `http://colpali:9006/v1` (not IP-based)
- **Recommendation**: Fix doc to match actual default or note config is environment-dependent

---

## File: docs/developers/architecture/03-authentication.md

### Inconsistency 1
- **Line/Section**: Configuration table (line 199)
- **Doc says**: `POSTGRES_DB` default is `busibox`
- **Code says**: authz config.py default is `busibox`, but docker-compose sets `POSTGRES_DB: authz` for authz-api. Production uses dedicated `authz` database.
- **Recommendation**: Fix doc: note that production/deployment typically sets `POSTGRES_DB=authz`; default `busibox` is for unconfigured/legacy cases

### Inconsistency 2
- **Line/Section**: Trust Boundaries diagram (lines 558-563)
- **Doc says**: `ingest-lxc :8020`, `search-lxc :8030`
- **Code says**: Data API on data-lxc:8002, Search API on milvus-lxc:8003. No ingest-lxc or search-lxc.
- **Recommendation**: Fix doc: use `data-lxc :8002`, `milvus-lxc :8003`

### Inconsistency 3
- **Line/Section**: Standard Service Accounts (lines 215-225)
- **Doc says**: `ingest-api` service account with `authz.keystore.*` scopes
- **Code says**: srv/data uses audience `data-api`; AUTHZ_BOOTSTRAP_ALLOWED_AUDIENCES in docker-compose includes `data-api` (not `ingest-api`)
- **Recommendation**: Fix doc: use `data-api` instead of `ingest-api` for the ingest/data service account

### Inconsistency 4
- **Line/Section**: AuthZ Keystore Endpoints (lines 548-556)
- **Doc says**: `/keystore/kek` | POST | Create KEK for role/user
- **Code says**: srv/authz keystore has `@router.post("/kek"` - full path is `/keystore/kek` (correct). But doc says `/keystore/kek/ensure-for-role/{role_id}` - code has `/kek/ensure-for-role/{role_id}` with keystore prefix. Paths match. Minor: doc lists `/keystore/kek` as POST but code also has GET `/kek/{owner_type}/{owner_id}` - doc doesn't list GET. Low priority.
- **Recommendation**: Optional: add GET keystore endpoint to doc if desired for completeness

---

## File: docs/developers/architecture/04-ingestion.md

### Inconsistency 1
- **Line/Section**: Pipeline (line 41)
- **Doc says**: Redis Streams entry (`jobs:ingestion`)
- **Code says**: srv/data uses `REDIS_STREAM` default `jobs:data`; docker-compose sets `REDIS_STREAM: jobs:data`
- **Recommendation**: Fix doc: use `jobs:data` (not `jobs:ingestion`)

### Inconsistency 2
- **Line/Section**: Auth & RLS (lines 80-81)
- **Doc says**: Validates audience claim is `ingest-api` and issuer is `busibox-authz`
- **Code says**: data-api uses `AUTHZ_AUDIENCE: data-api` (docker-compose line 386)
- **Recommendation**: Fix doc: use `data-api` as audience (not `ingest-api`)

### Inconsistency 3
- **Line/Section**: Key Endpoints (line 69)
- **Doc says**: `POST /search` -- hybrid search (semantic + BM25) using data-held embeddings
- **Code says**: Data API has no top-level `/search` endpoint. Search is in Search API (POST /search). Data API has `/files/{fileId}/search` for single-document search only.
- **Recommendation**: Fix doc: remove `POST /search` from Data API endpoints; document search is handled by Search API; Data API has `/files/{fileId}/search` for within-document search

---

## File: docs/developers/architecture/05-search.md

For Search doc, the config references are correct. The service_port 8003, POST /search, etc. match. One minor note:

### Inconsistency 1
- **Line/Section**: Configuration Highlights (line 93)
- **Doc says**: `SERVICE_PORT` (default 8003)
- **Code says**: srv/search uses `service_port: int = 8003` in config (pydantic BaseSettings). No env var `SERVICE_PORT` in search config - it's a class attribute. Docker uses `--port 8003` in uvicorn command.
- **Recommendation**: Low priority - doc is conceptually correct; config uses `service_port` not `SERVICE_PORT` env var

---

## Summary

| File | Inconsistencies | Severity |
|------|-----------------|----------|
| 00-overview.md | 2 | Medium |
| 01-containers.md | 0 | - |
| 02-ai.md | 1 | Low |
| 03-authentication.md | 4 | Medium |
| 04-ingestion.md | 3 | Medium |
| 05-search.md | 1 | Low |

**Key corrections needed:**
1. Replace `ingest-api` with `data-api` for audience and service names
2. Replace `jobs:ingestion` with `jobs:data` for Redis stream
3. Replace `agent_server`/`files` with `agent`/`data` for database names
4. Replace `test_agent_server`/`test_files` with `test_agent`/`test_data`
5. Fix Trust Boundaries diagram: use data-lxc:8002, milvus-lxc:8003 (not ingest-lxc:8020, search-lxc:8030)
6. Remove or correct Data API `POST /search` endpoint claim (belongs to Search API)
