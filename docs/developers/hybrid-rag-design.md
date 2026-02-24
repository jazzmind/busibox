---
title: Hybrid RAG Design
category: developers
order: 15
description: Design document for hybrid retrieval augmented generation using graph + vector search
published: true
---

# Hybrid RAG Design

This document describes the phased approach to implementing hybrid retrieval augmented generation (RAG) that combines Milvus vector/keyword search with Neo4j graph traversal.

## Current State (Tier 1 - Implemented)

### Schema-Driven Entity Extraction

Entity extraction is no longer automatic during the document processing pipeline. Instead, it is **schema-driven and on-demand**:

1. A user selects or generates an extraction schema with fields tagged `search: ["graph"]`
2. The agent API `/extract` endpoint runs schema extraction and stores records
3. After extraction, `POST /data/graph/from-extraction` creates Neo4j entities from graph-tagged fields
4. Entity types are normalized to canonical labels (Person, Organization, Technology, Location, Keyword, Concept, Project)
5. Entities are linked to documents via `MENTIONED_IN` relationships and to each other via `CO_OCCURS_WITH`

### Graph Context in Agent Search

The `document_search` agent tool now passes `expand_graph=True` to the search API. After vector/keyword retrieval:

1. The search service calls `GraphSearchService.expand_context()` with the result document IDs
2. Neo4j returns entities mentioned in those documents and related documents not in the initial results
3. The graph context includes entity types, mention counts, and shared-entity counts between documents
4. This context is appended to the search results as `graph_context` and included in the LLM's context window

### Default Entity Extraction Schemas

Built-in schemas are available via `POST /data/seed-default-schemas`:
- **General Entity Extraction**: People, Organizations, Technologies, Locations, Keywords, Concepts (all `graph`-tagged)
- **People & Organizations**: Focused extraction with role and context fields

---

## Tier 2: Graph-Informed Search (Planned)

### 2.1 Query-Side Entity Extraction

**Goal**: Identify entities in the user's query to enable graph-first retrieval.

**Approach**:
- Before vector search, run a lightweight entity extraction on the query text
- Use a small local LLM (e.g., the `fast` purpose model) with a constrained prompt
- Extract entity names and types from the query
- Look up matching entities in Neo4j

**Implementation**:
- Add `QueryEntityExtractor` to `srv/search/src/services/`
- Extract entities from the query using a fast LLM call with structured output
- Look up entities in Neo4j by name (case-insensitive fuzzy match)
- Return matched entity IDs and their document connections

**Example**:
```
Query: "What did Alice say about the Acme compliance audit?"
Extracted: Alice (Person), Acme (Organization), compliance audit (Keyword)
Neo4j lookup: Alice -> mentioned in docs [A, B, C]; Acme -> mentioned in docs [A, D, E]
Intersection: doc A mentions both Alice and Acme
```

### 2.2 Entity Embeddings

**Goal**: Store entity-level embeddings in Neo4j for semantic entity matching.

**Approach**:
- When creating graph entities from extraction, also generate an embedding for each entity
- Store embeddings as properties on Neo4j nodes
- Enable semantic similarity between entities (not just name matching)

**Implementation**:
- Extend `POST /data/graph/from-extraction` to optionally generate embeddings
- Use the same embedding model as document chunks (via the embedding service)
- Store as `embedding` property on `GraphNode` nodes
- Add a Neo4j vector index for entity embeddings
- Expose `find_similar_entities(embedding, threshold)` in `GraphSearchService`

**Benefits**:
- "Find documents about machine learning" can match entities named "AI", "deep learning", "neural networks"
- Entity deduplication via embedding similarity

### 2.3 Graph-Informed Reranking

**Goal**: Use graph relationships to boost relevance of search results.

**Approach**:
- After initial vector/keyword retrieval and reranking, apply a graph-based score boost
- Documents connected to query-extracted entities get a relevance boost
- Documents sharing more entities with high-scoring results get a co-occurrence boost

**Implementation**:
- Add `graph_rerank(results, query_entities, boost_factor)` to `GraphSearchService`
- For each result document, count entity overlaps with query entities
- Apply multiplicative boost: `final_score = base_score * (1 + boost_factor * entity_overlap_ratio)`
- Configurable boost factor (default: 0.2 = 20% max boost)

**Scoring formula**:
```
entity_overlap = count of query entities also mentioned in this document
max_possible = total query entities found in graph
overlap_ratio = entity_overlap / max_possible
final_score = base_score * (1 + 0.2 * overlap_ratio)
```

---

## Tier 3: Unified Hybrid Search (Planned)

