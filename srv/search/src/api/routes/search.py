"""
Search API routes.
"""

import time
import asyncpg
import structlog
from fastapi import APIRouter, HTTPException, Request
from typing import Dict

from shared.config import config
from shared.schemas import (
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchScores,
    HighlightFragment,
    SemanticAlignment,
    MatchedSpan,
    MMRSearchRequest,
    ExplainRequest,
    ExplainResponse,
)
from services.milvus_search import MilvusSearchService
from services.embedder import EmbeddingService
from services.reranker import RerankingService
from services.highlighter import HighlightingService
from services.semantic_alignment import SemanticAlignmentService

logger = structlog.get_logger()

router = APIRouter()

# Initialize services
milvus_service = MilvusSearchService(config.to_dict())
embedding_service = EmbeddingService(config.to_dict())
reranking_service = RerankingService(config.to_dict())
highlighting_service = HighlightingService(config.to_dict())
alignment_service = SemanticAlignmentService(config.to_dict())


@router.post("", response_model=SearchResponse)
async def search(
    search_request: SearchRequest,
    request: Request,
):
    """
    Main search endpoint with multiple modes and role-based partition filtering.
    
    Supports:
    - keyword: Pure BM25 keyword search
    - semantic: Pure dense vector search
    - hybrid: Combined dense + BM25 with RRF fusion (recommended)
    
    Access control: Only searches partitions the user has read permission on.
    Optional reranking with cross-encoder for improved accuracy.
    Optional highlighting and semantic alignment visualization.
    """
    start_time = time.time()
    user_id = request.state.user_id
    # Get authorization header for JWT passthrough to downstream services
    authorization = getattr(request.state, 'authorization', None)
    # Get role IDs from JWT (set by JWTAuthMiddleware)
    role_ids = getattr(request.state, 'role_ids', [])
    
    try:
        logger.info(
            "Search request received",
            user_id=user_id,
            query=search_request.query,
            mode=search_request.mode,
            limit=search_request.limit,
            role_count=len(role_ids),
        )
        
        # Prepare filters (exclude None values)
        filters = None
        if search_request.filters:
            filters = search_request.filters.dict(exclude_none=True)
        
        # Execute search based on mode (with partition filtering)
        if search_request.mode == "keyword":
            # Pure keyword search with role-based partitions
            results = milvus_service.keyword_search(
                query=search_request.query,
                user_id=user_id,
                top_k=search_request.rerank_k if search_request.rerank else search_request.limit,
                filters=filters,
                readable_role_ids=role_ids,
            )
            
            # Apply reranking if requested
            if search_request.rerank and len(results) > 0:
                logger.info(
                    "Applying reranking to keyword results",
                    num_results=len(results),
                    reranker_model=search_request.reranker_model,
                )
                results = await milvus_service.rerank_results(
                    query=search_request.query,
                    results=results,
                    top_k=search_request.limit,
                    reranker_model=search_request.reranker_model or "qwen3-gpu",
                )
        
        elif search_request.mode == "semantic":
            # Pure semantic search with role-based partitions
            query_embedding = await embedding_service.embed_query(
                search_request.query, 
                user_id=user_id,
                authorization=authorization
            )
            
            if not query_embedding:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to generate query embedding"
                )
            
            results = milvus_service.semantic_search(
                query_embedding=query_embedding,
                user_id=user_id,
                top_k=search_request.rerank_k if search_request.rerank else search_request.limit,
                filters=filters,
                readable_role_ids=role_ids,
            )
            
            # Apply reranking if requested
            if search_request.rerank and len(results) > 0:
                logger.info(
                    "Applying reranking to semantic results",
                    num_results=len(results),
                    reranker_model=search_request.reranker_model,
                )
                results = await milvus_service.rerank_results(
                    query=search_request.query,
                    results=results,
                    top_k=search_request.limit,
                    reranker_model=search_request.reranker_model or "qwen3-gpu",
                )
        
        elif search_request.mode == "hybrid":
            # Hybrid search (dense + sparse) with role-based partitions
            query_embedding = await embedding_service.embed_query(
                search_request.query, 
                user_id=user_id,
                authorization=authorization
            )
            
            if not query_embedding:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to generate query embedding"
                )
            
            results = await milvus_service.hybrid_search(
                query_embedding=query_embedding,
                query_text=search_request.query,
                user_id=user_id,
                top_k=search_request.limit,
                rerank_k=search_request.rerank_k,
                dense_weight=search_request.dense_weight,
                sparse_weight=search_request.sparse_weight,
                filters=filters,
                use_reranker=search_request.rerank,
                reranker_model=search_request.reranker_model or "qwen3-gpu",
                readable_role_ids=role_ids,
            )
        
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid search mode: {search_request.mode}"
            )
        
        # Apply offset for pagination
        total_results = len(results)
        results = results[search_request.offset:search_request.offset + search_request.limit]
        
        # Enrich results with metadata from PostgreSQL (RLS-aware)
        results = await _enrich_results(results, request)
        
        # Apply highlighting if requested
        if search_request.highlight and search_request.highlight.enabled:
            for result in results:
                highlights = highlighting_service.highlight(
                    query=search_request.query,
                    text=result["text"],
                    fragment_size=search_request.highlight.fragment_size,
                    num_fragments=search_request.highlight.num_fragments,
                )
                result["highlights"] = highlights
        
        # Compute semantic alignment for top results
        for i, result in enumerate(results[:5]):  # Only top 5 for performance
            try:
                alignment = alignment_service.compute_alignment(
                    query=search_request.query,
                    document=result["text"],
                    threshold=0.5,
                )
                result["semantic_alignment"] = alignment
            except Exception as e:
                logger.error(
                    "Failed to compute semantic alignment",
                    error=str(e),
                )
                result["semantic_alignment"] = None
        
        # Build response
        search_results = []
        for result in results:
            # Build scores object
            scores = SearchScores(
                dense=result.get("dense_score"),
                sparse=result.get("sparse_score"),
                rerank=result.get("rerank_score"),
                final=result["score"],
            )
            
            # Build highlights
            highlights = None
            if "highlights" in result:
                highlights = [
                    HighlightFragment(**h) for h in result["highlights"]
                ]
            
            # Build semantic alignment
            semantic_alignment = None
            if result.get("semantic_alignment"):
                alignment_data = result["semantic_alignment"]
                matched_spans = [
                    MatchedSpan(**span) for span in alignment_data.get("matched_spans", [])
                ]
                semantic_alignment = SemanticAlignment(
                    query_tokens=alignment_data["query_tokens"],
                    document_tokens=alignment_data.get("document_tokens"),
                    alignment_matrix=alignment_data.get("alignment_matrix"),
                    matched_spans=matched_spans,
                )
            
            search_result = SearchResult(
                file_id=result["file_id"],
                filename=result.get("filename", "Unknown"),
                chunk_index=result["chunk_index"],
                page_number=result.get("page_number", -1),
                text=result["text"],
                score=result["score"],
                scores=scores,
                highlights=highlights,
                semantic_alignment=semantic_alignment,
                metadata=result.get("metadata", {}),
            )
            search_results.append(search_result)
        
        execution_time_ms = int((time.time() - start_time) * 1000)
        
        logger.info(
            "Search completed",
            user_id=user_id,
            result_count=len(search_results),
            total=total_results,
            execution_time_ms=execution_time_ms,
        )
        
        return SearchResponse(
            query=search_request.query,
            mode=search_request.mode,
            total=total_results,
            limit=search_request.limit,
            offset=search_request.offset,
            execution_time_ms=execution_time_ms,
            results=search_results,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Search failed",
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {str(e)}"
        )


