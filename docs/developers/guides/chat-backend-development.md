---
title: "Chat Backend Development"
category: "developer"
order: 25
description: "Current Agent API chat routes, orchestration, persistence, service dependencies, deployment seams, and multi-agent ownership."
published: true
---

# Chat Backend Development

This is the backend companion to the sibling `busibox-frontend/docs/developers/architecture/02-chat.md`. It describes registered code paths, not a deployment guarantee. Agent API routers are mounted without a global `/api` prefix.

## Frontend-to-backend path

The deployed Marine UI uses two access patterns:

1. The Chat Server Component exchanges `busibox-session` for an `agent-api` token and calls Agent API directly to preload agents, conversations, and optional history.
2. Browser CRUD and streaming call `/agents/api/agent/*`. The Agents Next.js catch-all proxy exchanges the session cookie and forwards to Agent API. The canonical send endpoint is `POST /chat/message/stream/agentic`.

Chat is HTTP plus Server-Sent Events. The chat send path is not a WebSocket. The chat-adjacent WebSocket is `/llm/transcribe/stream` for audio transcription.

## Primary routers

| Source | Prefix | Chat-relevant routes |
|---|---|---|
| `srv/agent/app/api/chat.py` | `/chat` | `POST /message`, `/message/stream`, `/message/stream/agentic`; `GET /models`, `/<id>/history`; insights and message delete |
| `srv/agent/app/api/conversations.py` | none | Conversation CRUD/messages/shares, chat-attachment CRUD, user chat settings |
| `srv/agent/app/api/agents.py` | `/agents` | Visible agents, models, definitions, tools, workflows, evals |
| `srv/agent/app/api/insights.py` | `/insights` | Init/extract/search/list/stats/update/delete |
| `srv/agent/app/api/dispatcher.py` | `/dispatcher` | Route and route-stream contracts |
| `srv/agent/app/api/runs.py` | `/runs` | Agent runs, invoke, async invoke, schedules, workflows |
| `srv/agent/app/api/streams.py` and `execution_streams.py` | `/streams` | Run/task/workflow execution streams |
| `srv/agent/app/api/llm.py` | `/llm` | Models, completions, health/load, keys, media, cloud models, purposes, transcription WebSocket |

Inspect decorators and `srv/agent/app/main.py` before adding or renaming a route. Older `/chat/api/*` compatibility handlers remain in `chat.py`; do not extend them unless a current caller requires it.

## Agentic request lifecycle

`POST /chat/message/stream/agentic`:

1. Resolves the authenticated `Principal` from an RS256/JWKS Bearer token with the `agent-api` audience.
2. Refreshes the administrator-controlled `chat_model_routing_mode` from Config API. The browser's `model` field cannot override it.
3. For library-scoped knowledge, exchanges a `data:read` token and asks Data API to validate the selected library and resolve its documents under RLS before starting the SSE response.
4. Loads the owned conversation or creates one, updates its title, and emits `conversation_created` or `title_update`.
5. Persists the user message, links requested `ChatAttachment` rows, and loads the most recent 20 prior messages.
6. Chooses `selected_agents` or falls back to the Chat agent, then calls `run_agentic_dispatcher`.
7. Streams dispatcher/agent/tool events while accumulating final content, thoughts, run events, and document-search citations.
8. Persists the assistant message. Thoughts, selected agents, deterministic citations, effective model policy, and knowledge scope are stored in `routing_decision`.
9. Creates a `RunRecord` when possible, triggers optional insights/online eval work, commits, and emits `message_complete`.

Important request fields are `message`, `conversation_id`, `selected_agents`, `attachments`, `attachment_ids`, `knowledge_scope`, `selected_library_ids`, search flags, generation settings, and `metadata`. The Marine UI still sends `model: "auto"` for compatibility, but model routing is server policy. It also sends the user's knowledge scope, an active Chat agent ID when found, attachment IDs, and browser timezone/locale metadata.

Model policies are:

- `local`: local fast/tool-planning aliases and local Chat synthesis; no frontier fallback.
- `auto`: local fast/tool-planning aliases and Chat synthesis, with the existing complexity/context frontier escalation enabled.
- `frontier`: frontier alias for acknowledgement, planning, progress synthesis, and final response; no silent local response path.

Knowledge scopes are:

- `all`: Search API applies normal user access and searches all accessible documents.
- `libraries`: one selected library is validated through Data API and only its server-resolved file IDs are passed to document search.
- `attachments`: only file IDs linked to the current request's chat attachments are searched.

An empty library or attachment scope produces a grounded no-documents result rather than falling back to a broader search.

## SSE contract

