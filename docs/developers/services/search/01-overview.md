---
title: "Search Service Overview"
category: "developer"
order: 70
description: "Search API - hybrid search, reranking, partition-based access control"
published: true
---

# Search Service Overview

The Search API (`srv/search`) provides hybrid search (dense + BM25), optional reranking, and partition-based access control. It runs on `milvus-lxc` (port 8003) alongside Milvus.

## Key Capabilities

- **Hybrid search** — Semantic (dense vectors) + keyword (BM25)
- **Partition-aware** — Personal and role partitions from JWT
- **Reranking** — Optional cross-encoder via LiteLLM
- **Highlighting** — Search term highlighting in results

## Documentation

| Doc | Content |
|-----|---------|
| [02-architecture](02-architecture.md) | Service design, technology stack |
| [03-api](03-api.md) | API endpoints and OpenAPI spec |
| [ai-search-research](ai-search-research.md) | Research on multimodal indexing and reranking |
| [04-testing](04-testing.md) | How to run search tests |

## Quick Reference

- **Base URL**: `http://milvus-lxc:8003`
- **Auth**: JWT Bearer (audience `search-api`)
- **Main endpoint**: `POST /search`
