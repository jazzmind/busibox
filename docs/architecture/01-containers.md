---
title: "Container Architecture"
category: "developer"
order: 2
description: "LXC container topology, network layout, and service responsibilities"
published: true
---

# Container Topology

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Architecture  
**Related Docs**:  
- `architecture/00-overview.md`  
- `architecture/02-ai.md`  
- `architecture/03-authentication.md`  
- `architecture/04-ingestion.md`  
- `architecture/05-search.md`  
- `architecture/06-agents.md`  
- `architecture/07-apps.md`

## Network
- **Bridge**: `vmbr0`
- **CIDR**: `10.96.200.0/21`
- **Gateway**: `10.96.200.1`
- **Definition**: `provision/pct/vars.env` (authoritative CTIDs/IPs)

## Container Inventory

| Container | CTID | IP | Purpose | Key Ports | Notes |
| --- | --- | --- | --- | --- | --- |
| `proxy-lxc` | 200 | 10.96.200.200 | nginx reverse proxy | 80/443 | Fronts apps; terminates TLS in production. |
| `apps-lxc` | 201 | 10.96.200.201 | Next.js apps (AI Portal, Agent Client, etc.) | 3000 (internal), proxied via 80/443 on proxy | No direct access to ingest/search; proxies internal calls. |
| `agent-lxc` | 202 | 10.96.200.202 | Agent API skeleton | 8001 (intended) | Currently stub FastAPI; should call search + liteLLM rather than own ingest. |
| `pg-lxc` | 203 | 10.96.200.203 | PostgreSQL (files + authz/audit) | 5432 | RLS policies enforced; ingest/search/authz write here. |
| `milvus-lxc` | 204 | 10.96.200.204 | Milvus vector DB | 19530 | Stores document embeddings; partitioned by user/role. |
| `files-lxc` | 205 | 10.96.200.205 | MinIO object storage | 9000 (S3), 9001 (console) | Holds originals and derived artifacts. |
| `ingest-lxc` | 206 | 10.96.200.206 | Ingestion API + worker + Redis Streams | 8000 (API), 6379 (Redis) | Internal-only API for upload/status/search/embeddings. |
| `litellm-lxc` | 207 | 10.96.200.207 | liteLLM gateway | 4000 | Fronts vLLM/Ollama/remote providers; used by ingest + search. |
| `vllm-lxc` | 208 | 10.96.200.208 | vLLM inference | 8000 (default) | GPU-capable local model serving. |
| `ollama-lxc` | 209 | 10.96.200.209 | Ollama inference | 11434 | Local model serving option. |
| `authz-lxc` | 210 | 10.96.200.210 | AuthZ service | 8010 | Issues HS256 JWTs and records audit events. |

## Responsibilities & Traffic
- **North-south**: Users hit proxy → apps; no direct public access to ingest/search/authz.
- **East-west**:
  - Apps → Ingest API (`/upload`, `/status`, `/files`) for ingestion lifecycle.
  - Apps → Search API (`/search`) for hybrid retrieval.
  - Ingest worker → Milvus, MinIO, PostgreSQL, liteLLM.
  - Search → Milvus (+ optional embedding microservice on ingest) and PostgreSQL for metadata.
  - AuthZ → PostgreSQL for audit writes; apps call AuthZ to mint scoped tokens.

## Operational Sources of Truth
- **Provisioning**: `provision/pct/*.sh` + `vars.env`
- **Configuration**: `provision/ansible/roles/*` group vars and templates
- **Service code**: `srv/ingest`, `srv/search`, `srv/authz`, `srv/agent`

See individual component documents for API contracts and pipeline details.
