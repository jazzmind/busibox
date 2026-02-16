# Busibox Kubernetes Deployment

Deploy Busibox to a Kubernetes cluster (e.g., Rackspace Spot).

## Quick Start

```bash
# Full deployment (sync code, build on cluster, apply manifests)
make k8s-deploy

# Connect to the AI Portal (HTTPS tunnel)
make connect

# Or step by step:
make k8s-sync      # Sync code to in-cluster build server
make k8s-build     # Build images on build server + push to registry
make k8s-secrets   # Generate secrets from vault
make k8s-apply     # Apply manifests to cluster
```

## Prerequisites

1. **kubectl** configured with cluster access
2. **Kubeconfig** at `k8s/kubeconfig-rackspace-spot.yaml`
3. **Ansible vault** access (for secrets) or manual secrets configuration
4. **mkcert** (optional, recommended) for browser-trusted local SSL

## How It Works

Images are built **in-cluster** on a dedicated Docker-in-Docker (DinD) build server pod.
Source code is synced via `kubectl cp`, built natively on x86, and pushed to an in-cluster
Docker registry. No internet round-trips for image operations.

```
Admin Mac                   K8s Cluster
┌──────────────┐  kubectl   ┌──────────────────────────────────────┐
│              │  cp/exec   │  build-server (DinD)                 │
│  busibox/    │ ──────────>│    docker build -> docker push       │
│  source code │            │                          │           │
│              │            │              ┌────────────┘           │
│              │            │              v                        │
│              │            │  registry (localhost:30500)           │
│              │            │              │                        │
│              │            │              v                        │
│              │  kubectl   │  pods pull from localhost:30500       │
│              │  port-fwd  │                                      │
│              │ <──────────│  nginx (HTTPS)                       │
└──────────────┘            └──────────────────────────────────────┘
```

1. **`make k8s-sync`** copies source code to the build-server pod via `kubectl cp`
2. **`make k8s-build`** runs `docker build` + `docker push` inside the build-server
3. Images are pushed to the in-cluster registry at `localhost:30500`
4. **`make k8s-apply`** deploys manifests; kubelet pulls from `localhost:30500`
5. **`make connect`** sets up a local HTTPS tunnel for browser access

## Setup

### 1. Kubeconfig

Place your kubeconfig at `k8s/kubeconfig-rackspace-spot.yaml` or set the `KUBECONFIG` env var.

### 2. Secrets

Secrets are automatically generated from Ansible vault during deployment. To configure manually:

```bash
cp k8s/secrets/secrets.yaml.example k8s/secrets/secrets.yaml
# Edit with your actual values
kubectl apply -f k8s/secrets/secrets.yaml -n busibox
```

## Accessing the AI Portal

### Local Access via `make connect` (Recommended)

The easiest way to access the AI Portal running on K8s:

```bash
# Default: sets up https://busibox.local/portal
make connect

# Custom domain
make connect DOMAIN=my.local

# High port (no sudo needed)
make connect LOCAL_PORT=8443

# Check status
make k8s-connect-status

# Disconnect
make disconnect
```

**What `make connect` does:**

1. Generates an SSL certificate for the domain (uses mkcert if available for zero browser warnings)
2. Creates a K8s TLS Secret with the certificate
3. Patches the nginx ConfigMap to serve HTTPS
4. Restarts nginx to pick up the TLS configuration
5. Adds a `/etc/hosts` entry pointing the domain to 127.0.0.1 (requires sudo)
6. Starts `kubectl port-forward` in the background
7. Opens your browser to `https://busibox.local/portal`

**SSL Certificates:**

- **With mkcert** (recommended): `brew install mkcert` - creates locally-trusted certs, no browser warnings
- **Without mkcert**: Self-signed cert is generated. You'll see a browser warning on first visit.

### Direct NodePort Access

If the K8s node has a public IP, you can access services directly:

```bash
# Check the node IP
make k8s-status

# Access via NodePort (HTTP only by default)
http://<node-ip>:30080/portal
```

### Future: LoadBalancer

A `LoadBalancer` Service type can be configured later for production access with a real domain and Let's Encrypt SSL. This can be set up through the AI Portal admin UI or `make manage`.

## Architecture

```
k8s/
├── base/                        # Base Kustomize manifests
│   ├── infrastructure/          # PostgreSQL, Redis, MinIO, Milvus, etcd
│   ├── build/                   # In-cluster build server + Docker registry
│   ├── rbac/                    # ServiceAccount and RBAC for deploy-api
│   ├── apis/                    # AuthZ, Data, Search, Agent, Deploy, etc.
│   ├── llm/                     # LiteLLM gateway
│   ├── init-jobs/               # MinIO bucket init, Milvus schema init
│   └── frontend/                # Nginx reverse proxy (HTTP + HTTPS)
├── overlays/
│   └── rackspace-spot/          # Rackspace Spot specific patches
├── secrets/                     # Secret templates (not committed)
├── terraform/                   # Terraform for node pools
├── kubeconfig-rackspace-spot.yaml
└── README.md
```

