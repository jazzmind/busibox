---
title: "Search Service Architecture"
category: "developer"
order: 71
description: "FastAPI search service with keyword, semantic, hybrid search and reranking"
published: true
---

# Search Service Architecture

**Created**: 2025-11-17  
**Updated**: 2025-11-17  
**Status**: Active Development  
**Category**: Architecture

## Overview

The Search Service is a dedicated FastAPI application running in **milvus-lxc** (CTID 204) that provides sophisticated search capabilities including keyword search, semantic search, hybrid search with reranking, and result highlighting. It is the primary interface for RAG retrieval operations.

### Design Rationale

**Why separate from data-lxc:**
- **Separation of concerns**: Ingest handles file processing, Search handles retrieval
- **Performance isolation**: Heavy search traffic doesn't impact ingestion pipeline
- **Architectural clarity**: Search is primarily Milvus operations; colocating with Milvus reduces latency
- **Scalability**: Search service can be scaled independently

**Current state:**
- Basic hybrid search exists in `srv/data/src/api/routes/search.py`
- Only dense vector search implemented (BM25 mentioned but not fully utilized)
- No reranking, no highlighting, no semantic alignment visualization

**Target state:**
- Standalone search service in milvus-lxc
- True hybrid search (dense embeddings + BM25 sparse)
- Cross-encoder reranking
- Search term highlighting
- Semantic alignment scores and visualization
- Advanced filters and ranking options

---

## Architecture

### Service Location

- **Container**: milvus-lxc (CTID 204, IP 10.96.200.27)
- **Port**: 8003 (internal), exposed via reverse proxy
- **Service Name**: `search-api.service`
- **Code Location**: `srv/search/`

### Dependencies

```
search-api
├── Milvus (local, port 19530) - Vector search
├── PostgreSQL (pg-lxc) - File metadata
├── Redis (optional) - Query caching
└── Embedding service (litellm-lxc or local) - Query embedding
```

### Technology Stack

- **API Framework**: FastAPI (Python 3.11+)
- **Vector Store**: Milvus 2.5+ with BM25 functions
- **Embeddings**: text-embedding-3-small (OpenAI) or local alternatives
- **Reranking**: bge-reranker-v2-m3 (cross-encoder)
- **Highlighting**: Custom algorithm with token-level matching
- **Logging**: structlog with JSON output

---

## API Endpoints

### POST /search

**Purpose**: Main search endpoint with hybrid search and reranking

**Request:**
```json
{
  "query": "machine learning best practices",
  "mode": "hybrid",  // "keyword", "semantic", "hybrid"
  "limit": 10,
  "offset": 0,
  "rerank": true,
  "rerank_k": 100,
  "dense_weight": 0.7,
  "sparse_weight": 0.3,
  "filters": {
    "file_ids": ["uuid1", "uuid2"],
    "date_range": {"start": "2024-01-01", "end": "2024-12-31"},
    "metadata": {"department": "engineering"}
  },
  "highlight": {
    "enabled": true,
    "fragment_size": 200,
    "num_fragments": 3
  }
}
```

**Response:**
```json
{
  "query": "machine learning best practices",
  "mode": "hybrid",
  "total": 156,
  "limit": 10,
  "offset": 0,
  "execution_time_ms": 245,
  "results": [
    {
      "file_id": "uuid",
      "filename": "ml-guide.pdf",
      "chunk_index": 12,
      "page_number": 5,
      "text": "Full chunk text here...",
      "score": 0.89,
      "scores": {
        "dense": 0.85,
        "sparse": 0.78,
        "rerank": 0.89,
        "final": 0.89
      },
      "highlights": [
        {
          "fragment": "...best practices for <mark>machine learning</mark> include...",
          "score": 0.92,
          "start_offset": 120,
          "end_offset": 320
        }
      ],
      "semantic_alignment": {
        "query_tokens": ["machine", "learning", "best", "practices"],
        "alignment_scores": [0.91, 0.95, 0.82, 0.87],
        "matched_spans": [
          {"token": "machine learning", "start": 45, "end": 61, "score": 0.93}
        ]
      },
      "metadata": {
        "document_type": "pdf",
        "created_at": "2024-06-15",
        "department": "engineering"
      }
    }
  ]
}
```

