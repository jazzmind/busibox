"""
Search API routes for semantic document search.

Provides hybrid search (dense + sparse BM25) across user's documents.
"""

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.services.minio import MinIOService
from processors.embedder import Embedder
from services.milvus_service import MilvusService
from shared.config import Config

logger = structlog.get_logger()

router = APIRouter()

# Initialize services
config = Config()
embedder = Embedder(config.to_dict())
milvus_service = MilvusService(config.to_dict())
minio_service = MinIOService(config.to_dict())


class SearchRequest(BaseModel):
    """Search request model."""
    
    query: str = Field(..., description="Search query text", min_length=1, max_length=1000)
    limit: int = Field(10, description="Number of results to return", ge=1, le=100)
    offset: int = Field(0, description="Offset for pagination", ge=0)
    rerank_k: int = Field(100, description="Number of candidates before reranking", ge=10, le=500)
    dense_weight: float = Field(0.7, description="Weight for dense semantic search", ge=0.0, le=1.0)
    sparse_weight: float = Field(0.3, description="Weight for sparse BM25 search", ge=0.0, le=1.0)


class SearchResult(BaseModel):
    """Single search result."""
    
    file_id: str = Field(..., description="File identifier")
    filename: str = Field(..., description="Original filename")
    chunk_index: int = Field(..., description="Chunk index within file")
    page_number: int = Field(..., description="Page number (or -1 for non-PDF)")
    text: str = Field(..., description="Chunk text content")
    score: float = Field(..., description="Relevance score (0-1)")
    metadata: dict = Field(default_factory=dict, description="Additional metadata")


class SearchResponse(BaseModel):
    """Search response model."""
    
    query: str = Field(..., description="Original search query")
    results: list[SearchResult] = Field(..., description="Search results")
    total: int = Field(..., description="Total number of results")
    limit: int = Field(..., description="Results per page")
    offset: int = Field(..., description="Current offset")


@router.post(
    "",
    response_model=SearchResponse,
    summary="Search documents",
    description="""
    Perform semantic search across user's documents using hybrid search.
    
    **Features:**
    - Dense semantic search using text-embedding-3-small
    - Sparse BM25 keyword search
    - Weighted combination of both approaches
    - User permission filtering (only searches user's documents)
    - Pagination support
    
    **How it works:**
    1. Query is embedded using the same model as documents
    2. Milvus performs hybrid search (dense + BM25)
    3. Results are filtered by user_id
    4. Top-k results are returned with relevance scores
    
    **Example queries:**
    - "What are the key findings in the Q4 report?"
    - "Show me all references to machine learning"
    - "Find documents about customer feedback"
    """,
)
async def search_documents(
    search_request: SearchRequest,
    request: Request,
):
    """
    Search user's documents with semantic + keyword hybrid search.
    
    Args:
        search_request: Search parameters
        request: FastAPI request (for user_id from middleware)
    
    Returns:
        SearchResponse with ranked results
    
    Raises:
        HTTPException: If search fails or user not authenticated
    """
    try:
        # Get user_id from middleware (set by AuthMiddleware)
        user_id = request.state.user_id
        if not user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")
        
        logger.info(
            "Search request received",
            user_id=user_id,
            query=search_request.query,
            limit=search_request.limit,
            offset=search_request.offset,
        )
        
        # Generate embedding for query
        query_embedding = await embedder.embed_single(search_request.query)
        
        if not query_embedding:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate query embedding"
            )
        
        logger.debug(
            "Query embedding generated",
            user_id=user_id,
            embedding_dim=len(query_embedding),
        )
        
        # Perform hybrid search in Milvus
        search_results = milvus_service.hybrid_search(
            query_embedding=query_embedding,
            user_id=user_id,
            top_k=search_request.limit + search_request.offset,  # Get extra for offset
            rerank_k=search_request.rerank_k,
            dense_weight=search_request.dense_weight,
            sparse_weight=search_request.sparse_weight,
        )
        
        # Apply offset
        paginated_results = search_results[search_request.offset:][:search_request.limit]
        
        # Get file metadata from PostgreSQL to add filenames
        # Import here to avoid circular dependency
        from api.services.postgres import PostgresService
        postgres_service = PostgresService(config.to_dict())
        
        # Enrich results with filenames
        enriched_results = []
        for result in paginated_results:
            # Get file metadata
            file_metadata = await postgres_service.get_file_metadata(result["file_id"])
            
            enriched_result = SearchResult(
                file_id=result["file_id"],
                filename=file_metadata.get("filename", "unknown") if file_metadata else "unknown",
                chunk_index=result["chunk_index"],
                page_number=result["page_number"],
                text=result["text"],
                score=result["score"],
                metadata=result["metadata"],
            )
            enriched_results.append(enriched_result)
        
        logger.info(
            "Search completed successfully",
            user_id=user_id,
            result_count=len(enriched_results),
            total_matches=len(search_results),
        )
        
        return SearchResponse(
            query=search_request.query,
            results=enriched_results,
            total=len(search_results),
            limit=search_request.limit,
            offset=search_request.offset,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Search failed",
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {str(e)}"
        )


@router.get(
    "/health",
    summary="Search service health check",
    description="Check if search dependencies (Milvus, embedder) are available",
)
async def search_health():
    """Check search service health."""
    try:
        # Check embedder health
        embedder_healthy = await embedder.check_health()
        
        # Check Milvus connection
        milvus_service.connect()
        milvus_healthy = milvus_service.connected
        
        if embedder_healthy and milvus_healthy:
            return {
                "status": "healthy",
                "embedder": "ok",
                "milvus": "ok",
            }
        else:
            return {
                "status": "degraded",
                "embedder": "ok" if embedder_healthy else "unavailable",
                "milvus": "ok" if milvus_healthy else "unavailable",
            }
    except Exception as e:
        logger.error("Search health check failed", error=str(e))
        raise HTTPException(
            status_code=503,
            detail=f"Search service unhealthy: {str(e)}"
        )

