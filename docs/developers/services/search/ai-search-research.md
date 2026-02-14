---
title: "AI Search Research"
category: "developer"
order: 70
description: "Research on hybrid search, multimodal indexing, and reranking for RAG"
published: true
---

1) TL;DR—recommended stack for you
	•	Keep Milvus for vectors (it now supports BM25 full-text + dense + sparse + multi-vector in one place). Use hybrid search (dense + BM25) and rerank.  ￼
	•	If you need analyzers/synonyms/aggregations/DSL and turn-key RRF, add OpenSearch and fuse there (or do app-level RRF).  ￼
	•	For PDFs with charts/tables/screenshots, embed page images with ColPali (late-interaction VLM) and keep your text pipeline too; fuse them.  ￼
	•	Use a cross-encoder reranker (e.g., bge-reranker-v2-m3) over the fused candidate set.  ￼

2) Ingest & indexing (multimodal)

Text & code
	•	Chunk smartly: 400–800 tokens with ~10–15% overlap; keep section/page IDs + source offsets.
	•	Store:
	•	Dense text embeddings (Milvus FLOAT_VECTOR)
	•	Sparse via BM25 function (Milvus SPARSE_FLOAT_VECTOR) or OpenSearch BM25.  ￼
	•	Optional: learned sparse (SPLADE / BGE-M3) if you need lexical recall without a full BM25 engine.  ￼

Images
	•	Generate CLIP/SigLIP embeddings for text↔image. Keep auto-captions (BLIP-2) as text fields for lexical match.  ￼

PDFs (mixed layout)
	•	Do both:
	1.	Parse to text/markdown (Unstructured/Marker + TATR for tables) for BM25/dense.  ￼
	2.	Page-image embeddings with ColPali (no OCR or chunking needed; late-interaction multi-vector).  ￼

Audio/Video
	•	Transcribe (Whisper or equivalent) → index transcript (dense + BM25). Optionally embed audio with CLAP for text↔audio search. For video, index keyframe CLIP + transcript segments.  ￼

Schema sketch (Milvus)
	•	One collection per corpus; fields like:
	•	id (PK), doc_id, page, modality, text, text_dense, text_sparse (BM25), image_dense, colpali_vectors (multi-vector), metadata.
	•	Milvus BM25 function and multi-vector are built-in now (2.6+).  ￼

2) Query pipeline (3 stages)

A) Candidate generation (fast)
	•	Run dense (semantic) and sparse (BM25 or learned sparse). For PDFs, also query ColPali page vectors. Return top-K from each (e.g., k=100 each).
	•	If using OpenSearch, configure hybrid search via search pipeline and fuse scores (min-max + weighted sum) or use RRF (2.19+) so you don’t worry about score normalization.  ￼
	•	If staying in Milvus-only, issue multi-field searches (text_dense, text_sparse, image_dense, colpali_vectors) and fuse in the app.  ￼

B) Fusion
	•	Start with Reciprocal Rank Fusion:
\text{RRF}(d)=\sum_{r \in \text{runs}} \frac{1}{k + \text{rank}_r(d)} with k\approx 60. It’s simple and robust across domains.  ￼

C) Rerank (accurate, slower)
	•	Use a cross-encoder reranker on the fused top ~200; keep top 20–40 for the LLM. bge-reranker-v2-m3 is a strong, lightweight default (multilingual).  ￼
	•	For token-level precision on long passages, consider a late-interaction retriever (ColBERTv2) if you need even more exactness before the LLM.  ￼
	•	Apply MMR on the final set to reduce near-duplicate chunks.  ￼

3) When to add OpenSearch (vs. Milvus-only)
	•	Stay Milvus-only if you want: one system for dense + BM25 + multimodal + multi-vector, and you don’t need complex analyzers, synonyms, aggregations, or query DSL. (Milvus exposes BM25 as a function and supports hybrid/multi-vector search.)  ￼
	•	Add OpenSearch if you need: per-field analyzers, synonyms, nested docs, aggregations, guardrail filters, and productionized hybrid with RRF right in the engine. (Use neural + match in a hybrid clause; combine via search pipelines.)  ￼

