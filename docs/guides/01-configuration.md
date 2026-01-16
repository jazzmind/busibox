---
title: "Configuration"
category: "developer"
order: 4
description: "Environment variables, secrets management, and service configuration"
published: true
---

# Configuration Guide

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Guide  
**Related Docs**:  
- `guides/00-setup.md`  
- `architecture/03-authentication.md`  
- `guides/configuration/local-development.md`  
- `guides/configuration/vault-secrets.md`

## Environment Sources
- **Container IPs/CTIDs**: `provision/pct/vars.env` (authoritative for endpoints).
- **Ansible inventory**: `provision/ansible/inventory/*/hosts.yml` plus group vars.
- **Service env files**: templates under `provision/ansible/roles/*/templates/` and `.env.example` files within each service repo (apps live in separate repos).

## Core Variables by Service
- **Ingestion (`srv/ingest`)**
  - `POSTGRES_HOST`, `POSTGRES_DB=files`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
  - `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`
  - `MILVUS_HOST`, `MILVUS_COLLECTION`
  - `JWT_SECRET`, `JWT_ISSUER=ai-portal`, `JWT_AUDIENCE=ingest-api`
  - `LITELLM_BASE_URL` (cleanup/embeddings), `FASTEMBED_MODEL`, `COLPALI_BASE_URL`
- **Search (`srv/search`)**
  - `MILVUS_HOST`, `MILVUS_COLLECTION`
  - `POSTGRES_HOST`, `POSTGRES_DB=busibox`, creds
  - `LITELLM_BASE_URL`, `RERANKER_MODEL`, `ENABLE_RERANKING`
  - `JWT_SECRET`, `JWT_AUDIENCE=search-api`
- **AuthZ (`srv/authz`)**
  - `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE`
  - `AUTHZ_TOKEN_TTL`, PostgreSQL connection for audit
- **Apps (AI Portal/Agent Client)**
  - `NEXT_PUBLIC_INGEST_API_URL=http://10.96.200.206:8000`
  - `NEXT_PUBLIC_SEARCH_API_URL=http://10.96.200.204:8003`
  - LiteLLM, AuthZ, and OAuth settings per app repo.

## Secrets Handling
- Use Ansible Vault for production secrets (`provision/ansible/group_vars/*/vault.yml`).
- Do **not** commit raw secrets; keep `.env` files out of git.
- MinIO and database credentials must match what Ansible provisions into containers.

## Auth & Roles
- JWTs carry roles with CRUD permissions; align app-issued tokens with ingest/search expectations.
- Disable `ALLOW_LEGACY_AUTH` in production to require JWTs only.

## Validation Checklist
- MinIO console reachable at `http://10.96.200.205:9001`.
- PostgreSQL accessible from ingest/search containers.
- Milvus reachable from ingest and search containers.
- Apps can mint AuthZ tokens and call ingest/search with JWTs attached.
