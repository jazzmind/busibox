---
title: "Proxmox Container Rebuilds With Persistent Data"
category: "administrator"
order: 9
description: "Rebuild LXC containers without clobbering PostgreSQL, Milvus, MinIO, Neo4j, or Redis data"
published: true
---

# Proxmox Container Rebuilds With Persistent Data

This guide explains how to rebuild a single Proxmox LXC container while preserving data stored on host bind mounts.

## Why This Works

Stateful containers now use host-backed mount paths under:

- `/var/lib/data/*` for production
- `/var/lib/data-staging/*` for staging

Rebuilding a container replaces the container rootfs but keeps host mount data intact.

## Persistent Data Paths

| Service | Host Path (Production) | Container Path |
|--------|-------------------------|----------------|
| PostgreSQL | `/var/lib/data/postgres` | `/var/lib/postgresql/data` |
| Redis | `/var/lib/data/redis` | `/var/lib/redis` |
| Milvus | `/var/lib/data/milvus` | `/srv/milvus/data` |
| MinIO | `/var/lib/data/minio` | `/srv/minio/data` |
| Neo4j | `/var/lib/data/neo4j` | `/srv/neo4j/data` |

## Rebuild Script

Use:

```bash
bash provision/pct/containers/rebuild-container.sh <container-name> [staging|production] [--confirm]
```

Examples:

```bash
# Dry run (no destroy)
bash provision/pct/containers/rebuild-container.sh pg-lxc production

# Execute rebuild
bash provision/pct/containers/rebuild-container.sh pg-lxc production --confirm

# Staging
bash provision/pct/containers/rebuild-container.sh STAGE-pg-lxc staging --confirm
```

## Safety Checks Performed

Before destruction, the script:

1. Confirms the container exists on the host
2. Reads and records all LXC `mp*` mount definitions
3. Verifies host mount directories exist
4. Verifies stateful data mounts under `/var/lib/data*` are non-empty
5. Runs in dry-run mode unless `--confirm` is passed

After recreation, the script verifies expected mount points were restored.

## Container Names Supported

- `proxy-lxc`
- `core-apps-lxc`
- `user-apps-lxc`
- `agent-lxc`
- `authz-lxc`
- `pg-lxc`
- `milvus-lxc`
- `files-lxc`
- `neo4j-lxc`
- `data-lxc`
- `litellm-lxc`
- `bridge-lxc`
- `vllm-lxc`
- `ollama-lxc`

For staging, `STAGE-` prefix is optional in script input.

## Clean Staging Reinstall

To rebuild the entire staging environment while preserving stateful data and then re-run configuration:

```bash
# 1) Dry run safety checks
bash provision/pct/containers/rebuild-staging.sh

# 2) Perform clean reinstall (destroy + recreate staging LXCs)
bash provision/pct/containers/rebuild-staging.sh --confirm

# Optional: include Ollama in staging rebuild
bash provision/pct/containers/rebuild-staging.sh --with-ollama --confirm

# 3) Re-apply service configuration
make install SERVICE=all INV=inventory/staging
```

`rebuild-staging.sh` verifies that required staging data directories exist and are non-empty before any destructive step:

- `/var/lib/data-staging/postgres`
- `/var/lib/data-staging/redis`
- `/var/lib/data-staging/milvus`
- `/var/lib/data-staging/minio`
- `/var/lib/data-staging/neo4j`

It also verifies expected bind mounts after recreation for postgres, milvus, minio, neo4j, and redis.

## Post-Rebuild Step

After container rebuild, re-apply service configuration from the repository root:

```bash
# Production example
make install SERVICE=postgres

# Staging example
make install SERVICE=postgres INV=inventory/staging
```

Use the matching service name printed by the rebuild script for the rebuilt container.

## Notes

- `rebuild-container.sh` recreates via existing `create-*.sh` scripts.
- For multi-service scripts (for example `create-data-services.sh`), only missing containers are created. Existing containers are left in place.
- If a required host data directory is missing, run:

```bash
bash provision/pct/host/setup-proxmox-host.sh
```