## Commands

| Command | Description |
|---------|-------------|
| `make k8s-deploy` | Full deployment (sync, build, push, apply) |
| `make k8s-sync` | Sync source code to build server |
| `make k8s-build` | Build images on build server + push to registry |
| `make k8s-apply` | Apply manifests only |
| `make k8s-secrets` | Generate secrets from vault |
| `make k8s-status` | Show deployment status |
| `make k8s-delete` | Delete all resources |
| `make k8s-logs SERVICE=authz-api` | View pod logs |
| `make connect` | HTTPS tunnel to AI Portal |
| `make disconnect` | Stop HTTPS tunnel |
| `make k8s-connect-status` | Check tunnel status |

### Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `K8S_OVERLAY` | `rackspace-spot` | Kustomize overlay |
| `K8S_TAG` | git short SHA | Image tag |
| `KUBECONFIG` | `k8s/kubeconfig-rackspace-spot.yaml` | Kubeconfig path |
| `DOMAIN` | `busibox.local` | Domain for `make connect` |
| `LOCAL_PORT` | `443` | Local port for `make connect` |

## Services

### Build Infrastructure
- **Build Server** - DinD pod for native x86 image builds (30Gi PVC)
- **Registry** - In-cluster Docker registry at `localhost:30500` (20Gi PVC)

### Infrastructure (StatefulSets with persistent storage)
- **PostgreSQL** - Shared database (authz, data, agent, litellm, ai_portal)
- **Redis** - Job queue and caching
- **MinIO** - S3-compatible object storage
- **Milvus** - Vector database (with etcd + milvus-minio)

### APIs (Deployments)
- **AuthZ API** (8010) - Authentication & authorization
- **Data API** (8002) - Document processing
- **Data Worker** - Background job processor
- **Search API** (8003) - Semantic search
- **Agent API** (8000) - AI agent operations
- **Deploy API** (8011) - Application deployment to K8s
- **Bridge API** (8081) - Multi-channel communication
- **Docs API** (8004) - Documentation service
- **Embedding API** (8005) - FastEmbed embeddings

### Core Apps (via Deploy API)
- **AI Portal** (3000) - Main dashboard and admin UI
- **Agent Manager** (3001) - Agent management and chat

### LLM
- **LiteLLM** (4000) - Unified LLM gateway

### Frontend
- **Nginx** (NodePort 30080/30443) - Reverse proxy with HTTPS

## Resource Requirements

Tested on Rackspace Spot `mh.vs1.xlarge-ord`:
- 8 CPU, 60GB RAM, ~200GB storage
- All services fit comfortably on a single node

## Storage

Uses Cinder CSI `ssd` storage class (default on Rackspace Spot):
- PostgreSQL: 20Gi
- Redis: 5Gi
- MinIO: 20Gi
- Milvus: 20Gi + 10Gi (milvus-minio) + 5Gi (etcd)
- Model cache: 10Gi (shared across APIs)
- FastEmbed cache: 5Gi
- Build server: 30Gi (Docker layer cache)
- Registry: 20Gi (image storage)

## Adding New Overlays

To deploy to a different cluster:

1. Create `k8s/overlays/<name>/kustomization.yaml`
2. Reference `../../base` as the base
3. Add cluster-specific patches
4. Deploy with `make k8s-deploy K8S_OVERLAY=<name>`

## Troubleshooting

```bash
# Check pod status
make k8s-status

# View logs
make k8s-logs SERVICE=authz-api
make k8s-logs SERVICE=postgres
make k8s-logs SERVICE=proxy

# Describe a failing pod
kubectl --kubeconfig=k8s/kubeconfig-rackspace-spot.yaml describe pod -n busibox -l app=authz-api

# Shell into a pod
kubectl --kubeconfig=k8s/kubeconfig-rackspace-spot.yaml exec -it -n busibox deploy/authz-api -- bash

# Check events
kubectl --kubeconfig=k8s/kubeconfig-rackspace-spot.yaml get events -n busibox --sort-by='.lastTimestamp'

# Check connect tunnel
make k8s-connect-status

# Rebuild and reconnect
make disconnect
make k8s-deploy
make connect
```

### Common Issues

**Port 443 in use**: Use a high port: `make connect LOCAL_PORT=8443`

**Browser SSL warning**: Install mkcert for trusted certs: `brew install mkcert`

**Port-forward dies**: Just run `make connect` again - it's idempotent.

**Proxy not serving HTTPS**: Run `make connect` to configure HTTPS, or check:
```bash
make k8s-logs SERVICE=proxy
kubectl --kubeconfig=k8s/kubeconfig-rackspace-spot.yaml get secret proxy-tls -n busibox
```
