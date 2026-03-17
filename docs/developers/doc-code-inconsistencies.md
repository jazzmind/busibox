---
title: "Documentation-Code Inconsistencies"
category: "developer"
order: 200
description: "Tracking document listing inconsistencies between documentation and actual codebase"
published: true
---

# Documentation-Code Inconsistencies

**Created**: 2026-02-14  
**Updated**: 2026-03-16  
**Status**: Resolved — all sections reviewed and fixed  
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

## Administrator Docs — Resolved (2026-03-16)

The docs referenced below (`00-setup`, `01-configuration`, `02-deployment`, `runtime-deployment`) no longer exist. Administrator docs were reorganized into `01-quickstart` through `11-kubernetes` with correct service names, ports, and API paths. No references to `ingest-api`, `srv/ingest`, or `INV=inventory/test` remain.

---

## User Docs — Resolved (2026-03-16)

The docs referenced below (`05-usage`, `10-platform-overview`, `11-ai-models`, `15-agent-tools`, `16-app-development`) no longer exist. User docs were reorganized into `01-quickstart` through `08-troubleshooting` with correct API paths and service names. No references to `ingest-api`, `/api/chat`, or old busibox-app APIs remain.

Additional fix: `web-crawler` tool reference removed from `architecture/02-ai.md` (tool does not exist in agent codebase).

---

## Common Patterns

1. **`ingest-api` → `data-api`**: Service renamed. Use `data-api`, `srv/data`, port 8002.
2. **`test` inventory → `staging`**: Use `INV=inventory/staging`.
3. **Agent API paths**: No `/api/` prefix. Use `/agents`, `/conversations`, `/chat/message`, `/runs`, `/agents/tools`.
4. **busibox-app**: Use `createZeroTrustClient`, `exchangeTokenZeroTrust`, `uploadChatAttachment`, `agentChat` — not `useAuthzTokenManager`, `IngestClient`, `AgentClient`.
5. **DB names**: `agent`, `authz`, `data` (not `agent_server`, `files`). Test: `test_agent`, `test_authz`, `test_data`.