@router.post("/keyword", response_model=SearchResponse)
async def keyword_search(
    search_request: SearchRequest,
    request: Request,
):
    """Pure BM25 keyword search."""
    search_request.mode = "keyword"
    return await search(search_request, request)


@router.post("/semantic", response_model=SearchResponse)
async def semantic_search(
    search_request: SearchRequest,
    request: Request,
):
    """Pure dense vector semantic search."""
    search_request.mode = "semantic"
    return await search(search_request, request)


@router.post("/mmr", response_model=SearchResponse)
async def mmr_search(
    search_request: MMRSearchRequest,
    request: Request,
):
    """
    Search with Maximal Marginal Relevance for diversity.
    
    MMR reduces redundancy by penalizing results similar to already-selected ones.
    """
    user_id = request.state.user_id
    
    try:
        # First, get initial results
        base_request = SearchRequest(**search_request.dict())
        response = await search(base_request, request)
        
        # Apply MMR to diversify results
        if len(response.results) > 1:
            diversified = _apply_mmr(
                results=response.results,
                lambda_param=search_request.lambda_param,
                diversity_threshold=search_request.diversity_threshold,
            )
            response.results = diversified
        
        return response
    
    except Exception as e:
        logger.error(
            "MMR search failed",
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"MMR search failed: {str(e)}"
        )