### POST /search/keyword

**Purpose**: Pure BM25 keyword search (fast, exact matches)

**Use cases:**
- Finding specific terms, IDs, codes
- When semantic understanding is not needed
- Debugging/comparison with semantic search

### POST /search/semantic

**Purpose**: Pure dense vector search (semantic understanding)

**Use cases:**
- Conceptual queries
- When keyword matching might miss relevant content
- Cross-lingual search

### POST /search/mmr

**Purpose**: Search with Maximal Marginal Relevance to reduce redundancy

**Additional parameters:**
```json
{
  "lambda_param": 0.5,  // Diversity vs relevance (0=max diversity, 1=max relevance)
  "diversity_threshold": 0.85  // Cosine similarity threshold for duplicates
}
```

### GET /search/explain

**Purpose**: Explain why a document was retrieved for a query

**Request:**
```json
{
  "query": "machine learning",
  "file_id": "uuid",
  "chunk_index": 12
}
```

**Response:**
```json
{
  "query": "machine learning",
  "document": {
    "file_id": "uuid",
    "chunk_index": 12,
    "text": "..."
  },
  "explanation": {
    "dense_score": 0.85,
    "sparse_score": 0.78,
    "term_contributions": {
      "machine": 0.42,
      "learning": 0.36
    },
    "semantic_matches": [
      {"query_term": "machine", "doc_terms": ["algorithm", "model", "machine"], "scores": [0.6, 0.7, 1.0]},
      {"query_term": "learning", "doc_terms": ["training", "learning", "optimization"], "scores": [0.8, 1.0, 0.65]}
    ]
  }
}
```

### GET /health

**Purpose**: Health check for search service

**Response:**
```json
{
  "status": "healthy",
  "milvus": "connected",
  "postgres": "connected",
  "reranker": "loaded",
  "embedder": "available"
}
```

---

## Search Modes

### 1. Keyword Search (BM25)

**Algorithm**: BM25 (Best Match 25)

**How it works:**
1. Query is tokenized and analyzed
2. Milvus BM25 function computes relevance scores
3. Results ranked by BM25 score

**Strengths:**
- Fast (no embedding generation)
- Excellent for exact term matching
- Works well for IDs, names, specific phrases

**Weaknesses:**
- No semantic understanding
- Vocabulary mismatch issues
- No handling of synonyms

**When to use:**
- Searching for specific terms, codes, identifiers
- When speed is critical
- When user knows exact terminology

### 2. Semantic Search (Dense Vectors)

**Algorithm**: Cosine similarity on text-embedding-3-small embeddings

**How it works:**
1. Query is embedded using same model as documents
2. Milvus performs ANN search using HNSW index
3. Results ranked by cosine similarity

**Strengths:**
- Understands semantic meaning
- Handles synonyms and paraphrasing
- Cross-lingual capabilities
- Good for conceptual queries

**Weaknesses:**
- Can miss exact term matches
- Slower (embedding generation + ANN search)
- May retrieve semantically similar but irrelevant content

**When to use:**
- Conceptual questions
- When exact terminology is unknown
- Multi-language scenarios

### 3. Hybrid Search (Recommended Default)

**Algorithm**: Weighted combination of BM25 + dense vector search with RRF

**How it works:**
1. Query is both tokenized (BM25) and embedded (dense)
2. Two separate searches run in parallel
3. Results fused using Reciprocal Rank Fusion (RRF):
   ```
   RRF_score(d) = Σ(1 / (k + rank_i(d)))
   where k=60, rank_i is rank in search i
   ```
4. Optional: Cross-encoder reranking on top-K results

**Strengths:**
- Best of both worlds (precision + recall)
- Robust across different query types
- State-of-art retrieval performance

**Configuration:**
```python
# Dense-heavy (semantic understanding)
dense_weight = 0.7
sparse_weight = 0.3

# Balanced
dense_weight = 0.5
sparse_weight = 0.5

# Sparse-heavy (keyword precision)
dense_weight = 0.3
sparse_weight = 0.7
```

