---
title: "Multiple Deployments"
category: "administrator"
order: 7
description: "Managing staging and production environments"
published: true
---

# Multiple Deployments

Busibox supports multiple independent environments (staging, production) on the same Proxmox host or separate infrastructure. This lets you test changes safely before deploying to production.

## Environment Architecture

Each environment gets its own set of containers with separate IPs, databases, and storage:

```
Production (10.96.200.x)          Staging (10.96.201.x)
┌──────────────────────┐          ┌──────────────────────┐
│ proxy    200.200     │          │ proxy    201.200     │
│ apps     200.201     │          │ apps     201.201     │
│ pg       200.203     │          │ pg       201.203     │
│ milvus   200.204     │          │ milvus   201.204     │
│ files    200.205     │          │ files    201.205     │
│ data     200.206     │          │ data     201.206     │
│ agent    200.207     │          │ agent    201.207     │
│ authz    200.210     │          │ authz    201.210     │
└──────────────────────┘          └──────────────────────┘
```

Environments are completely isolated -- separate databases, separate vector stores, separate file storage. Changes in staging cannot affect production.

## Setting Up a Staging Environment

### 1. Create Staging Containers (Proxmox)

On the Proxmox host:

```bash
cd /root/busibox/provision/pct
bash create_lxc_base.sh staging
```

This creates a parallel set of containers with staging IPs (default: 10.96.201.x subnet).

### 2. Configure Staging Inventory

Staging configuration lives in `provision/ansible/inventory/staging/`:

```
provision/ansible/inventory/
├── production/
│   ├── hosts.yml          # Production container IPs
│   └── group_vars/
│       └── all.yml        # Production-specific settings
├── staging/
│   ├── hosts.yml          # Staging container IPs
│   └── group_vars/
│       └── all.yml        # Staging-specific settings
└── local/
    └── group_vars/
        └── all.yml        # Docker local settings
```

### 3. Deploy to Staging

```bash
# Deploy everything to staging
make install SERVICE=all INV=staging

# Deploy specific services to staging
make install SERVICE=authz,agent INV=staging
```

The `INV=staging` flag tells the make system to use the staging inventory.

## Working with Environments

### Deploying

```bash
# Production (default)
make install SERVICE=authz

# Staging
make install SERVICE=authz INV=staging
```

### Managing

```bash
# Production status
make manage SERVICE=all ACTION=status

# Staging status
make manage SERVICE=all ACTION=status INV=staging

# Staging logs
make manage SERVICE=agent ACTION=logs INV=staging
```

### Testing

```bash
# Test against staging
make test-local SERVICE=agent INV=staging

# Test against production
make test-local SERVICE=agent INV=production
```

## Deployment Groups

For large deployments, services can be deployed in groups following dependency order:

### Deployment Order

1. **Infrastructure** -- databases and storage must be up first
2. **Security** -- AuthZ service for authentication
3. **LLM** -- model gateway and inference engines
4. **APIs** -- application services that depend on infrastructure
5. **Frontend** -- web applications and reverse proxy

### Group Commands

```bash
# Deploy by group
make install SERVICE=infrastructure
make install SERVICE=apis
make install SERVICE=frontend

# Deploy everything in order
make install SERVICE=all
```

When using `SERVICE=all`, the correct order is handled automatically.

## Environment-Specific Configuration

### Staging vs Production Differences

| Setting | Production | Staging |
|---------|-----------|---------|
| Container IPs | 10.96.200.x | 10.96.201.x |
| Container IDs | 200-219 | 300-319 |
| Domain | your-domain.com | staging.your-domain.com |
| SSL | Production certificates | Self-signed or staging certs |
| AI models | Full model set | Minimal set (save resources) |

### Shared Configuration

Settings that are the same across environments live in `provision/ansible/group_vars/all/`:

- App definitions (`apps.yml`)
- Service versions
- Default configuration values

### Environment Overrides

Environment-specific overrides go in the inventory group_vars:

```yaml
# inventory/staging/group_vars/all.yml
domain: staging.example.com
enable_gpu: false
litellm_models:
  - gpt-4o-mini  # Minimal model set for staging
```

## Promotion Workflow

A typical workflow for promoting changes:

1. **Develop** -- make changes in the codebase
2. **Deploy to staging** -- `make install SERVICE=<service> INV=staging`
3. **Test on staging** -- `make test-local SERVICE=<service> INV=staging`
4. **Verify manually** -- check the staging portal
5. **Deploy to production** -- `make install SERVICE=<service>`
6. **Verify production** -- `make manage SERVICE=<service> ACTION=status`

## Docker Environments

For Docker-based deployments, multiple environments can be managed using different compose profiles or separate Docker networks. The local inventory (`inventory/local/`) is pre-configured for single-machine Docker deployments.

## Resource Considerations

Running multiple environments on the same host requires sufficient resources:

| Resource | Production | + Staging |
|----------|-----------|-----------|
| RAM | 16 GB minimum | 32 GB recommended |
| CPU | 4 cores minimum | 8 cores recommended |
| Storage | 100 GB minimum | 200 GB recommended |
| GPU (optional) | 1x for vLLM | Shared or separate |

Staging can run with reduced resources by:
- Using fewer/smaller AI models
- Disabling GPU-dependent features (ColPali, vLLM)
- Using cloud models instead of local inference

## Next Steps

- [Troubleshooting](08-troubleshooting.md)
- [Command-line management](06-manage.md)
