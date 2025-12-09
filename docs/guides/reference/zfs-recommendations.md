# ZFS Storage Recommendations - Quick Reference

## TL;DR - What Should Use Dedicated ZFS Storage?

### ✅ YES - Use Dedicated ZFS Datasets

| Service | Why | Priority |
|---------|-----|----------|
| **PostgreSQL** (`/var/lib/postgresql/data`) | Database files - independent snapshots, compression saves 30-50% | **HIGH** |
| **MinIO** (`/srv/minio/data`) | Object storage - large files, need compression & quotas | **HIGH** |
| **Milvus** (`/srv/milvus/data`) | Vector DB - heavy I/O, benefits from ZFS tuning | **HIGH** |
| **LLM Models** (`/var/lib/llm-models`) | Already implemented ✓ - large files, share across containers | **DONE** ✓ |

### ❌ NO - Keep in Container Storage

| Location | Why | Alternatives |
|----------|-----|--------------|
| **Application Code** (`/srv/apps`, `/srv/agent`) | Deployed from Git - no benefit to ZFS snapshots | Use Git branches & tags |
| **Container Rootfs** | LXC snapshots work fine - rebuilding is cheap | `pct snapshot` |
| **Temp/Cache Dirs** | Ephemeral data - no need for persistence | Let containers handle it |
| **Build Artifacts** (`.mastra/.build`) | Generated code - can be rebuilt | Rebuild from source |

---

## Detailed Decision Matrix

### Use Cases FOR ZFS Datasets

✅ **Database files** - PostgreSQL, MySQL, etc.
- Independent snapshots without stopping container
- Compression saves significant space
- Can migrate between hosts easily
- Roll back bad migrations

✅ **Object storage** - MinIO, S3-compatible stores  
- Large files benefit from 1M record size
- Compression on images/PDFs
- Quotas prevent disk exhaustion

✅ **Vector databases** - Milvus, Weaviate, Qdrant
- Heavy I/O patterns benefit from ZFS ARC cache tuning
- Large datasets need compression
- Snapshots before schema changes

✅ **LLM models** - Ollama, vLLM, HuggingFace cache
- Huge files (10-50GB per model)
- Share across multiple containers
- Download once, use everywhere

✅ **User uploads** - Document storage, file shares
- Need for snapshots and backups
- Quotas to prevent abuse
- Compression on PDFs/Office docs

### Use Cases AGAINST ZFS Datasets