Shared event definitions are in `srv/agent/app/schemas/streaming.py`; `chat.py` also emits lifecycle events.

| Event | Persistence/consumer note |
|---|---|
| `conversation_created`, `title_update` | Update frontend URL/sidebar state |
| `thought`, `plan`, `progress` | Diagnostics/reasoning; bridge requests may suppress them |
| `tool_start`, `tool_result` | Tool timeline; document-search results feed citations |
| `content`, `content_chunk` | `fast_ack` and `interim` phases may be transient; final content is persisted |
| `interim` | Follow-up/status content, not the final stored answer |
| `clarify_parallel`, `prompt` | Frontend user-choice state |
| `complete`, `message_complete` | Execution/persistence completion |
| `error` | Stream failure |

When changing events, coordinate with `busibox-frontend/packages/app/src/lib/agent/stream-event-processor.ts` and keep field aliases/backward compatibility through rolling deployment.

## Orchestration and tools

- `services/agentic_dispatcher.py` resolves built-in or database agent definitions, chooses direct or routed execution, and injects metadata/insights.
- `agents/chat_agent.py` handles fast acknowledgement, tool planning, parallel tool groups, progress/interim events, final synthesis, and optional frontier escalation.
- `agents/base_agent.py` builds context, manages tools/context compression, and calls LLMs through LiteLLM.
- `services/attachment_resolver.py` waits for uploaded content, uses available chunks/markdown/search, and emits progress.
- `tools/document_search_tool.py` calls Search API and returns relevance-gated citation candidates.

The standard non-agentic stream still uses `services/chat_executor.py` and `services/dispatcher_service.py`. Trace which endpoint the frontend uses before editing the older path.

## Persistence and service dependencies

Conversation, Message, ConversationShare, ChatAttachment, and ChatSettings models live in `srv/agent/app/models/domain.py`; request/response models live in `schemas/conversation.py`. Conversation APIs perform owner/shared-role checks. Any attachment authorization change needs an explicit ownership review before broadening access.

Attachment/document flow crosses services:

1. Data API uploads to object storage, creates database/status rows, and queues ingestion.
2. Workers parse, chunk, embed, and index the file.
3. Chat stores attachment references and resolves content for the agent.
4. Document search calls Search API and returns citation metadata.
5. The frontend Documents app renders status, HTML, images, enhancement, and download routes.

## Configuration and deployment

- Agent settings: `srv/agent/app/config/settings.py`.
- Injected Agent API environment: `provision/ansible/roles/agent_api/templates/agent-api.env.j2`.
- Public platform flags: `srv/agent/app/services/platform_config.py` via Config API.
- Frontend app definition: `provision/ansible/group_vars/all/apps.yml` (`busibox-chat`, port 3003, `/chat`, Marine brand).
- Frontend deployment: `srv/deploy/src/core_app_executor.py` maps `busibox-chat` to `apps/chat` and currently fixes the repository to `jazzmind/busibox-frontend`.
- Selected frontend ref: `BUSIBOX_FRONTEND_GITHUB_REF`; runtime clone/update is in `provision/docker/core-apps-entrypoint-runtime.sh`.

A frontend Git ref setting does not change the hard-coded repository owner. Record both backend and frontend refs in deployment proof.

## Multi-agent ownership

| Owner | Files/services |
|---|---|
| API contract/persistence | `chat.py`, `conversations.py`, schemas, domain models, migrations, conversation/chat tests |
| Orchestration | dispatcher, Chat/base agents, attachment resolver, tools, orchestration tests |
| LLM/models | `llm.py`, `model_selector.py`, load monitoring, LiteLLM/model config |
| Auth | agent auth dependencies/tokens/token service and `srv/authz` |
| RAG | `srv/data`, `srv/search`, `srv/embedding`, document-search tool |
| Deploy/config | `provision`, `srv/deploy`, inventories, frontend refs, health checks |
| Integrator | Cross-repo contract, combined diff, runtime verification, docs |

Only one owner should edit collision-prone route, model, domain, or environment-template files at a time. Preserve pre-existing dirty files and state their ownership in every handoff.

## Focused verification

```bash
make test-docker SERVICE=agent ARGS="tests/integration/test_agentic_stream_sequence.py tests/integration/test_api_conversations.py tests/unit/test_attachment_resolver.py tests/unit/test_model_selector.py"
```

Add `tests/integration/test_chat_flow.py`, attachment tests, dispatcher tests, or load tests according to the changed layer. Then verify through the authenticated Agents proxy and reload the persisted conversation. Use the Busibox CLI/MCP or documented `make` flow; never run raw Docker/Compose/Ansible commands.
