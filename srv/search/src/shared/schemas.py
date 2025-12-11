"""
Pydantic schemas for API request/response models.
"""

from typing import List, Dict, Optional, Any, Literal
from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    """Filters for search queries."""
    
    file_ids: Optional[List[str]] = Field(None, description="Filter by specific file IDs")
    date_range: Optional[Dict[str, str]] = Field(None, description="Date range filter")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Metadata filters")


class HighlightConfig(BaseModel):
    """Configuration for highlighting."""
    
    enabled: bool = Field(True, description="Enable highlighting")
    fragment_size: int = Field(200, description="Characters per fragment")
    num_fragments: int = Field(3, description="Max fragments to return")


class SearchRequest(BaseModel):
    """Main search request model."""
    
    query: str = Field(..., description="Search query text", min_length=1, max_length=1000)
    mode: Literal["keyword", "semantic", "hybrid"] = Field(
        "hybrid",
        description="Search mode"
    )
    limit: int = Field(10, description="Number of results to return", ge=1, le=100)
    offset: int = Field(0, description="Offset for pagination", ge=0)
    rerank: bool = Field(True, description="Enable reranking")
    reranker_model: Optional[Literal["qwen3-gpu", "baai-gpu", "baai-cpu", "none"]] = Field(
        "qwen3-gpu",
        description="Reranker model to use: qwen3-gpu (fast), baai-gpu (accurate), baai-cpu (slow), none (disabled)"
    )
    rerank_k: int = Field(100, description="Number of candidates before reranking", ge=10, le=500)
    dense_weight: float = Field(0.7, description="Weight for dense semantic search", ge=0.0, le=1.0)
    sparse_weight: float = Field(0.3, description="Weight for sparse BM25 search", ge=0.0, le=1.0)
    filters: Optional[SearchFilters] = Field(None, description="Additional filters")
    highlight: Optional[HighlightConfig] = Field(
        default_factory=HighlightConfig,
        description="Highlighting configuration"
    )


class MMRSearchRequest(SearchRequest):
    """Search with MMR diversity."""
    
    lambda_param: float = Field(
        0.5,
        description="Diversity vs relevance (0=max diversity, 1=max relevance)",
        ge=0.0,
        le=1.0
    )
    diversity_threshold: float = Field(
        0.85,
        description="Cosine similarity threshold for duplicates",
        ge=0.0,
        le=1.0
    )


class HighlightFragment(BaseModel):
    """A highlighted text fragment."""
    
    fragment: str = Field(..., description="HTML fragment with highlights")
    score: float = Field(..., description="Relevance score for this fragment")
    start_offset: int = Field(..., description="Start character offset in original text")
    end_offset: int = Field(..., description="End character offset in original text")


class MatchedSpan(BaseModel):
    """A semantically aligned span."""
    
    query_token: str = Field(..., description="Query token")
    doc_span: str = Field(..., description="Matched document span")
    score: float = Field(..., description="Alignment score")
    start: int = Field(..., description="Start offset in document")
    end: int = Field(..., description="End offset in document")


class SemanticAlignment(BaseModel):
    """Semantic alignment information."""
    
    query_tokens: List[str] = Field(..., description="Query tokens")
    document_tokens: Optional[List[str]] = Field(None, description="Document tokens")
    alignment_matrix: Optional[List[List[float]]] = Field(
        None,
        description="Token-level alignment scores"
    )
    matched_spans: List[MatchedSpan] = Field(..., description="High-confidence matches")


class SearchScores(BaseModel):
    """Detailed scoring breakdown."""
    
    dense: Optional[float] = Field(None, description="Dense vector score")
    sparse: Optional[float] = Field(None, description="Sparse BM25 score")
    rerank: Optional[float] = Field(None, description="Reranker score")
    final: float = Field(..., description="Final combined score")


class SearchResult(BaseModel):
    """Single search result with all enrichments."""
    
    file_id: str = Field(..., description="File identifier")
    filename: str = Field(..., description="Original filename")
    chunk_index: int = Field(..., description="Chunk index within file")
    page_number: int = Field(..., description="Page number (or -1 for non-PDF)")
    text: str = Field(..., description="Chunk text content")
    score: float = Field(..., description="Overall relevance score")
    scores: Optional[SearchScores] = Field(None, description="Detailed score breakdown")
    highlights: Optional[List[HighlightFragment]] = Field(None, description="Highlighted fragments")
    semantic_alignment: Optional[SemanticAlignment] = Field(
        None,
        description="Semantic alignment visualization"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class SearchResponse(BaseModel):
    """Search response with results and metadata."""
    
    query: str = Field(..., description="Original search query")
    mode: str = Field(..., description="Search mode used")
    total: int = Field(..., description="Total number of results")
    limit: int = Field(..., description="Results per page")
    offset: int = Field(..., description="Current offset")
    execution_time_ms: int = Field(..., description="Execution time in milliseconds")
    results: List[SearchResult] = Field(..., description="Search results")


class BatchSearchRequest(BaseModel):
    """Batch search for multiple queries."""
    
    queries: List[str] = Field(..., description="List of queries to search", min_items=1, max_items=10)
    mode: Literal["keyword", "semantic", "hybrid"] = Field("hybrid", description="Search mode")
    limit: int = Field(10, description="Results per query", ge=1, le=100)
    rerank: bool = Field(True, description="Enable reranking")


class BatchSearchResponse(BaseModel):
    """Response for batch search."""
    
    results: List[SearchResponse] = Field(..., description="Results for each query")
    execution_time_ms: int = Field(..., description="Total execution time")


class ExplainRequest(BaseModel):
    """Request to explain a search result."""
    
    query: str = Field(..., description="Search query")
    file_id: str = Field(..., description="File ID")
    chunk_index: int = Field(..., description="Chunk index")


class TermContribution(BaseModel):
    """Contribution of a term to the score."""
    
    term: str = Field(..., description="Query term")
    score: float = Field(..., description="Contribution score")


class SemanticMatch(BaseModel):
    """Semantic match between query and document terms."""
    
    query_term: str = Field(..., description="Query term")
    doc_terms: List[str] = Field(..., description="Matching document terms")
    scores: List[float] = Field(..., description="Similarity scores")


class ExplainResponse(BaseModel):
    """Explanation of why a document was retrieved."""
    
    query: str = Field(..., description="Search query")
    document: Dict[str, Any] = Field(..., description="Document information")
    explanation: Dict[str, Any] = Field(..., description="Scoring explanation")


class HealthResponse(BaseModel):
    """Health check response."""
    
    status: Literal["healthy", "degraded", "unhealthy"] = Field(..., description="Overall status")
    milvus: str = Field(..., description="Milvus connection status")
    postgres: str = Field(..., description="PostgreSQL connection status")
    reranker: str = Field(..., description="Reranker model status")
    embedder: str = Field(..., description="Embedding service status")
    cache: Optional[str] = Field(None, description="Cache status (if enabled)")

