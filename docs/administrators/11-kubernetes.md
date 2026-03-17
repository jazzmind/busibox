---
title: "Kubernetes Deployment"
category: "administrator"
order: 11
description: "Deploy and manage Busibox on Kubernetes clusters"
published: true
---

# Kubernetes Deployment

Busibox can deploy to Kubernetes clusters in addition to Docker and Proxmox LXC containers. The Kubernetes backend uses Kustomize manifests with overlay support for different clusters.

## Overview

The Kubernetes deployment uses an **in-cluster build** architecture: source code is synced to a build server pod, images are built natively on the cluster, pushed to an in-cluster Docker registry, and pulled by service pods. No external registry or internet round-trips are needed for image operations.

```
Admin Workstation              K8s Cluster
┌──────────────┐  kubectl      ┌──────────────────────────────────────┐
│              │  cp/exec      │  build-server (DinD)                 │
│  busibox/    │ ─────────────>│    docker build -> docker push       │
│  source code │               │              │                       │
│              │               │              v                       │
│              │               │  registry (localhost:30500)           │
│              │  kubectl      │              │                       │
│              │  port-fwd     │              v                       │
│              │ <─────────────│  nginx (HTTPS) -> service pods       │
└──────────────┘               └──────────────────────────────────────┘
```

## Prerequisites

- **kubectl** configured with cluster access
- **Kubeconfig** for your target cluster
- **Ansible vault** access (for secrets generation) or manual secrets
- **mkcert** (optional, recommended) for browser-trusted local SSL

## Getting Started

### Option 1: Busibox CLI (Recommended)

The Busibox CLI provides guided Kubernetes setup. Run `busibox`, create or edit a profile, and select **k8s** as the backend:

1. Configure kubeconfig path and overlay name
2. Optionally set a Spot API token (for Rackspace Spot clusters)
3. Select environment (production/staging)
4. Deploy and manage from the K8s management screen

From the CLI, the K8s management screen provides: deploy, apply manifests, generate secrets, check status, view logs, delete resources, and set up HTTPS tunnels.

### Option 2: `make` Commands

```bash
# Full deployment (sync code, build images, apply manifests)
make k8s-deploy

# Step by step:
make k8s-sync      # Sync code to in-cluster build server
make k8s-build     # Build images on build server + push to registry
make k8s-secrets   # Generate secrets from vault
make k8s-apply     # Apply manifests to cluster

# Access the AI Portal (HTTPS tunnel)
make connect

# Manage
make k8s-status                  # Show deployment status
make k8s-logs SERVICE=authz-api  # View pod logs
make disconnect                  # Stop HTTPS tunnel
make k8s-delete                  # Delete all resources
```

## Architecture

Busibox Kubernetes manifests live in `k8s/`:

```
k8s/
├── base/                        # Base Kustomize manifests
│   ├── infrastructure/          # PostgreSQL, Redis, MinIO, Milvus
│   ├── build/                   # In-cluster build server + Docker registry
│   ├── rbac/                    # ServiceAccount and RBAC for deploy-api
│   ├── apis/                    # AuthZ, Data, Search, Agent, Deploy, etc.
│   ├── llm/                     # LiteLLM gateway
│   ├── init-jobs/               # MinIO bucket init, Milvus schema init
│   └── frontend/                # Nginx reverse proxy
├── overlays/
│   └── rackspace-spot/          # Rackspace Spot specific patches
├── secrets/                     # Secret templates (not committed)
├── terraform/                   # Terraform for node pools
└── README.md                    # Detailed K8s reference
```

### Services Deployed

| Category | Services |
|----------|----------|
| **Infrastructure** | PostgreSQL, Redis, MinIO, Milvus (with etcd) |
| **APIs** | AuthZ, Data, Data Worker, Search, Agent, Deploy, Bridge, Docs, Embedding |
| **LLM** | LiteLLM gateway |
| **Frontend** | Nginx reverse proxy (NodePort 30080/30443) |
| **Build** | Build server (DinD), in-cluster Docker registry |
| **Core Apps** | AI Portal, Agent Manager (deployed via Deploy API) |

## Accessing the AI Portal

### Local HTTPS Tunnel (Recommended)

```bash
make connect                          # https://busibox.local/portal
make connect DOMAIN=my.local          # Custom domain
make connect LOCAL_PORT=8443          # High port (no sudo)
make k8s-connect-status               # Check tunnel status
make disconnect                       # Stop tunnel
```

`make connect` generates an SSL certificate (using mkcert if available for zero browser warnings), configures nginx for HTTPS, adds a `/etc/hosts` entry, and starts `kubectl port-forward`.

### Direct NodePort Access

If the K8s node has a public IP:

```bash
make k8s-status
# Access via http://<node-ip>:30080/portal
```

## Adding Cluster Overlays

To deploy to a different cluster, create a new Kustomize overlay:

1. Create `k8s/overlays/<name>/kustomization.yaml`
2. Reference `../../base` as the base
3. Add cluster-specific patches (storage classes, resource limits, GPU config)
4. Deploy with `make k8s-deploy K8S_OVERLAY=<name>`

## Resource Requirements

Tested on Rackspace Spot `mh.vs1.xlarge-ord` (8 CPU, 60GB RAM, ~200GB storage). All services fit on a single node. Storage uses persistent volume claims:

| Service | Storage |
|---------|---------|
| PostgreSQL | 20Gi |
| Redis | 5Gi |
| MinIO | 20Gi |
| Milvus + etcd | 25Gi |
| Build server | 30Gi |
| Registry | 20Gi |
| Model cache | 10Gi |

## Configuration Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `K8S_OVERLAY` | `rackspace-spot` | Kustomize overlay to use |
| `K8S_TAG` | git short SHA | Image tag for builds |
| `KUBECONFIG` | `k8s/kubeconfig-rackspace-spot.yaml` | Path to kubeconfig |
| `DOMAIN` | `busibox.local` | Domain for `make connect` |
| `LOCAL_PORT` | `443` | Local port for `make connect` |

## Troubleshooting

```bash
make k8s-status                        # Check pod status
make k8s-logs SERVICE=authz-api        # View logs
make k8s-connect-status                # Check tunnel status
```

Common issues:

- **Port 443 in use** -- use `make connect LOCAL_PORT=8443`
- **Browser SSL warning** -- install mkcert: `brew install mkcert`
- **Port-forward dies** -- run `make connect` again (idempotent)

For detailed troubleshooting, see `k8s/README.md` in the repository root.

## Related

- [Administrator Quick Start](01-quickstart.md)
- [Command-Line Management](06-manage.md)
- [Multiple Deployments](07-multiple-deployments.md)
