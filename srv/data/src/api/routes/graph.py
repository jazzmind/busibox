"""
Graph visualization API routes.

Provides endpoints for frontend knowledge map rendering:
- GET /data/graph: Get graph visualization data (nodes + edges)
- GET /data/graph/entity/{node_id}: Get a specific entity and neighbors
- GET /data/graph/document/{document_id}: Get graph for a specific document

Returns data formatted for react-force-graph, vis-network, or similar
graph visualization libraries.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Any, Dict, List, Optional

from api.routes.data import require_data_read

logger = structlog.get_logger()

router = APIRouter()


# =============================================================================
# Helpers
# =============================================================================

def _get_graph_service(request: Request):
    """Get graph service from app state."""
    graph_service = getattr(request.app.state, "graph_service", None)
    if not graph_service or not graph_service.available:
        return None
    return graph_service


# =============================================================================
# Endpoints
# =============================================================================

@router.get(
    "",
    summary="Get knowledge graph visualization data",
    dependencies=[Depends(require_data_read)],
)
async def get_graph(
    request: Request,
    center: Optional[str] = Query(None, description="Center node ID to expand from"),
    label: Optional[str] = Query(None, description="Filter by node label (e.g., 'Person', 'Project')"),
    depth: int = Query(2, ge=1, le=5, description="Traversal depth from center"),
    limit: int = Query(100, ge=1, le=500, description="Maximum nodes to return"),
    library_ids: Optional[str] = Query(None, description="Comma-separated library IDs to filter graph (Document nodes only)"),
):
    """
    Get graph data for knowledge map visualization.
    
    Returns nodes and edges formatted for frontend graph rendering libraries.
    
    Query parameters:
    - **center**: Optional node ID to expand from (shows neighborhood)
    - **label**: Optional label filter (e.g., Person, Technology, Project)
    - **depth**: How many hops to traverse (1-5)
    - **limit**: Maximum nodes to return
    
    Response format (for react-force-graph, vis-network, etc.):
    ```json
    {
        "nodes": [{"node_id": "...", "name": "...", "entity_type": "...", ...}],
        "edges": [{"type": "RELATED_TO", "from": "node1", "to": "node2"}],
        "total_nodes": 42,
        "total_edges": 38
    }
    ```
    """
    graph_service = _get_graph_service(request)
    
    if not graph_service:
        return {
            "nodes": [],
            "edges": [],
            "total_nodes": 0,
            "total_edges": 0,
            "graph_available": False,
        }
    
    user_id = getattr(request.state, "user_id", None)
    
    try:
        library_ids_list: Optional[List[str]] = None
        if library_ids:
            library_ids_list = [lid.strip() for lid in library_ids.split(",") if lid.strip()]
        result = await graph_service.get_graph_visualization(
            center_id=center,
            label=label,
            depth=depth,
            owner_id=user_id,
            limit=limit,
            library_ids=library_ids_list,
        )
        
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        
        logger.info(
            "[GRAPH API] Visualization data returned",
            user_id=user_id,
            center=center,
            label=label,
            node_count=len(nodes),
            edge_count=len(edges),
        )
        
        return {
            "nodes": nodes,
            "edges": edges,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "graph_available": True,
        }
    except Exception as e:
        logger.error("[GRAPH API] Failed to get graph data", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/entity/{node_id}",
    summary="Get entity details and neighbors",
    dependencies=[Depends(require_data_read)],
)
async def get_entity(
    request: Request,
    node_id: str,
    depth: int = Query(1, ge=1, le=3, description="Neighbor traversal depth"),
    limit: int = Query(30, ge=1, le=100, description="Maximum neighbors"),
):
    """
    Get details of a specific entity and its immediate neighbors.
    
    Useful for entity detail views and expanding nodes in the graph UI.
    """
    graph_service = _get_graph_service(request)
    
    if not graph_service:
        raise HTTPException(
            status_code=503,
            detail="Graph database not available",
        )
    
    user_id = getattr(request.state, "user_id", None)
    
    try:
        result = await graph_service.get_neighbors(
            node_id=node_id,
            depth=depth,
            owner_id=user_id,
            limit=limit,
        )
        
        return {
            "node_id": node_id,
            "neighbors": result.get("nodes", []),
            "relationships": result.get("relationships", []),
            "neighbor_count": len(result.get("nodes", [])),
        }
    except Exception as e:
        logger.error("[GRAPH API] Failed to get entity", error=str(e), node_id=node_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/document/{document_id}",
    summary="Get graph data for a specific document",
    dependencies=[Depends(require_data_read)],
)
async def get_document_graph(
    request: Request,
    document_id: str,
    depth: int = Query(2, ge=1, le=3, description="Traversal depth from document"),
    limit: int = Query(50, ge=1, le=200, description="Maximum nodes"),
):
    """
    Get the graph context for a specific document.
    
    Shows entities mentioned in the document and their connections.
    Useful for document detail views to show related knowledge.
    """
    graph_service = _get_graph_service(request)
    
    if not graph_service:
        return {
            "document_id": document_id,
            "nodes": [],
            "edges": [],
            "graph_available": False,
        }
    
    user_id = getattr(request.state, "user_id", None)
    
    try:
        # Get the document's graph neighborhood
        result = await graph_service.get_graph_visualization(
            center_id=document_id,
            depth=depth,
            owner_id=user_id,
            limit=limit,
        )
        
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        
        return {
            "document_id": document_id,
            "nodes": nodes,
            "edges": edges,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "graph_available": True,
        }
    except Exception as e:
        logger.error("[GRAPH API] Failed to get document graph", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/stats",
    summary="Get graph database statistics",
    dependencies=[Depends(require_data_read)],
)
async def get_graph_stats(request: Request):
    """
    Get statistics about the knowledge graph.
    
    Returns node counts by label, relationship counts by type, etc.
    """
    graph_service = _get_graph_service(request)
    
    if not graph_service:
        return {
            "available": False,
            "total_nodes": 0,
            "total_relationships": 0,
            "labels": {},
            "relationship_types": {},
        }
    
    user_id = getattr(request.state, "user_id", None)
    
    try:
        # Get node counts by label
        label_counts = await graph_service.query(
            "MATCH (n:GraphNode) "
            "WHERE (n.owner_id = $owner_id OR n.visibility = 'shared') "
            "WITH labels(n) as labels "
            "UNWIND labels as label "
            "WITH label WHERE label <> 'GraphNode' "
            "RETURN label, count(*) as count ORDER BY count DESC",
            params={"owner_id": user_id},
        )
        
        # Get relationship counts by type
        rel_counts = await graph_service.query(
            "MATCH (a:GraphNode)-[r]->(b:GraphNode) "
            "WHERE (a.owner_id = $owner_id OR a.visibility = 'shared') "
            "RETURN type(r) as type, count(*) as count ORDER BY count DESC",
            params={"owner_id": user_id},
        )
        
        labels = {r.get("label", "Unknown"): r.get("count", 0) for r in label_counts}
        rel_types = {r.get("type", "Unknown"): r.get("count", 0) for r in rel_counts}
        
        return {
            "available": True,
            "total_nodes": sum(labels.values()),
            "total_relationships": sum(rel_types.values()),
            "labels": labels,
            "relationship_types": rel_types,
        }
    except Exception as e:
        logger.error("[GRAPH API] Failed to get stats", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
