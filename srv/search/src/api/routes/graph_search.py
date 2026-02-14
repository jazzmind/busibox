"""
Graph search API routes.

Provides endpoints for graph-based search operations:
- POST /graph: Search the knowledge graph for entities and relationships
- POST /graph/related: Find entities related to search results (Graph-RAG)
- POST /graph/path: Find paths between entities
"""

import structlog
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

logger = structlog.get_logger()

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class GraphSearchRequest(BaseModel):
    """Request for graph entity search."""
    query: str = Field(..., description="Search query to find entities", min_length=1, max_length=500)
    entity_type: Optional[str] = Field(None, description="Filter by entity type (Person, Organization, Technology, etc.)")
    depth: int = Field(2, description="Graph traversal depth", ge=1, le=5)
    limit: int = Field(20, description="Maximum results", ge=1, le=100)


class GraphExpandRequest(BaseModel):
    """Request to expand search results with graph context."""
    document_ids: List[str] = Field(..., description="Document IDs from search results to expand", min_length=1)
    depth: int = Field(1, description="Traversal depth", ge=1, le=3)
    limit: int = Field(20, description="Maximum related nodes", ge=1, le=50)


class GraphPathRequest(BaseModel):
    """Request to find path between entities."""
    from_entity: str = Field(..., description="Source entity name or ID")
    to_entity: str = Field(..., description="Target entity name or ID")
    max_depth: int = Field(5, description="Maximum path length", ge=1, le=10)


class GraphSearchResponse(BaseModel):
    """Response from graph search."""
    nodes: List[Dict[str, Any]] = Field(default_factory=list, description="Graph nodes")
    edges: List[Dict[str, Any]] = Field(default_factory=list, description="Graph edges/relationships")
    query: str = Field("", description="Original query")


class GraphExpandResponse(BaseModel):
    """Response from graph context expansion."""
    related_entities: List[Dict[str, Any]] = Field(default_factory=list, description="Related entities found via graph")
    related_documents: List[Dict[str, Any]] = Field(default_factory=list, description="Related documents found via graph")
    graph_context: str = Field("", description="Text context for RAG injection")


class GraphPathResponse(BaseModel):
    """Response from path finding."""
    nodes: List[Dict[str, Any]] = Field(default_factory=list, description="Nodes in the path")
    relationships: List[Dict[str, Any]] = Field(default_factory=list, description="Relationships in the path")


# =============================================================================
# Endpoints
# =============================================================================

@router.post("", response_model=GraphSearchResponse)
async def graph_search(
    body: GraphSearchRequest,
    request: Request,
):
    """
    Search the knowledge graph for entities matching the query.
    
    Returns matching entities and their graph neighborhood,
    formatted for visualization or context injection.
    """
    user_id = request.state.user_id
    graph_service = getattr(request.app.state, "graph_search_service", None)
    
    if not graph_service or not graph_service.available:
        return GraphSearchResponse(
            nodes=[],
            edges=[],
            query=body.query,
        )
    
    try:
        result = await graph_service.graph_query(
            query_text=body.query,
            user_id=user_id,
            entity_type=body.entity_type,
            depth=body.depth,
            limit=body.limit,
        )
        
        logger.info(
            "Graph search completed",
            user_id=user_id,
            query=body.query,
            node_count=len(result.get("nodes", [])),
            edge_count=len(result.get("edges", [])),
        )
        
        return GraphSearchResponse(**result)
    except Exception as e:
        logger.error("Graph search failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/related", response_model=GraphExpandResponse)
async def graph_expand(
    body: GraphExpandRequest,
    request: Request,
):
    """
    Expand search results with graph context (Graph-RAG).
    
    Given document IDs from vector/keyword search, find related
    entities and documents through graph traversal.
    """
    user_id = request.state.user_id
    graph_service = getattr(request.app.state, "graph_search_service", None)
    
    if not graph_service or not graph_service.available:
        return GraphExpandResponse()
    
    try:
        result = await graph_service.expand_context(
            document_ids=body.document_ids,
            user_id=user_id,
            depth=body.depth,
            limit=body.limit,
        )
        
        logger.info(
            "Graph context expansion completed",
            user_id=user_id,
            input_docs=len(body.document_ids),
            related_entities=len(result.get("related_entities", [])),
            related_documents=len(result.get("related_documents", [])),
        )
        
        return GraphExpandResponse(**result)
    except Exception as e:
        logger.error("Graph expansion failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/path", response_model=GraphPathResponse)
async def graph_path(
    body: GraphPathRequest,
    request: Request,
):
    """
    Find the shortest path between two entities in the knowledge graph.
    """
    user_id = request.state.user_id
    graph_service = getattr(request.app.state, "graph_search_service", None)
    
    if not graph_service or not graph_service.available:
        return GraphPathResponse()
    
    try:
        result = await graph_service.find_path(
            from_entity=body.from_entity,
            to_entity=body.to_entity,
            user_id=user_id,
            max_depth=body.max_depth,
        )
        
        return GraphPathResponse(**result)
    except Exception as e:
        logger.error("Graph path finding failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
