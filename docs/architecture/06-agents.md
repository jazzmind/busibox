# Agent Service

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Draft  
**Category**: Architecture  
**Related Docs**:  
- `architecture/00-overview.md`  
- `architecture/02-ai.md`  
- `architecture/05-search.md`

## Current State
- **Container**: `agent-lxc` (CT 202)
- **Code**: `srv/agent`
- **Port**: Intended `8001`
- **Exposure**: Internal-only
- **Implementation**: FastAPI skeleton with stub routes for auth/files/search/agent/webhooks; no production logic or integrations are wired.

## Intended Responsibilities
- Orchestrate agent-style requests (RAG):
  - Accept user prompt + context directives.
  - Call Search API for retrieval.
  - Call liteLLM for synthesis/chain execution.
  - Enforce RBAC using the same JWT/role model as ingest/search.
- Provide a stable surface for apps to invoke AI workflows without duplicating search/LLM calls.

## What to Rely On Today
- Do **not** rely on `srv/agent` for ingestion or search; those are provided by dedicated services.
- Use Search API for retrieval and liteLLM directly (or via planned agent endpoints) for generation until the agent service is completed.

## Next Steps (when implementing)
- Mirror JWT middleware behavior from ingest/search (HS256, audience-specific).
- Remove upload/webhook stubs; delegate ingestion to `ingest-lxc`.
- Add thin orchestration layer that composes:
  - Search API call (partitions derived from user roles).
  - liteLLM call for synthesis/rerank.
  - Response packaging with citations.
- Add observability consistent with other services (structlog, health endpoints).
