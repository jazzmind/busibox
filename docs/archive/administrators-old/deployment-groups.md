---
title: "Deployment Groups and Order"
category: "administrator"
order: 23
description: "Service deployment groups and execution order for Busibox"
published: true
---

# Deployment Groups and Order

## Overview

Busibox services are organized into 4 deployment groups, deployed in a specific order to ensure dependencies are satisfied. The groups are designed so that:

1. **Sequential deployment** of groups produces the same result as a full deployment
2. **Nginx deploys first** to enable future web-driven installation
3. **Each group can be deployed independently** for easier recovery if deployment fails

## Deployment Order

```
┌─────────────────────────────────────────────────────────────────┐
│                        DEPLOYMENT ORDER                         │
├─────────────────────────────────────────────────────────────────┤
│  1. CORE      →  2. LLM        →  3. APIs      →  4. Apps      │
│  (nginx,          (vllm,           (authz,          (ai-portal, │
│   minio,           litellm,         ingest,          agent-mgr) │
│   postgres,        colpali)         search,                     │
│   milvus)                           agent,                      │
│                                     docs)                       │
└─────────────────────────────────────────────────────────────────┘
```

## Group Details

### Group 1: Core Services (tag: `core`)

Infrastructure services deployed first. **Nginx is first** to enable the future web-driven installation UI.

| Order | Service | Make Target | Description |
|-------|---------|-------------|-------------|
| 1 | Nginx | `make core-nginx` | Web proxy - enables install UI |
| 2 | MinIO | `make core-storage` | Object storage |
| 3 | PostgreSQL | `make core-database` | Relational database |
| 4 | Milvus | `make core-vectorstore` | Vector database |

```bash
# Deploy all core services
make core

# Deploy individually
make core-nginx       # First - enables web install UI
make core-storage     # MinIO
make core-database    # PostgreSQL
make core-vectorstore # Milvus
```

### Group 2: LLM Services (tag: `llm`)

GPU inference, embedding, and LLM gateway services.

| Order | Service | Make Target | Description |
|-------|---------|-------------|-------------|
| 1 | vLLM | `make llm-vllm` | GPU inference (ports 8000-8005) |
| 2 | ColPali | `make llm-colpali` | Visual embeddings |
| 3 | LiteLLM | `make llm-litellm` | LLM gateway/proxy |

```bash
# Deploy all LLM services
make llm

# Deploy individually
make llm-vllm     # GPU inference
make llm-colpali  # Visual embeddings
make llm-litellm  # LLM gateway
```

### Group 3: APIs (tag: `apis`)

Application services that depend on core infrastructure and LLM services. **AuthZ is first** since all other APIs require authentication.

| Order | Service | Make Target | Description |
|-------|---------|-------------|-------------|
| 1 | AuthZ | `make apis-authz` | Authentication - required by all APIs |
| 2 | Ingest | `make apis-ingest` | File ingestion + embedding |
| 3 | Search | `make apis-search` | Hybrid search API |
| 4 | Agent | `make apis-agent` | Agent orchestration |
| 5 | Docs | `make apis-docs` | Documentation API |
| 6 | Bridge | `make apis-bridge` | Multi-channel communication (optional) |

```bash
# Deploy all APIs
make apis

# Deploy individually
make apis-authz   # Authentication (first)
make apis-ingest  # File processing
make apis-search  # Search service
make apis-agent   # Agent service
make apis-docs    # Documentation
make apis-bridge  # Bridge (optional)
```

### Group 4: Apps (tag: `apps`)

Frontend applications that depend on all other services.

| Order | Service | Make Target | Description |
|-------|---------|-------------|-------------|
| 1 | AI Portal | `make deploy-ai-portal` | Main web portal |
| 2 | Agent Manager | `make deploy-agent-manager` | Agent management UI |

```bash
# Deploy all apps
make apps-frontend

# Deploy individually
make deploy-ai-portal
make deploy-agent-manager
```

## Full Deployment

### Option 1: Single Command

```bash
make all
```

### Option 2: Sequential Groups (equivalent to `make all`)

```bash
make core && make llm && make apis && make apps-frontend
```

This is useful for:
- Resuming from where a deployment failed
- Deploying only what changed
- Debugging deployment issues

## Tag Hierarchy

Every Ansible task has hierarchical tags:

| Level | Examples | Usage |
|-------|----------|-------|
| Group | `core`, `llm`, `apis`, `apps` | `--tags core` deploys all core services |
| Subgroup | `core_nginx`, `apis_authz`, `llm_vllm` | `--tags core_nginx` deploys just nginx |
| Service | `nginx`, `postgres`, `authz`, `milvus` | Backward compatible with existing tags |
| Role | `search_api`, `agent_api`, `ingest` | Fine-grained control |

### Examples

```bash
# Deploy all core services
ansible-playbook -i inventory/staging site.yml --tags core

# Deploy just nginx from core
ansible-playbook -i inventory/staging site.yml --tags core_nginx

# Deploy nginx (backward compatible)
ansible-playbook -i inventory/staging site.yml --tags nginx
```

## Environment Selection

### Staging

```bash
make staging  # Switch to staging (persisted)
make core     # Deploys to staging

# Or one-time override
make core INV=inventory/staging
```

### Production

```bash
make production  # Switch to production (persisted)
make core        # Deploys to production

# Or one-time override
make core INV=inventory/production
```

## Recovery Scenarios

### Deployment Failed at APIs

If deployment fails during the APIs phase:

```bash
# Core and LLM are already deployed, resume from APIs
make apis && make apps-frontend
```

### Need to Update Just Search

```bash
make apis-search
```

### Fresh Installation

```bash
# Full deploy
make all

# Or step by step with verification
make core
make verify-health  # Check core services
make llm
make apis
make verify-health  # Check all services
make apps-frontend
```

## Related Documentation

- [Deployment Optimization](./deployment-optimization.md)
- [App Auto-Deploy](./app-auto-deploy.md)
- [Nginx API Gateway](./nginx-api-gateway.md)
