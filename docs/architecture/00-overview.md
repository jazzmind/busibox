---
title: "Architecture Overview"
category: "developer"
order: 1
description: "High-level system architecture and design principles of the Busibox platform"
published: true
---

# Busibox Architecture Overview

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Architecture  
**Related Docs**:  
- `architecture/01-containers.md`  
- `architecture/02-ai.md`  
- `architecture/03-authentication.md`  
- `architecture/04-ingestion.md`  
- `architecture/05-search.md`  
- `architecture/06-agents.md`  
- `architecture/07-apps.md`

## Overview
Busibox is a local-first LLM platform running on a single Proxmox host with isolated LXC containers for each service. The platform delivers secure document ingestion, hybrid search, and agent-style retrieval while enforcing role-based access control and Row-Level Security (RLS) in PostgreSQL.

## Core Principles
- **Isolation-first**: One major concern per container; shared nothing beyond the Proxmox bridge network.
- **RBAC everywhere**: JWTs carry role permissions; services translate them into RLS session variables.
- **Deterministic pipelines**: Ingestion writes to MinIO → PostgreSQL → Milvus; search reads from Milvus + Postgres partitions.
- **Infrastructure as code**: Containers defined in `provision/pct/vars.env`; configuration and deployment via Ansible.
- **Local LLM stack**: liteLLM gateway fronts local vLLM/Ollama and optional remote providers.

## End-to-End Flow (Happy Path)
1. **AuthZ token issued** by the `authz` service (CT 210) for a user and their document roles.  
2. **Upload** goes to the ingestion API (CT 206): file is stored in MinIO, metadata + visibility recorded in PostgreSQL, job queued in Redis.  
3. **Processing** worker (same CT 206) extracts text (Marker/TATR fallback), chunks, embeds (FastEmbed + optional ColPali visual), and indexes to Milvus; RLS metadata is kept in PostgreSQL.  
4. **Search** service (CT 204) receives JWT, builds allowed partitions (personal + role-based), performs hybrid search (Milvus vectors + BM25), optional rerank via liteLLM.  
5. **Apps** (CT 201) present UI (AI Portal, Agent Client) and proxy internal calls; they never expose ingest/search directly.  
6. **Agent API** (CT 202) is currently a thin stub; RAG aggregation is intended to layer over search + LLM gateway.

## Data Plane
- **Object Storage**: MinIO in `files-lxc` stores originals and derived artifacts.
- **Metadata & RLS**: PostgreSQL in `pg-lxc` holds file metadata, ingestion status, and role bindings.
- **Vectors**: Milvus in `milvus-lxc` stores embeddings; partitions align to users/roles.
- **Queue**: Redis Streams in `ingest-lxc` coordinates ingestion jobs.

## Control Plane
- **liteLLM gateway** in `litellm-lxc` normalizes LLM calls and reranker access.
- **AuthZ** in `authz-lxc` issues HS256 JWTs and records audit events in PostgreSQL.
- **Provisioning/Config**: Proxmox scripts in `provision/pct/`, Ansible roles in `provision/ansible/`.

## What Changed from Prior Docs
- Ingestion now lives in its own service (`srv/ingest`) with internal-only API and Redis queue; upload/webhook logic was removed from the agent API stubs.
- Search is a dedicated service (`srv/search`) with hybrid retrieval and partition-based authorization.
- AuthZ is a standalone service (CT 210) issuing scoped JWTs for ingest and search; services accept legacy `X-User-Id` only for migration.
- Agent API remains skeletal; RAG orchestration should call search + liteLLM rather than duplicate ingestion/search logic.

See the referenced component documents for detail on responsibilities, interfaces, and operational notes.