### 3.1 Classification-Driven Retrieval Routing

**Goal**: Automatically choose the optimal retrieval strategy based on query characteristics.

**Approach**:
- Classify queries into categories that benefit from different retrieval strategies:
  - **Factual/Entity**: "Who is the CEO of Acme?" -> Graph-first, then vector for context
  - **Conceptual/Semantic**: "Explain our data retention policy" -> Vector-first with graph expansion
  - **Relational**: "What connects Alice to the Q3 report?" -> Graph traversal primary
  - **Broad/Exploratory**: "Summarize recent compliance changes" -> Hybrid with equal weighting

**Implementation**:
- Add `QueryClassifier` service using the `classify` purpose model
- Classify into: `entity_lookup`, `semantic_search`, `relationship_query`, `exploratory`
- Each category maps to a retrieval strategy with different weights for vector vs graph
- The search orchestrator applies the strategy transparently

### 3.2 Unified Search Orchestrator

**Goal**: Replace the current sequential search + optional graph expansion with a parallel, strategy-driven orchestrator.

**Approach**:
- Run vector search and graph search in parallel
- Merge results using Reciprocal Rank Fusion (RRF) with strategy-driven weights
- Deduplicate by document ID, keeping the highest-scoring occurrence

**Architecture**:
```
Query -> QueryClassifier -> Strategy Selection
                |
                v
    +-----------+-----------+
    |                       |
    v                       v
Vector Search           Graph Search
(Milvus)               (Neo4j)
    |                       |
    v                       v
    +--------> RRF <--------+
                |
                v
         Reranker (optional)
                |
                v
         Final Results + Graph Context
```

**Implementation**:
- New `HybridSearchOrchestrator` in `srv/search/src/services/`
- Accepts `SearchRequest` and returns unified `SearchResponse`
- Internally dispatches to `MilvusSearchService` and `GraphSearchService` in parallel
- Merges using RRF: `score = sum(1 / (k + rank_i))` for each ranker
- Strategy weights: `final_score = alpha * vector_rrf + (1-alpha) * graph_rrf`
- Alpha values per strategy: entity_lookup=0.3, semantic=0.8, relationship=0.2, exploratory=0.5

### 3.3 Entity-Centric Search Mode

**Goal**: Enable direct entity-based retrieval where users can search by entity relationships.

**Approach**:
- New search mode: `mode=entity` alongside existing `hybrid`, `semantic`, `keyword`
- Searches start from entity nodes and traverse to documents
- Supports filters like entity type, relationship type, depth

**API**:
```json
POST /search
{
  "query": "Alice",
  "mode": "entity",
  "filters": {
    "entity_type": "Person",
    "relationship_depth": 2
  }
}
```

**Returns**: Documents connected to matched entities, ordered by relationship strength (number of shared entities, co-occurrence count).

---

## Implementation Priority

| Item | Complexity | Impact | Dependencies |
|------|-----------|--------|-------------|
| 2.1 Query Entity Extraction | Medium | High | Fast LLM model available |
| 2.2 Entity Embeddings | Medium | Medium | Embedding service, Neo4j vector index |
| 2.3 Graph-Informed Reranking | Low | Medium | 2.1 |
| 3.1 Query Classification | Medium | High | `classify` purpose model |
| 3.2 Unified Orchestrator | High | High | 2.1, 2.3, 3.1 |
| 3.3 Entity-Centric Search | Medium | Medium | 2.2 |

**Recommended order**: 2.1 -> 2.3 -> 2.2 -> 3.1 -> 3.2 -> 3.3

## Performance Considerations

- **Latency**: Graph queries add ~50-100ms. Running in parallel with vector search minimizes impact.
- **Entity extraction on queries**: Use the `fast` model with structured output to keep under 200ms.
- **Entity embedding generation**: Batch during extraction, not on every search.
- **Neo4j scaling**: Entity count per user is typically 100s-1000s, well within single-instance capacity.
- **Caching**: Cache entity extraction results for repeated/similar queries using Redis.

## Related Files

- `srv/search/src/services/graph_search.py` - Graph search service (Tier 1 implemented)
- `srv/search/src/api/routes/search.py` - Search API with `expand_graph` parameter
- `srv/data/src/api/routes/graph.py` - Graph entity management (`/from-extraction` endpoint)
- `srv/data/src/services/graph_service.py` - Neo4j CRUD operations
- `srv/agent/app/tools/document_search_tool.py` - Agent RAG tool with graph context
- `srv/agent/app/clients/busibox.py` - BusiboxClient with `expand_graph` support
