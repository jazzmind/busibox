---
title: "Documentation-Code Inconsistencies"
category: "developer"
order: 200
description: "Tracking document listing inconsistencies between documentation and actual codebase"
published: true
---

# Documentation-Code Inconsistencies

**Created**: 2026-02-14  
**Updated**: 2026-02-14  
**Status**: Resolved (architecture docs); administrator/user docs may need follow-up  
**Purpose**: Track inconsistencies found during documentation audit so we can decide whether to fix the code or the docs in each case.

## Doc Structure (2026-02-14)

- **Architecture**: `docs/developers/architecture/` — 00-overview through 09-databases
- **Services**: `docs/developers/services/{agents,authz,data,search}/` — 01-overview, 02-architecture, 03-api, 04-testing per service
- **Administrators**: `docs/administrators/` — 01–08 numbered guides
- **Users**: `docs/users/` — 01–08 numbered guides
- **Archive**: `docs/archive/` — old structure and superseded docs
- **Reference**: `docs/developers/reference/` — cross-cutting reference docs (2026-02-14: reviewed, links fixed, cross-linked from numbered docs)

## Legend

- **Fix doc**: The code is correct; update the documentation
- **Fix code**: The documentation describes intended behavior; update the code
- **Verify**: Needs manual verification or decision

---

## Architecture Docs — Resolved (2026-02-14)

The following were fixed in the architecture docs:

| Doc | Fix Applied |
|-----|-------------|
| 00-overview | DB names: `agent`, `authz`, `data`; test DBs: `test_agent`, `test_authz`, `test_data` |
| 02-ai | ColPali default: `http://colpali:9006/v1` |
| 04-ingestion | Redis stream `jobs:data`; Data API has `POST /files/{fileId}/search` (not `/search`); link to services/data |
| 05-search | Link to services/search |
| 06-agents | API paths: `/chat/message`, `/agents`, `/conversations`, `/runs`, `/agents/tools` (no `/api` prefix); DB `agent` |
| 07-apps | busibox-app: `createZeroTrustClient`, `uploadChatAttachment`, `agentChat` |
| 08-tests | Container IPs: agent 10.96.201.202, search 10.96.201.204; test DB names; bootstrap link → services/authz/04-testing |
| 09-databases | DB names `agent`, `data`; migration script at `scripts/migrations/migrate_to_separate_databases.py` |

---

## Administrator Docs — May Need Updates

These paths reference docs that may have moved. Verify against current structure:

| Doc | Section | Issue | Recommendation |
|-----|---------|-------|----------------|
| 00-setup | Related Docs | `guides/01-configuration.md` → `01-configuration.md` (same dir) | Fix doc |
| 00-setup | Service code | `srv/ingest` → `srv/data` | Fix doc |
| 01-configuration | Ingestion | `srv/ingest` → `srv/data` | Fix doc |
| 01-configuration | Apps env | Data API port 8002 | Fix doc |
| 02-deployment | Test inventory | `INV=inventory/test` → `INV=inventory/staging` | Fix doc |
| 02-deployment | Make target | `make ingest` → `make data` | Fix doc |
| 02-deployment | Health check | Data API port 8002 | Fix doc |
| runtime-deployment | Related docs | Paths may not exist | Fix doc |
| runtime-deployment | Docker commands | Use `make` per project rules | Fix doc |

---

## User Docs — May Need Updates

| Doc | Section | Issue | Recommendation |
|-----|---------|-------|----------------|
| 05-usage | Upload/Status | Data API port 8002 | Fix doc |
| 05-usage | Agent chat | `POST /chat/message` (not `/api/chat`) | Fix doc |
| 05-usage | Token exchange | `exchangeTokenZeroTrust` | Fix doc |
| 05-usage | Audience | `data-api` (not `ingest-api`) | Fix doc |
| 10-platform-overview | Getting Started | Link to `../administrators/01-configuration.md` | Fix doc |
| 11-ai-models | Configuration | `AGENT_SERVER_DEFAULT_MODEL` — verify in agent settings | Verify |
| 15-agent-tools | Web Crawler | No web crawler tool in agent codebase | Fix doc (remove or mark planned) |
| 15-agent-tools | Agent API | Paths: `/chat/message`, `/agents`, `/conversations` | Fix doc |
| 15-agent-tools | Example | Agents use UUIDs | Fix doc |
| 16-app-development | Service Clients | `createAgentClient`, `uploadChatAttachment`, `generateEmbedding` | Fix doc |
| 16-app-development | Auth helpers | Use `createZeroTrustClient` | Fix doc |

---

## Common Patterns

1. **`ingest-api` → `data-api`**: Service renamed. Use `data-api`, `srv/data`, port 8002.
2. **`test` inventory → `staging`**: Use `INV=inventory/staging`.
3. **Agent API paths**: No `/api/` prefix. Use `/agents`, `/conversations`, `/chat/message`, `/runs`, `/agents/tools`.
4. **busibox-app**: Use `createZeroTrustClient`, `exchangeTokenZeroTrust`, `uploadChatAttachment`, `agentChat` — not `useAuthzTokenManager`, `IngestClient`, `AgentClient`.
5. **DB names**: `agent`, `authz`, `data` (not `agent_server`, `files`). Test: `test_agent`, `test_authz`, `test_data`.
