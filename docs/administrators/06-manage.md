---
title: "Service Management"
category: "administrator"
order: 6
description: "Managing Busibox services with the CLI"
published: true
---

# Service Management

All Busibox service management is done through the **Busibox CLI** -- an interactive terminal UI that handles vault decryption, SSH connectivity, and service operations across Docker, Proxmox, and Kubernetes deployments.

**Critical**: Never run `docker compose`, `docker`, or `ansible-playbook` directly. The CLI decrypts vault secrets and injects them at runtime; direct commands skip this and cause authentication failures.

## Using the CLI

Launch the CLI from the repository root:

```bash
busibox
```

On launch, the CLI prompts for your master password to decrypt the vault key for the active profile. Once unlocked, all service operations use the decrypted secrets automatically.

### Manage Screen

Press `m` from the main menu to enter the **Manage** screen:

- **View status** -- see all services with health indicators (running, stopped, error)
- **Restart** -- restart a service with existing configuration
- **Stop / Start** -- stop or start individual services
- **Redeploy** -- full rebuild: pull code, install dependencies, inject fresh secrets, restart
- **Logs** -- follow service logs in real-time
- **Switch profiles** -- change between deployment targets (Docker, Proxmox, K8s)

### Models Screen

Press `m` again (or access from the main menu) for AI model management:

- Browse available models with resource requirements
- Download models to your deployment target
- Benchmark model performance
- Configure model tiers (which models serve which tasks)

### When to Restart vs Redeploy

- **Restart** -- service is misbehaving, you want to clear its state. Uses existing configuration.
- **Redeploy** -- you changed configuration, updated code, or rotated secrets. Performs a full rebuild.

## Services Reference

### Infrastructure

| Service | Description |
|---------|-------------|
| `postgres` | PostgreSQL database |
| `redis` | Redis queue/cache |
| `minio` | MinIO object storage |
| `milvus` | Milvus vector database |

### API Services

| Service | Description |
|---------|-------------|
| `authz` | Authentication and authorization |
| `data` | Data API (upload, metadata, structured data) |
| `search` | Search API (hybrid search, retrieval) |
| `agent` | Agent API (chat, agent orchestration) |
| `embedding` | Embedding API (vector generation) |
| `deploy` | Deploy API (app deployment) |
| `docs` | Documentation API |

### LLM Services

| Service | Description |
|---------|-------------|
| `litellm` | LiteLLM model gateway |
| `vllm` | vLLM local inference (NVIDIA GPU) |
| `colpali` | ColPali visual embeddings |

### Frontend

| Service | Description |
|---------|-------------|
| `nginx` | Reverse proxy |
| `busibox-portal` | Busibox Portal application |
| `busibox-agents` | Busibox Agents application |
| `core-apps` | Both Portal and Agents |

### Service Groups

| Group | Includes |
|-------|---------|
| `infrastructure` | postgres, redis, minio, milvus |
| `apis` | authz, data, search, agent, embedding, deploy, docs |
| `llm` | litellm, vllm, colpali |
| `frontend` | nginx, core-apps |
| `all` | Everything |

## Common Workflows

### After Pulling New Code

Launch the CLI, select the affected profile, and redeploy the changed services from the Manage screen.

### Investigating Issues

1. Open the Manage screen and check service status
2. View logs for the affected service
3. Restart if the service is in a bad state
4. Redeploy if restart doesn't resolve the issue

### Managing Multiple Environments

The CLI supports multiple deployment profiles. Each profile has its own:

- Deployment target (Docker, Proxmox, Kubernetes)
- SSH configuration
- Vault key (encrypted separately)
- Environment settings (staging vs production)

Switch between profiles from the main menu. See [Multiple Deployments](07-multiple-deployments.md).

## Proxmox Container Reference

For low-level debugging on the Proxmox host, containers can be accessed directly:

| Container | Default CTID |
|-----------|-------------|
| proxy-lxc | 200 |
| apps-lxc | 202 |
| pg-lxc | 203 |
| milvus-lxc | 204 |
| files-lxc | 205 |
| data-lxc | 206 |
| agent-lxc | 207 |
| authz-lxc | 210 |

## For AI Agents

AI coding agents should use the `mcp-admin` MCP server rather than the CLI or `make` commands directly. The MCP server provides the same service management operations with proper vault authentication. See [MCP and Make Internals](../developers/reference/mcp-and-make-internals.md).

## Next Steps

- [Multiple deployments (staging/production)](07-multiple-deployments.md)
- [Troubleshooting](08-troubleshooting.md)