**When to use:**
- Default for most applications
- When query type is unknown
- For production RAG systems

---

## Reranking

### Cross-Encoder Reranking

**Model**: `BAAI/bge-reranker-v2-m3` (560M parameters)

**Why rerank:**
- Bi-encoders (used in retrieval) are fast but less accurate
- Cross-encoders see query + document together, more accurate
- Reranking top-K is fast enough for production

**Pipeline:**
```
1. Hybrid search retrieves top-100 candidates (fast)
2. Cross-encoder reranks top-100 → top-10 (accurate)
3. Return top-10 to user
```

**Performance:**
- Retrieval: ~50-100ms
- Reranking 100 docs: ~100-200ms on CPU, ~20-40ms on GPU
- Total: ~150-250ms

**Implementation:**
```python
from sentence_transformers import CrossEncoder

reranker = CrossEncoder('BAAI/bge-reranker-v2-m3')

# Get query-document pairs
pairs = [(query, doc['text']) for doc in candidates]

# Score all pairs
scores = reranker.predict(pairs)

# Rerank
reranked = sorted(zip(candidates, scores), 
                 key=lambda x: x[1], 
                 reverse=True)
```

**Alternative models:**
- `ms-marco-MiniLM-L-12-v2`: Faster, English-only
- `bge-reranker-large`: More accurate, slower
- `colbert-ir/colbertv2.0`: Token-level reranking

---

## Highlighting

### Search Term Highlighting

**Purpose**: Show users WHERE their query matches in the result

**Algorithm:**

1. **Tokenization**: Split query and document into tokens
2. **Fuzzy matching**: Match tokens with edit distance tolerance
3. **Span extraction**: Extract context around matches
4. **Markup**: Wrap matches in `<mark>` tags

**Features:**

- **Multi-term highlighting**: All query terms highlighted
- **Stemming**: Matches "running" when searching "run"
- **Context windows**: Show surrounding text for context
- **Fragment selection**: Choose best fragments if document is long

**Example:**
```
Query: "machine learning algorithms"

Highlight:
"...In this chapter, we explore various <mark>machine learning</mark> 
<mark>algorithms</mark> including decision trees, neural networks, and 
support vector machines. The choice of <mark>algorithm</mark> depends..."
```

**Configuration:**
```python
{
  "fragment_size": 200,      # Characters per fragment
  "num_fragments": 3,        # Max fragments to return
  "pre_tag": "<mark>",       # Opening tag
  "post_tag": "</mark>",     # Closing tag
  "fragment_delimiter": " ... "
}
```

### Semantic Alignment Visualization

**Purpose**: Show HOW semantically similar the query and result are

**Algorithm:**

1. **Token embeddings**: Generate embeddings for each token
2. **Alignment matrix**: Compute similarity between query and doc tokens
3. **Attention-style visualization**: Show which doc tokens align with query

**Output:**
```json
{
  "semantic_alignment": {
    "query_tokens": ["machine", "learning", "best", "practices"],
    "document_tokens": ["algorithm", "training", "optimal", "methods", "..."],
    "alignment_matrix": [
      [0.8, 0.3, 0.2, 0.4],  // "algorithm" similarity to query tokens
      [0.4, 0.9, 0.3, 0.2],  // "training" similarity to query tokens
      [0.3, 0.2, 0.85, 0.7], // "optimal" similarity to query tokens
      [0.2, 0.3, 0.7, 0.88]  // "methods" similarity to query tokens
    ],
    "matched_spans": [
      {
        "query_token": "machine",
        "doc_span": "algorithm",
        "score": 0.8,
        "start": 45,
        "end": 54
      },
      {
        "query_token": "learning",
        "doc_span": "training",
        "score": 0.9,
        "start": 120,
        "end": 128
      }
    ]
  }
}
```

**Frontend visualization:**
- Heatmap showing alignment strength
- Hovering over query term highlights aligned doc terms
- Color intensity indicates alignment strength

