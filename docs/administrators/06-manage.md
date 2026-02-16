---
title: "Command-Line Management"
category: "administrator"
order: 6
description: "Managing Busibox services from the command line"
published: true
---

# Command-Line Management

All Busibox service operations go through the unified `make` interface. This ensures secrets are properly injected, environments are auto-detected, and commands work identically across Docker and Proxmox deployments.

**Critical**: Never run `docker compose`, `docker`, or `ansible-playbook` directly. Always use `make` commands.

## Quick Reference

```bash
# Deploy a service
make install SERVICE=authz

# Restart a service
make manage SERVICE=authz ACTION=restart

# View logs
make manage SERVICE=authz ACTION=logs

# Check status
make manage SERVICE=authz ACTION=status

# Full redeploy (rebuild + restart with fresh secrets)
make manage SERVICE=authz ACTION=redeploy
```

## Service Names

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
| `core-apps` | Both Busibox Portal and Busibox Agents |

### Service Groups

| Group | Includes |
|-------|---------|
| `infrastructure` | postgres, redis, minio, milvus |
| `apis` | authz, data, search, agent, embedding, deploy, docs |
| `llm` | litellm, vllm, colpali |
| `frontend` | nginx, core-apps |
| `all` | Everything |

## Deploying Services

### Install (Deploy)

```bash
# Single service
make install SERVICE=authz

# Multiple services (comma-separated)
make install SERVICE=authz,agent,data

# Service group
make install SERVICE=apis

# Everything
make install SERVICE=all

# Specific version/branch for apps
make install SERVICE=busibox-portal REF=v1.2.3
```

`make install` performs a full deployment: pulls code, installs dependencies, injects secrets from vault, builds, and starts the service.

## Managing Running Services

### Actions

| Action | What It Does |
|--------|-------------|
| `status` | Show service status (running/stopped, uptime) |
| `start` | Start a stopped service |
| `stop` | Stop a running service |
| `restart` | Stop and start a service (keeps existing config) |
| `logs` | Follow service logs in real-time |
| `redeploy` | Full rebuild: pull code, install deps, inject secrets, restart |

### Examples

```bash
# Check if services are running
make manage SERVICE=authz,postgres ACTION=status

# Restart a service (uses existing environment)
make manage SERVICE=agent ACTION=restart

# Full redeploy (re-injects secrets, rebuilds)
make manage SERVICE=agent ACTION=redeploy

# Stop a service
make manage SERVICE=vllm ACTION=stop

# View logs (Ctrl+C to exit)
make manage SERVICE=data ACTION=logs
```

**When to restart vs redeploy**:
- **Restart**: Service is misbehaving, you want to clear its state
- **Redeploy**: You changed configuration, updated code, or rotated secrets

## Interactive Menus

Running `make` commands without arguments launches interactive menus:

```bash
make            # Main launcher menu
make install    # Installation wizard (no SERVICE=)
make manage     # Service management menu (no SERVICE=)
make test       # Testing menu
```

## Proxmox-Specific Commands

On the Proxmox host, you can also interact with containers directly:

```bash
# Check container status
pct status <CTID>

# Enter a container shell
pct enter <CTID>

# Inside a container, check services
systemctl status <service-name>
journalctl -u <service-name> -n 50 --no-pager
```

### Container IDs

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

## Testing

```bash
# Docker testing (local)
make test-docker SERVICE=authz

# Remote testing (against staging/production)
make test-local SERVICE=agent INV=staging

# Interactive test menu
make test
```

## Health Checks

Quick health check commands:

```bash
# All services
make manage SERVICE=all ACTION=status

# Individual health endpoints
curl http://<authz-ip>:8010/health/live
curl http://<data-ip>:8002/health
curl http://<search-ip>:8003/health
curl http://<agent-ip>:8000/health
curl http://<files-ip>:9000/minio/health/live
curl http://<milvus-ip>:9091/healthz
```

## Common Workflows

### After Pulling New Code

```bash
git pull origin main
make install SERVICE=<changed-services>
```

### After Changing Secrets

```bash
cd provision/ansible
ansible-vault edit roles/secrets/vars/vault.yml
cd ../..
make install SERVICE=<affected-services>
```

### Investigating Issues

```bash
# 1. Check status
make manage SERVICE=data ACTION=status

# 2. Check logs
make manage SERVICE=data ACTION=logs

# 3. Restart if needed
make manage SERVICE=data ACTION=restart

# 4. Full redeploy if restart doesn't help
make manage SERVICE=data ACTION=redeploy
```

## Reference

- [Log Viewing Commands](../developers/reference/log-viewing-commands.md) — Scripts for viewing logs by app or service
- [Core App Rebuild](../developers/reference/core-app-rebuild.md) — Rebuilding core apps without container restart

## Next Steps

- [Multiple deployments (staging/production)](07-multiple-deployments.md)
- [Troubleshooting](08-troubleshooting.md)