@router.post("/explain", response_model=ExplainResponse)
async def explain_result(
    explain_request: ExplainRequest,
    request: Request,
):
    """
    Explain why a specific document was retrieved for a query.
    
    Provides detailed scoring breakdown and semantic matches.
    Uses role-based partition filtering to ensure user has access to the document.
    """
    user_id = request.state.user_id
    authorization = getattr(request.state, 'authorization', None)
    role_ids = getattr(request.state, 'role_ids', [])
    
    try:
        # Get the document (with role-based access control)
        document = milvus_service.get_document(
            file_id=explain_request.file_id,
            chunk_index=explain_request.chunk_index,
            user_id=user_id,
            readable_role_ids=role_ids,
        )
        
        if not document:
            raise HTTPException(
                status_code=404,
                detail="Document not found"
            )
        
        # Generate query embedding
        query_embedding = await embedding_service.embed_query(
            explain_request.query, 
            user_id=user_id,
            authorization=authorization
        )
        
        if not query_embedding:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate query embedding"
            )
        
        # Get document embedding
        doc_embedding = document.get("text_dense", [])
        
        # Compute scores
        explanation = {
            "dense_score": None,
            "sparse_score": None,
            "rerank_score": None,
            "semantic_matches": [],
        }
        
        # Dense score (cosine similarity)
        if doc_embedding:
            import numpy as np
            q_norm = np.array(query_embedding) / (np.linalg.norm(query_embedding) + 1e-8)
            d_norm = np.array(doc_embedding) / (np.linalg.norm(doc_embedding) + 1e-8)
            dense_score = float(np.dot(q_norm, d_norm))
            explanation["dense_score"] = dense_score
        
        # Rerank score
        rerank_explanation = reranking_service.explain_score(
            query=explain_request.query,
            document=document["text"],
        )
        explanation["rerank_score"] = rerank_explanation["score"]
        
        # Semantic alignment
        alignment = alignment_service.compute_alignment(
            query=explain_request.query,
            document=document["text"],
            threshold=0.4,
        )
        explanation["semantic_matches"] = alignment.get("matched_spans", [])
        
        # Term contributions (from highlighting)
        highlights = highlighting_service.highlight(
            query=explain_request.query,
            text=document["text"],
        )
        explanation["highlighted_matches"] = highlights
        
        return ExplainResponse(
            query=explain_request.query,
            document={
                "file_id": document["file_id"],
                "chunk_index": document["chunk_index"],
                "text": document["text"],
            },
            explanation=explanation,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Explain failed",
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Explain failed: {str(e)}"
        )


async def _set_rls_session_vars(conn, request: Request):
    """Set session variables for RLS on ingestion DB."""
    user_id = getattr(request.state, "user_id", "")
    # Use role_ids (set by JWTAuthMiddleware) not readable_role_ids
    role_ids = getattr(request.state, "role_ids", [])
    # PostgreSQL SET command doesn't support parameterized queries
    # Use string formatting with proper escaping
    # NOTE: Use SET (not SET LOCAL) because asyncpg uses autocommit by default
    # SET LOCAL only persists within a transaction block
    await conn.execute(f"SET app.user_id = '{user_id}'")
    await conn.execute(f"SET app.user_role_ids_read = '{','.join(role_ids)}'")


async def _enrich_results(results: list, request: Request) -> list:
    """
    Enrich search results with metadata from PostgreSQL.
    
    Args:
        results: Search results from Milvus
        request: Request (for RLS session vars)
    
    Returns:
        Enriched results with filenames
    """
    if not results:
        return results
    
    try:
        # Use shared PostgresService connection pool
        from api.main import pg_service
        async with await pg_service.acquire() as conn:
            await _set_rls_session_vars(conn, request)
            # Get unique file IDs
            file_ids = list(set(result["file_id"] for result in results))
            
            # Fetch filenames
            file_rows = await conn.fetch("""
                SELECT file_id, filename
                FROM ingestion_files
                WHERE file_id = ANY($1::uuid[])
            """, file_ids)
            
            # Build filename lookup
            filename_lookup = {
                str(row["file_id"]): row["filename"]
                for row in file_rows
            }
            
            # Enrich results
            enriched = []
            for result in results:
                if result["file_id"] in filename_lookup:
                    result["filename"] = filename_lookup[result["file_id"]]
                    enriched.append(result)
            
            return enriched
    
    except Exception as e:
        logger.error(
            "Failed to enrich results",
            error=str(e),
            exc_info=True,
        )
        # Return un-enriched results on failure
        return results


def _apply_mmr(
    results: list,
    lambda_param: float,
    diversity_threshold: float,
) -> list:
    """
    Apply Maximal Marginal Relevance to diversify results.
    
    Args:
        results: Search results
        lambda_param: 0-1, balance between relevance (1) and diversity (0)
        diversity_threshold: Cosine similarity threshold for considering items similar
    
    Returns:
        Diversified results
    """
    if len(results) <= 1:
        return results
    
    # Simple MMR implementation
    # In practice, you would compute pairwise similarities
    # For now, we'll use a heuristic based on text overlap
    
    selected = [results[0]]  # Always include top result
    remaining = results[1:]
    
    while remaining and len(selected) < len(results):
        best_score = -1
        best_idx = 0
        
        for i, candidate in enumerate(remaining):
            # Relevance score
            relevance = candidate.score
            
            # Diversity: minimum similarity to already selected
            diversity = 1.0  # Assume diverse
            for selected_result in selected:
                similarity = _text_similarity(candidate.text, selected_result.text)
                if similarity > diversity_threshold:
                    diversity = min(diversity, 1.0 - similarity)
            
            # MMR score
            mmr_score = lambda_param * relevance + (1 - lambda_param) * diversity
            
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i
        
        selected.append(remaining.pop(best_idx))
    
    return selected


def _text_similarity(text1: str, text2: str) -> float:
    """
    Compute simple text similarity (Jaccard).
    
    Args:
        text1: First text
        text2: Second text
    
    Returns:
        Similarity score (0-1)
    """
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1 & words2
    union = words1 | words2
    
    return len(intersection) / len(union)

