---
title: "ZFS Storage Strategy"
category: "developer"
order: 64
description: "Recommended ZFS storage architecture for production Busibox deployments"
published: true
---

# ZFS Storage Strategy for Busibox Infrastructure

## Overview

This document outlines the recommended ZFS storage architecture for production deployments of the Busibox infrastructure.

## Current State

All containers use `local-zfs` for their rootfs (20GB default). Data is stored within containers:
- PostgreSQL: `/var/lib/postgresql/` (in pg-lxc container)
- MinIO: `/srv/minio/data` (in files-lxc container) 
- Milvus: `/srv/milvus/data` (in milvus-lxc container)
- LLM Models: **✓ Using dedicated host storage** `/var/lib/llm-models/` (bind-mounted)

**Status**: Data persists with container snapshots, but not optimal for production.

---

## Recommended Architecture

### Phase 1: Critical Data Migration (High Priority)

Move database and storage files to dedicated ZFS datasets on the Proxmox host.

#### Benefits
- ✅ **Snapshot independently** - Back up databases without entire container
- ✅ **Rebuild containers safely** - Upgrade/rebuild without data loss
- ✅ **Better performance** - Tune ZFS parameters per workload
- ✅ **Easier migration** - `zfs send/receive` for moving data between hosts
- ✅ **Compression** - Save 30-50% disk space automatically
- ✅ **Quotas** - Prevent runaway disk usage

---

### Implementation Plan

#### 1. Create ZFS Datasets (On Proxmox Host)

```bash
# Create parent dataset for all persistent data
zfs create rpool/data

# Create datasets for each service
zfs create rpool/data/postgres
zfs create rpool/data/minio
zfs create rpool/data/milvus

# Set mount points
zfs set mountpoint=/var/lib/data/postgres rpool/data/postgres
zfs set mountpoint=/var/lib/data/minio rpool/data/minio
zfs set mountpoint=/var/lib/data/milvus rpool/data/milvus

# Enable compression (saves 30-50% space)
zfs set compression=lz4 rpool/data/postgres
zfs set compression=lz4 rpool/data/minio
zfs set compression=lz4 rpool/data/milvus

# Set recommended quotas
zfs set quota=100G rpool/data/postgres   # Adjust based on your needs
zfs set quota=500G rpool/data/minio      # Adjust based on your needs
zfs set quota=200G rpool/data/milvus     # Adjust based on your needs

# PostgreSQL-specific tuning
zfs set recordsize=8K rpool/data/postgres      # Match PostgreSQL page size
zfs set logbias=latency rpool/data/postgres    # Optimize for database workload

# MinIO-specific tuning (large objects)
zfs set recordsize=1M rpool/data/minio         # Optimize for large files
zfs set logbias=throughput rpool/data/minio    # Optimize for throughput

# Milvus-specific tuning
zfs set recordsize=128K rpool/data/milvus      # Good for vector data
zfs set primarycache=metadata rpool/data/milvus # Save RAM, Milvus has its own cache
```

#### 2. Add Bind Mounts to Containers

Edit container configs on Proxmox host:

**PostgreSQL Container** (`/etc/pve/lxc/203.conf`):
```bash
# Add to bottom of file
mp0: /var/lib/data/postgres,mp=/var/lib/postgresql/data
```

**MinIO Container** (`/etc/pve/lxc/205.conf`):
```bash
# Add to bottom of file  
mp0: /var/lib/data/minio,mp=/srv/minio/data
```

**Milvus Container** (`/etc/pve/lxc/204.conf`):
```bash
# Add to bottom of file
mp0: /var/lib/data/milvus,mp=/srv/milvus/data
```

#### 3. Migration Process (Zero Downtime)

For each service:

```bash
# Example: PostgreSQL migration
# 1. Stop the service
pct exec 203 -- systemctl stop postgresql

# 2. Copy existing data to host dataset
pct exec 203 -- bash -c "cd /var/lib/postgresql && tar czf /tmp/pg_backup.tar.gz ."
pct pull 203 /tmp/pg_backup.tar.gz /tmp/pg_backup.tar.gz
tar xzf /tmp/pg_backup.tar.gz -C /var/lib/data/postgres/

# 3. Stop container, add bind mount, restart
pct stop 203
echo "mp0: /var/lib/data/postgres,mp=/var/lib/postgresql/data" >> /etc/pve/lxc/203.conf
pct start 203

# 4. Verify and clean up
pct exec 203 -- systemctl start postgresql
pct exec 203 -- systemctl status postgresql
rm /tmp/pg_backup.tar.gz
```

Repeat for MinIO (container 205) and Milvus (container 204).

---

### Phase 2: Automated Snapshots & Backups

#### Daily Snapshots

```bash
# Create snapshot script
cat > /usr/local/bin/zfs-snapshot-data.sh << 'EOF'
#!/bin/bash
# Daily snapshots with 7-day retention

TIMESTAMP=$(date +%Y%m%d-%H%M)

# Snapshot each dataset
zfs snapshot rpool/data/postgres@daily-${TIMESTAMP}
zfs snapshot rpool/data/minio@daily-${TIMESTAMP}
zfs snapshot rpool/data/milvus@daily-${TIMESTAMP}
zfs snapshot rpool/llm-models@daily-${TIMESTAMP}

# Delete snapshots older than 7 days
for dataset in postgres minio milvus llm-models; do
    zfs list -t snapshot -o name -s creation rpool/data/${dataset} | \
    grep '@daily-' | head -n -7 | xargs -n 1 zfs destroy
done

echo "$(date): Snapshots created and old snapshots pruned"
EOF

chmod +x /usr/local/bin/zfs-snapshot-data.sh

# Add to crontab (daily at 2 AM)
(crontab -l 2>/dev/null; echo "0 2 * * * /usr/local/bin/zfs-snapshot-data.sh >> /var/log/zfs-snapshots.log 2>&1") | crontab -
```

