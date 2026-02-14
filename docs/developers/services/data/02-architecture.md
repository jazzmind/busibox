---
title: "Data Service Architecture"
category: "developer"
order: 61
description: "Document processing pipeline, chunking, multi-flow, ZFS storage"
published: true
---

# Data Service Architecture

## Pipeline Overview

Upload → MinIO + PostgreSQL metadata → Redis queue (`jobs:data`) → Worker (extract, chunk, embed) → Milvus + PostgreSQL.

See [architecture/04-ingestion](../architecture/04-ingestion.md) for the high-level pipeline. Key implementation details:

- **Chunking**: [chunking-implementation.md](chunking-implementation.md)
- **Multi-flow**: [multi-flow-implementation.md](multi-flow-implementation.md)
- **ZFS storage**: [zfs-storage-strategy.md](zfs-storage-strategy.md)

## Redis Stream

Default stream: `jobs:data` (configurable via `REDIS_STREAM`).

## Configuration

Authoritative config: `srv/data/src/shared/config.py`