OpenSearch hybrid example (neural + BM25 + normalization):

PUT /_search/pipeline/nlp-search-pipeline
{
  "phase_results_processors": [
    {
      "normalization-processor": {
        "normalization": { "technique": "min_max" },
        "combination": { "technique": "arithmetic_mean", "parameters": { "weights": [0.4, 0.6] } }
      }
    }
  ]
}

GET /my-nlp-index/_search?search_pipeline=nlp-search-pipeline
{
  "query": {
    "hybrid": {
      "queries": [
        { "match": { "passage_text": { "query": "your query" } } },
        { "neural": { "passage_embedding": { "query_text": "your query", "model_id": "MODEL", "k": 100 } } }
      ]
    }
  }
}

￼

Milvus BM25 + multi-vector schema excerpt:

schema.add_field("text", DataType.VARCHAR, max_length=1000, enable_analyzer=True)
schema.add_field("text_dense", DataType.FLOAT_VECTOR, dim=768)
schema.add_field("text_sparse", DataType.SPARSE_FLOAT_VECTOR)
schema.add_field("image_dense", DataType.FLOAT_VECTOR, dim=512)
schema.add_function(Function(name="text_bm25_emb",
                             input_field_names=["text"],
                             output_field_names=["text_sparse"],
                             function_type=FunctionType.BM25))

￼

4) PDF specifics (where most RAG breaks)
	•	Dual-track indexing outperforms text-only: (a) high-quality parsing (Unstructured/Marker + TATR for tables) → BM25 + dense; (b) ColPali page embeddings capture charts, figures, and layout without OCR. Fuse both and rerank.  ￼

5) Query understanding & routing
	•	Classify queries to route (text, image, pdf page, audio/video) and select indexes.
	•	Expand with lightweight techniques (query synonyms in OpenSearch; optional SPLADE expansion).  ￼

6) Evaluation you can trust
	•	Build a harness (nDCG@10, Recall@k) over a held-out set; BEIR-style methodology reminds you hybrids win across diverse domains; there is no single silver bullet.  ￼
	•	Track latency budget: ~150–250 ms retrieval + 50–150 ms rerank on GPU is reachable with sane K.

7) Concrete next steps for your pipeline
	1.	Upgrade Milvus (≥2.6) and enable BM25 function; store dense + sparse + any image/page vectors in one collection; keep PG as the source of truth for metadata.  ￼
	2.	Turn on hybrid retrieval (dense + BM25) + bge-reranker-v2-m3. Start K=100 dense + 100 sparse → fuse (RRF) → rerank top 200 → keep 30.  ￼
	3.	Add ColPali for PDF pages (plus your current text chunks) and include it as a third leg in fusion.  ￼
	4.	If you need analyzers/aggregations/synonyms, wire OpenSearch with the hybrid search pipeline (or move everything there and keep Milvus for multi-vector heavy retrieval).  ￼
	5.	Add MMR before final context packaging to reduce redundancy.  ￼

8) References you’ll actually use
	•	OpenSearch hybrid + RRF: docs & blog (pipelines, normalization, rank-fusion).  ￼
	•	Milvus: full-text BM25, hybrid & multi-vector search.  ￼
	•	Rerankers: BGE reranker v2 m3 (fast, multilingual).  ￼
	•	Late-interaction: ColBERTv2 (token-level matching).  ￼
	•	PDF vision retrieval: ColPali (page-image retrieval that beats text-only pipelines).  ￼
	•	Multimodal foundations: CLIP/SigLIP, BLIP-2, ImageBind, CLAP.  ￼
	•	Why hybrid works broadly: BEIR benchmark takeaway (no single method dominates).  ￼


