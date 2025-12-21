# Agent Service

**Created**: 2025-12-09  
**Last Updated**: 2025-12-20  
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
- End-user JWT: RS256 tokens from AuthZ service (`iss=busibox-authz`, `aud=agent-api`).
- Token validation via JWKS from AuthZ service (`AUTHZ_JWKS_URL`).
- Token exchange: Agent service exchanges user tokens for service-specific tokens (e.g., `search-api`, `ingest-api`) via AuthZ token-exchange grant to call downstream services on behalf of the user.
- Scopes from JWT are stored in token grants for downstream calls.
- **Note**: OAuth2 scope-based operation authorization (e.g., `agent.execute`) is designed but not yet enforced. See `architecture/03-authentication.md` for current status.

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