---

## Performance Optimization

### Caching Strategy

**Query cache (Redis):**
- Cache frequent queries for 5 minutes
- Key: `search:{user_id}:{query_hash}:{params_hash}`
- Invalidate on document upload/delete

**Embedding cache:**
- Cache query embeddings for 15 minutes
- Key: `embedding:{model}:{text_hash}`

### Indexing Strategy

**Milvus indexes:**
- Dense vectors: HNSW (M=16, efConstruction=256)
- Sparse vectors: INVERTED_INDEX
- Scalar fields: INDEX on user_id, file_id

**Performance targets:**
- P50: < 150ms
- P95: < 300ms
- P99: < 500ms

### Batching

For multiple queries:
```
POST /search/batch
{
  "queries": ["query1", "query2", ...],
  "mode": "hybrid",
  ...
}
```

Batch embedding generation and search for efficiency.

---

## Security & Access Control

### Authentication

- JWT token in `Authorization: Bearer <token>` header
- User ID extracted from token
- All searches scoped to user's documents

### Row-Level Security

Milvus filter expression:
```python
expr = f'user_id == "{user_id}"'
```

Ensures users only search their own documents.

### Rate Limiting

- 100 requests per minute per user (standard)
- 1000 requests per minute per user (premium)
- Enforced at nginx reverse proxy level

---

## Monitoring & Observability

### Metrics

**Search metrics:**
- `search_requests_total{mode, status}`
- `search_latency_seconds{mode, percentile}`
- `search_results_returned{mode}`
- `reranking_latency_seconds`

**Resource metrics:**
- `milvus_connections_active`
- `embedding_generation_seconds`
- `cache_hit_rate{cache_type}`

### Logging

**Structured logs (JSON):**
```json
{
  "timestamp": "2024-11-17T10:30:45Z",
  "level": "info",
  "event": "search_completed",
  "user_id": "uuid",
  "query": "machine learning",
  "mode": "hybrid",
  "results": 10,
  "execution_time_ms": 245,
  "cache_hit": false
}
```

### Tracing

OpenTelemetry spans:
- `search_request`: Overall request
- `embed_query`: Embedding generation
- `milvus_search`: Vector search
- `rerank`: Reranking operation
- `highlight`: Highlighting generation

---

## Migration Strategy

### Phase 1: Parallel Deployment

1. Deploy new search service to milvus-lxc
2. Keep existing search in data-lxc operational
3. Route 10% of traffic to new service
4. Compare results and performance

### Phase 2: Feature Parity

1. Implement all existing features
2. Add new features (reranking, highlighting)
3. Increase traffic to 50%

### Phase 3: Full Migration

1. Route 100% traffic to new service
2. Remove search endpoint from data-lxc
3. Update documentation

### Rollback Plan

- Keep old service operational for 2 weeks
- Feature flag to switch back if needed
- Monitoring alerts for degraded performance

---

## Development Roadmap

### MVP (Week 1)
- [x] Architecture design
- [ ] Basic FastAPI service
- [ ] Hybrid search implementation
- [ ] Health check endpoint
- [ ] Ansible deployment role

### V1.0 (Week 2)
- [ ] Reranking with bge-reranker-v2-m3
- [ ] Basic keyword highlighting
- [ ] Query caching
- [ ] Performance optimization

### V1.1 (Week 3)
- [ ] Semantic alignment visualization
- [ ] MMR diversity
- [ ] Explain endpoint
- [ ] Advanced filters

### V2.0 (Future)
- [ ] Multi-modal search (text + images)
- [ ] ColPali page-level search
- [ ] Query expansion
- [ ] Learning to rank

---

## References

- **Hybrid Search**: [Milvus Hybrid Search Guide](https://milvus.io/docs/hybrid_search.md)
- **Reranking**: [BGE Reranker](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- **RRF**: [Reciprocal Rank Fusion Paper](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)
- **BM25**: [Milvus BM25 Function](https://milvus.io/docs/full_text_search.md)
- **Architecture Inspiration**: `docs/architecture/ai-search.md`

