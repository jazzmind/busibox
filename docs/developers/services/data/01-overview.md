---
title: "Data Service Overview"
category: "developer"
order: 60
description: "Data API and worker - document upload, processing pipeline, embeddings"
published: true
---

# Data Service Overview

The Data service (`srv/data`) handles document upload, processing, embedding generation, and storage. It runs on `data-lxc` (ports 8002 API, 8005 Embedding API, 6379 Redis) and coordinates with MinIO, PostgreSQL, and Milvus.

## Key Capabilities

- **Upload** — Multipart upload, SHA-256 dedupe, visibility (personal/shared)
- **Processing pipeline** — Extraction → chunking → embedding → Milvus indexing
- **Embedding API** — FastEmbed text embeddings (port 8005)
- **Libraries** — Document libraries, triggers, app data

## Documentation

| Doc | Content |
|-----|---------|
| [02-architecture](02-architecture.md) | Pipeline, chunking, multi-flow, ZFS storage |
| [03-api](03-api.md) | REST API reference |
| [04-testing](04-testing.md) | How to run data service tests |
| [05-app-data-schemas](05-app-data-schemas.md) | Structured data documents, triggers |

## Quick Reference

- **Base URL**: `http://data-lxc:8002`
- **Auth**: JWT Bearer (audience `data-api`)
- **Redis stream**: `jobs:data` (default)
