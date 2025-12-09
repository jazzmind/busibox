# Agent Service

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Architecture  
**Related Docs**:  
- `architecture/00-overview.md`  
- `architecture/02-ai.md`  
- `architecture/05-search.md`

## Current State (agent-server, Mastra-based)
- **Container**: `agent-lxc` (CT 202)
- **Code**: `agent-server` (Mastra)
- **Port**: 4111 (internal)
- **Exposure**: Internal-only
- **Implementation**: Mastra agents + tools with a streaming `/api/chat` endpoint.

## Responsibilities
- Orchestrate agent-style requests (RAG + web + attachment decisions):
  - Accept user prompt, toggles (web/doc), attachments metadata.
  - Call Search API for retrieval (document-search tool).
  - Call liteLLM via OpenAI-compatible API for synthesis.
  - Enforce RBAC using the same JWT/role model as apps/search/ingest.
- Provide a stable surface for apps to invoke AI workflows without duplicating search/LLM calls.

## What to Rely On Today
- Use `agent-server /api/chat` for streaming chat with debug marker; legacy app-side logic remains as fallback.
- Retrieval is delegated to Search API via the `document-search` tool (grounded RAG).
- Web search tool is currently a placeholder (returns “not configured”) until a provider is added.

## Auth
- End-user JWT: HS256 (`SSO_JWT_SECRET`, `iss=ai-portal`, `aud=agent-server`).
- Tools: `document-search` forwards Authorization to Search API; if missing, can mint scoped token via AuthZ (`AUTHZ_API_URL`) using user roles/userId; falls back to `SEARCH_API_SERVICE_KEY` if set.
- Admin APIs (`/admin/*`) still use EdDSA public keys (`TOKEN_SERVICE_PUBLIC_KEY`) for client credential tokens.

## Built-in Agents (hardcoded, listed via `/admin/agents`)
- `rag-search-agent`: uses `document-search` tool; grounded answers with citations.
- `web-search-agent`: placeholder web search (provider not configured).
- `attachment-agent`: heuristic action/modelHint for attachments.
- `chat-agent`: final responder; uses provided doc/web/attachment context, avoids fabrication.
- (Legacy) `documentAgent`, `weatherAgent` remain available.

## Chat Endpoint
- **Path**: `POST /api/chat`
- **Behavior**: attachment decision → optional doc search → chat synthesis via liteLLM; streams tokens and prepends `<!-- ROUTING_DEBUG:... -->`.
- **Inputs**: `content`, `enableDocumentSearch`, `enableWebSearch`, `attachmentIds?`, `model?`, `conversationId?`
- **Outputs**: streaming text + routing debug; doc results included in debug payload for UI display.

## App Integration (high level)
- Apps should mint a user HS256 JWT and call `agent-server /api/chat`, streaming the response to the UI; keep legacy in-app routing as fallback until fully migrated.
