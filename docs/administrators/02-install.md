---
title: "Installation"
category: "administrator"
order: 2
description: "Full installation guide for Proxmox and Docker deployments"
published: true
---

# Installation

Busibox runs on either Proxmox LXC containers or Docker. This guide covers both paths in detail.

## Deployment Options

| Option | Best For | Requirements |
|--------|---------|-------------|
| **Proxmox LXC** | Production, multi-node | Proxmox VE host, dedicated hardware |
| **Docker** | Development, single-machine | Docker Desktop or Docker Engine |

Both options use the same `make` commands for service management. The underlying orchestration differs but the experience is identical.

## Proxmox Installation

### 1. Prepare the Host

On your Proxmox host:

```bash
# Clone the repository
cd /root
git clone <busibox-repo-url>
cd busibox
```

### 2. Configure Container Variables

Edit `provision/pct/vars.env` with your environment:

```bash
# Network
SUBNET=10.96.200.0/21
GATEWAY=10.96.200.1

# Container IDs and IPs
CT_PROXY=200
IP_PROXY=10.96.200.200

CT_APPS=202
IP_APPS=10.96.200.201

CT_PG=203
IP_PG=10.96.200.203

CT_MILVUS=204
IP_MILVUS=10.96.200.204

CT_FILES=205
IP_FILES=10.96.200.205

CT_DATA=206
IP_DATA=10.96.200.206

CT_AGENT=207
IP_AGENT=10.96.200.207

CT_AUTHZ=210
IP_AUTHZ=10.96.200.210

# SSH access
SSH_PUBKEY_PATH=/root/.ssh/id_rsa.pub

# Proxmox template
TEMPLATE=local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst
```

### 3. Create Containers

```bash
cd /root/busibox/provision/pct
bash create_lxc_base.sh production
```

This creates all LXC containers with:
- Correct networking and firewall rules
- SSH access configured
- Storage mounts
- Resource limits

### 4. Deploy from Admin Workstation

On your admin workstation (not the Proxmox host):

```bash
cd busibox

# Install Ansible dependencies
cd provision/ansible && make deps && cd ../..

# Deploy all services
make install SERVICE=all
```

### Container Map

| Container | CTID | IP | Services |
|-----------|------|-----|----------|
| proxy-lxc | 200 | 10.96.200.200 | nginx reverse proxy |
| apps-lxc | 202 | 10.96.200.201 | AI Portal, Agent Manager, custom apps |
| pg-lxc | 203 | 10.96.200.203 | PostgreSQL |
| milvus-lxc | 204 | 10.96.200.204 | Milvus vector database, Search API |
| files-lxc | 205 | 10.96.200.205 | MinIO object storage |
| data-lxc | 206 | 10.96.200.206 | Data API, Data Worker, Redis |
| agent-lxc | 207 | 10.96.200.207 | Agent API, LiteLLM |
| authz-lxc | 210 | 10.96.200.210 | AuthZ service, Deploy API |

## Docker Installation

### 1. Prerequisites

- Docker Desktop (macOS/Windows) or Docker Engine (Linux)
- Docker Compose v2
- At least 16 GB RAM recommended

### 2. Configure

```bash
cd busibox

# Local inventory is pre-configured for Docker
# Edit if needed:
# provision/ansible/inventory/local/group_vars/all.yml
```

### 3. Deploy

```bash
make install SERVICE=all
```

Docker Compose handles container creation, networking, and volume management automatically.

## GPU and Storage (Proxmox)

For GPU passthrough (vLLM, ColPali) and ZFS storage recommendations, see:
- [GPU Passthrough](../developers/reference/gpu-passthrough.md)
- [ZFS Storage Recommendations](../developers/reference/zfs-recommendations.md)

## Secrets Management

Busibox uses Ansible Vault for secrets. Secrets are stored encrypted in `provision/ansible/roles/secrets/vars/vault.yml` and injected at deploy time.

### Viewing/Editing Secrets

```bash
cd provision/ansible
ansible-vault edit roles/secrets/vars/vault.yml
```

### Key Secrets

| Secret | Purpose |
|--------|---------|
| `vault_postgres_password` | PostgreSQL admin password |
| `vault_minio_secret_key` | MinIO storage credentials |
| `vault_jwt_signing_key` | JWT token signing |
| `vault_litellm_api_key` | LLM gateway access |
| `vault_github_token` | GitHub access for app deployment |

**Important**: Never commit unencrypted secrets. Never run services directly with `docker compose` or `ansible-playbook` -- always use `make` commands, which inject secrets from the vault.

## Validation

After deployment, verify all services:

```bash
# Check all service status
make manage SERVICE=all ACTION=status

# Health checks
curl http://<authz-ip>:8010/health/live    # AuthZ
curl http://<data-ip>:8002/health           # Data API
curl http://<search-ip>:8003/health         # Search API
curl http://<agent-ip>:8000/health          # Agent API
curl http://<files-ip>:9000/minio/health/live  # MinIO
```

## Deployment Order

Services have dependencies and should be deployed in order:

1. **Infrastructure**: PostgreSQL, Redis, MinIO, Milvus
2. **Security**: AuthZ service
3. **LLM**: LiteLLM gateway, vLLM/MLX (if using local models)
4. **APIs**: Data API, Search API, Embedding API, Agent API
5. **Frontend**: nginx, AI Portal, Agent Manager

When using `make install SERVICE=all`, this order is handled automatically.

## Updating

To update Busibox after pulling new code:

```bash
git pull origin main

# Redeploy changed services
make install SERVICE=authz,agent,data

# Or redeploy everything
make install SERVICE=all
```

Individual services can be updated without affecting others. See [Command-Line Management](06-manage.md) for more options.
