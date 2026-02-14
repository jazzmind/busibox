---
title: "Search Service API"
category: "developer"
order: 72
description: "Search API endpoints and request/response formats"
published: true
---

# Search Service API

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| POST | `/search` | Main hybrid search |
| POST | `/search/keyword` | Keyword (BM25) only |
| POST | `/search/semantic` | Semantic (dense) only |
| POST | `/search/explain` | Explain query scoring |
| POST | `/search/graph` | Knowledge graph search |
| POST | `/search/graph/related` | Graph-RAG: entities related to results |
| POST | `/search/graph/path` | Paths between entities |

## Main Search (POST /search)

See [02-architecture](02-architecture.md#api-endpoints) for full request/response schemas.

**Auth**: JWT Bearer with audience `search-api`. Partition access is derived from JWT claims.

## OpenAPI

Full specification: [search-api-openapi.yaml](search-api-openapi.yaml)