❌ **Application code** from Git repos
- **Why not**: Git is already version control
- **Alternative**: Use Git tags, branches, container snapshots
- **Exception**: If you make manual edits in production (don't do this!)

❌ **Node.js `node_modules/`** or Python `venv/`
- **Why not**: Generated from lock files, can rebuild
- **Alternative**: Store lock files in Git, rebuild on deploy
- **Exception**: Extremely long build times (>10 min)

❌ **Build artifacts** (`.next/`, `dist/`, `.mastra/.build`)
- **Why not**: Generated code, version controlled in CI/CD
- **Alternative**: Rebuild from source
- **Exception**: Very expensive build processes

❌ **Logs** (unless you need long retention)
- **Why not**: Log aggregation systems handle this better
- **Alternative**: Ship to Loki/Elasticsearch, rotate locally
- **Exception**: Compliance requirements for 7+ years

❌ **Container rootfs** (OS, packages, configs)
- **Why not**: LXC already has snapshot support via ZFS
- **Alternative**: `pct snapshot 203 pre-upgrade`, Ansible for config
- **Exception**: You're not using ZFS for container storage

---

## Implementation Priority

### Phase 1: Critical Data (Do Now)
1. **PostgreSQL** - Most important, easiest migration
2. **MinIO** - User data, file uploads
3. **Milvus** - Vector embeddings, hard to recreate

### Phase 2: Performance (Do Next)
4. **LLM Models** - Already done ✓
5. **Automated snapshots** - Daily backups
6. **Remote replication** - Disaster recovery

### Phase 3: Nice to Have (Optional)
7. **Monitoring** - Grafana dashboards for ZFS metrics
8. **Alerting** - Email on quota warnings, snapshot failures

---

## Storage Size Planning

Based on your infrastructure:

| Dataset | Typical Size | With Compression | Quota Recommendation |
|---------|--------------|-------------------|---------------------|
| `rpool/data/postgres` | 5-20 GB | 3-12 GB (40% savings) | 100 GB |
| `rpool/data/minio` | 50-500 GB | 30-350 GB (30% on docs) | 500 GB |
| `rpool/data/milvus` | 10-100 GB | 7-70 GB (30% savings) | 200 GB |
| `rpool/llm-models` | 50-500 GB | 40-450 GB (10-20% savings) | 1 TB |
| **Total** | **115-1120 GB** | **80-882 GB** | **1.8 TB** |

**Recommendation**: Plan for 1-2 TB dedicated to data datasets on your ZFS pool.

---

## Quick Commands

### Create All Datasets
```bash
bash scripts/setup-zfs-storage.sh
```

### Check Usage
```bash
zfs list -o name,used,avail,compressratio,mountpoint rpool/data
```

### Snapshot All Data
```bash
for ds in postgres minio milvus; do
    zfs snapshot rpool/data/${ds}@$(date +%Y%m%d-%H%M)
done
```

### List Snapshots
```bash
zfs list -t snapshot rpool/data/postgres
```

### Restore from Snapshot (read-only view)
```bash
ls /var/lib/data/postgres/.zfs/snapshot/20251022-0200/
```

### Roll Back (DESTRUCTIVE)
```bash
# Stop service first!
pct exec 203 -- systemctl stop postgresql
zfs rollback rpool/data/postgres@20251022-0200
pct exec 203 -- systemctl start postgresql
```

---

## Common Questions

### Q: Should I move `/srv/apps` to ZFS?
**A**: No. Application code is version controlled in Git. Use Git tags and container snapshots instead.

### Q: Should I snapshot the entire container?
**A**: For most containers, yes! Use `pct snapshot 203`. But for data-heavy containers (Postgres, MinIO), separate datasets give you better control.

### Q: Can I use both container snapshots AND dataset snapshots?
**A**: Yes! Container snapshots for OS/config, dataset snapshots for data. Best of both worlds.

### Q: How much does compression save?
**A**: 
- Databases: 30-50% (lots of repeated patterns)
- Documents/PDFs: 20-40% 
- Images/Videos: 0-10% (already compressed)
- LLM Models: 10-20% (binary data, some patterns)

### Q: Does ZFS compression slow things down?
**A**: No! LZ4 compression is so fast it often *improves* performance by reducing disk I/O.

### Q: Should I use deduplication?
**A**: **Usually no**. Dedup uses lots of RAM (5GB per TB of data). Stick with compression.

---

## Summary

**Do This:**
- ✅ Move PostgreSQL, MinIO, Milvus to dedicated datasets
- ✅ Set up automated daily snapshots  
- ✅ Use LLM models on dedicated storage (already done)

**Don't Do This:**
- ❌ Move application code to ZFS (use Git)
- ❌ Move build artifacts to ZFS (rebuild them)
- ❌ Enable deduplication (too much RAM overhead)

**Result:**
- 📸 Independent snapshots per service
- 💾 30-50% disk space savings
- 🚀 Easy container rebuilds without data loss
- 📦 Better resource management with quotas

---

**See Also:**
- [ZFS_STORAGE_STRATEGY.md](./ZFS_STORAGE_STRATEGY.md) - Full implementation guide
- [../scripts/setup-zfs-storage.sh](../scripts/setup-zfs-storage.sh) - Automated setup script

**Created**: 2025-10-22  
**Last Updated**: 2025-10-22

