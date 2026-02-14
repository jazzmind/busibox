---
title: "Data API Reference"
category: "developer"
order: 62
description: "Data API REST endpoints"
published: true
---

# Data API Reference

**Base URL**: `http://data-lxc:8002`  
**Auth**: JWT Bearer (audience `data-api`)

## Key Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/upload` | Multipart upload; dedupe via SHA-256; visibility, roles |
| GET | `/status/{fileId}` | SSE status stream |
| GET | `/files/{fileId}` | Metadata lookup |
| GET | `/files/{fileId}/markdown` | Retrieved extracted markdown |
| DELETE | `/files/{fileId}` | Delete file |
| POST | `/files/{fileId}/roles` | Share/unshare with roles |
| POST | `/files/{fileId}/search` | Search within a single document |
| POST | `/api/embeddings` | Text embedding (FastEmbed) |
| GET | `/health` | Health check |

**Note**: Cross-document hybrid search is provided by the Search API (`srv/search`), not the Data API.