#### Remote Backups (Optional)

```bash
# Send snapshots to remote ZFS host
zfs send rpool/data/postgres@daily-20251022 | \
    ssh backup-host zfs receive backups/postgres@daily-20251022

# Incremental backups (much faster)
zfs send -i @daily-20251021 rpool/data/postgres@daily-20251022 | \
    ssh backup-host zfs receive backups/postgres@daily-20251022
```

---

### Phase 3: Optional - Application Code Storage

**NOT RECOMMENDED** for most cases, but here's the analysis:

#### ❌ Why NOT to Use ZFS for `/srv/apps` or `/srv/agent`

1. **No benefit**: Application code is deployed from GitHub releases
2. **Complicates deployments**: Bind mounts interfere with `deploywatch`
3. **Git is already version control**: No need for ZFS snapshots
4. **Container snapshots work fine**: Entire container is cheap to rebuild

#### ✅ ONLY do this if:
- You make lots of manual changes to deployed code
- You need to roll back code frequently
- You want to share one codebase across multiple test containers

**Implementation** (if needed):
```bash
zfs create rpool/code
zfs create rpool/code/agent
zfs create rpool/code/apps

zfs set mountpoint=/var/lib/code/agent rpool/code/agent
zfs set mountpoint=/var/lib/code/apps rpool/code/apps

# Add to agent-lxc config (202)
mp1: /var/lib/code/agent,mp=/srv/agent

# Add to apps-lxc config (201)  
mp1: /var/lib/code/apps,mp=/srv/apps
```

**Recommendation**: **Skip this.** Use Git branches and container snapshots instead.

---

## Monitoring & Maintenance

### Check Dataset Usage
```bash
zfs list -o name,used,avail,refer,mountpoint rpool/data
```

### Check Compression Ratio
```bash
zfs get compressratio rpool/data/postgres
zfs get compressratio rpool/data/minio
```

### List Snapshots
```bash
zfs list -t snapshot rpool/data/postgres
```

### Restore from Snapshot
```bash
# View files from a snapshot (read-only)
ls /var/lib/data/postgres/.zfs/snapshot/daily-20251022/

# Roll back entire dataset (DESTRUCTIVE - loses all changes after snapshot)
zfs rollback rpool/data/postgres@daily-20251022

# Clone snapshot to new dataset (non-destructive)
zfs clone rpool/data/postgres@daily-20251022 rpool/data/postgres-test
```

---

## Summary

### Do This (Phase 1)
✅ **PostgreSQL** - Dedicated dataset (`rpool/data/postgres`)  
✅ **MinIO** - Dedicated dataset (`rpool/data/minio`)  
✅ **Milvus** - Dedicated dataset (`rpool/data/milvus`)  
✅ **LLM Models** - Already using `rpool/llm-models` ✓

### Maybe Later (Phase 2)
⏰ Automated snapshots  
⏰ Remote backups  

### Skip This
❌ Application code in ZFS (use Git instead)  
❌ Container rootfs on separate datasets (container snapshots work fine)

---

## Next Steps

1. Run the Phase 1 implementation on your Proxmox host
2. Test with one service (e.g., PostgreSQL) before migrating all
3. Verify services work after migration
4. Set up automated snapshots (Phase 2)
5. Document your snapshot/restore procedures

---

## Automation Script

Create a script to automate the setup:

```bash
# File: scripts/setup-zfs-storage.sh
#!/bin/bash
set -e

echo "Setting up ZFS storage for Busibox infrastructure..."

# Check if running on Proxmox host
if ! command -v zfs &> /dev/null; then
    echo "ERROR: ZFS not found. Run this on the Proxmox host."
    exit 1
fi

# Create datasets
echo "Creating ZFS datasets..."
zfs create -p rpool/data/postgres
zfs create -p rpool/data/minio
zfs create -p rpool/data/milvus

# Set mount points
echo "Configuring mount points..."
zfs set mountpoint=/var/lib/data/postgres rpool/data/postgres
zfs set mountpoint=/var/lib/data/minio rpool/data/minio
zfs set mountpoint=/var/lib/data/milvus rpool/data/milvus

# Enable compression
echo "Enabling compression..."
zfs set compression=lz4 rpool/data/postgres
zfs set compression=lz4 rpool/data/minio
zfs set compression=lz4 rpool/data/milvus

# Tune for workloads
echo "Applying workload-specific tuning..."
zfs set recordsize=8K rpool/data/postgres
zfs set logbias=latency rpool/data/postgres

zfs set recordsize=1M rpool/data/minio
zfs set logbias=throughput rpool/data/minio

zfs set recordsize=128K rpool/data/milvus
zfs set primarycache=metadata rpool/data/milvus

# Set quotas (optional - comment out if not needed)
# zfs set quota=100G rpool/data/postgres
# zfs set quota=500G rpool/data/minio
# zfs set quota=200G rpool/data/milvus

echo "✓ ZFS datasets created successfully!"
echo ""
echo "Next steps:"
echo "1. Stop services in containers"
echo "2. Copy existing data to new datasets"
echo "3. Add bind mounts to container configs"
echo "4. Restart containers"
echo ""
echo "See docs/ZFS_STORAGE_STRATEGY.md for detailed migration steps."
```

---

**File**: `busibox/docs/ZFS_STORAGE_STRATEGY.md`  
**Created**: 2025-10-22  
**Author**: AI Architect  

