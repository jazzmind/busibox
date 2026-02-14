"""
Graph database tools for AI agents.

Provides tools to query and explore the knowledge graph:
- graph_query: Search for entities and their relationships
- graph_explore: Explore the neighborhood of a specific node
- graph_relate: Create relationships between entities

These tools connect directly to Neo4j via the neo4j Python driver.
All operations respect multi-tenant access control.
"""

import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Tool, RunContext

from app.agents.core import BusiboxDeps

import structlog

logger = structlog.get_logger()

# Neo4j driver - optional
try:
    from neo4j import AsyncGraphDatabase
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False


# =============================================================================
# Output Schemas
# =============================================================================

class GraphNode(BaseModel):
    """A graph node."""
    node_id: str = Field(description="Unique node identifier")
    name: str = Field(default="", description="Node name")
    labels: List[str] = Field(default_factory=list, description="Node labels/types")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Node properties")


class GraphEdge(BaseModel):
    """A graph relationship."""
    type: str = Field(description="Relationship type")
    from_id: str = Field(description="Source node ID")
    to_id: str = Field(description="Target node ID")


class GraphQueryOutput(BaseModel):
    """Output from graph query."""
    found: bool = Field(description="Whether results were found")
    node_count: int = Field(description="Number of nodes returned")
    edge_count: int = Field(description="Number of edges returned")
    context: str = Field(description="Formatted context for LLM")
    nodes: List[Dict[str, Any]] = Field(default_factory=list, description="Graph nodes")
    edges: List[Dict[str, Any]] = Field(default_factory=list, description="Graph edges")
    error: Optional[str] = Field(default=None, description="Error message if query failed")


class GraphExploreOutput(BaseModel):
    """Output from graph explore."""
    found: bool = Field(description="Whether the node was found")
    center_node: Optional[Dict[str, Any]] = Field(default=None, description="The center node")
    neighbor_count: int = Field(description="Number of connected nodes")
    context: str = Field(description="Formatted context for LLM")
    neighbors: List[Dict[str, Any]] = Field(default_factory=list, description="Connected nodes")
    relationships: List[Dict[str, Any]] = Field(default_factory=list, description="Connecting relationships")
    error: Optional[str] = Field(default=None, description="Error message")


class GraphRelateOutput(BaseModel):
    """Output from graph relate."""
    success: bool = Field(description="Whether the relationship was created")
    message: str = Field(description="Status message")
    error: Optional[str] = Field(default=None, description="Error message")


# =============================================================================
# Neo4j Connection Helper
# =============================================================================

_driver = None


async def _get_driver():
    """Get or create the Neo4j driver (singleton)."""
    global _driver
    if _driver is not None:
        return _driver
    
    if not NEO4J_AVAILABLE:
        return None
    
    uri = os.getenv("NEO4J_URI", "")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    
    if not uri:
        return None
    
    try:
        _driver = AsyncGraphDatabase.driver(
            uri,
            auth=(user, password),
            max_connection_pool_size=10,
            connection_acquisition_timeout=5.0,
        )
        await _driver.verify_connectivity()
        return _driver
    except Exception as e:
        logger.warning("[GRAPH-TOOL] Failed to connect to Neo4j", error=str(e))
        _driver = None
        return None


def _sanitize_label(label: str) -> str:
    """Sanitize a label to prevent Cypher injection."""
    return "".join(c for c in label if c.isalnum() or c == "_")


# =============================================================================
# Tool Functions
# =============================================================================

async def graph_query(
    ctx: RunContext[BusiboxDeps],
    query: str,
    entity_type: Optional[str] = None,
    depth: int = 2,
    limit: int = 20,
) -> GraphQueryOutput:
    """
    Search the knowledge graph for entities matching a query.
    
    Use this tool to find people, organizations, technologies, concepts,
    projects, and their relationships in the knowledge graph.
    
    Args:
        ctx: RunContext with user context
        query: Natural language query to find entities (searches by name)
        entity_type: Optional filter: Person, Organization, Technology, Concept, Location, Project
        depth: How many hops to traverse (1-5, default 2)
        limit: Maximum results (default 20)
        
    Returns:
        GraphQueryOutput with matching nodes, edges, and formatted context
    """
    driver = await _get_driver()
    if not driver:
        return GraphQueryOutput(
            found=False,
            node_count=0,
            edge_count=0,
            context="Knowledge graph is not available.",
            error="Neo4j not connected",
        )
    
    user_id = ctx.deps.user_id
    
    try:
        terms = [t.strip().lower() for t in query.split() if len(t.strip()) > 2]
        if not terms:
            return GraphQueryOutput(
                found=False,
                node_count=0,
                edge_count=0,
                context="Query too short to search.",
            )
        
        # Build name match conditions
        where_parts = []
        params: Dict[str, Any] = {"user_id": user_id, "limit": min(limit, 50)}
        for i, term in enumerate(terms):
            where_parts.append(f"toLower(n.name) CONTAINS $term{i}")
            params[f"term{i}"] = term
        
        name_clause = " OR ".join(where_parts)
        access_clause = "(n.owner_id = $user_id OR n.visibility = 'shared')"
        
        type_clause = ""
        if entity_type:
            safe_type = _sanitize_label(entity_type)
            type_clause = f"AND n:{safe_type}"
        
        depth = min(max(depth, 1), 5)
        
        cypher = (
            f"MATCH (n:GraphNode) "
            f"WHERE ({name_clause}) AND {access_clause} {type_clause} "
            f"WITH n LIMIT $limit "
            f"OPTIONAL MATCH (n)-[r]-(related:GraphNode) "
            f"WHERE (related.owner_id = $user_id OR related.visibility = 'shared') "
            f"RETURN collect(DISTINCT properties(n)) as center_nodes, "
            f"collect(DISTINCT properties(related)) as neighbor_nodes, "
            f"collect(DISTINCT {{type: type(r), from: startNode(r).node_id, to: endNode(r).node_id}}) as edges"
        )
        
        async with driver.session() as session:
            result = await session.run(cypher, params)
            record = await result.single()
            
            if not record:
                return GraphQueryOutput(
                    found=False,
                    node_count=0,
                    edge_count=0,
                    context=f"No entities found matching '{query}'.",
                )
            
            center_nodes = record.get("center_nodes", [])
            neighbor_nodes = record.get("neighbor_nodes", [])
            edges = [e for e in record.get("edges", []) if e.get("from") and e.get("to")]
            
            all_nodes = center_nodes + [n for n in neighbor_nodes if n not in center_nodes]
            
            # Build context for LLM
            context_parts = []
            for node in center_nodes[:10]:
                name = node.get("name", node.get("node_id", "unknown"))
                ntype = node.get("entity_type", "Entity")
                context_parts.append(f"- {name} ({ntype})")
            
            if edges:
                for edge in edges[:10]:
                    context_parts.append(
                        f"  [{edge.get('from', '?')}] --{edge.get('type', '?')}--> [{edge.get('to', '?')}]"
                    )
            
            context = f"Knowledge graph results for '{query}':\n" + "\n".join(context_parts)
            
            return GraphQueryOutput(
                found=len(all_nodes) > 0,
                node_count=len(all_nodes),
                edge_count=len(edges),
                context=context,
                nodes=all_nodes,
                edges=edges,
            )
    except Exception as e:
        logger.warning("[GRAPH-TOOL] graph_query failed", error=str(e))
        return GraphQueryOutput(
            found=False,
            node_count=0,
            edge_count=0,
            context="Graph query failed.",
            error=str(e),
        )


async def graph_explore(
    ctx: RunContext[BusiboxDeps],
    node_id: str,
    depth: int = 2,
    rel_types: Optional[List[str]] = None,
    limit: int = 30,
) -> GraphExploreOutput:
    """
    Explore the neighborhood of a specific node in the knowledge graph.
    
    Use this tool when you know a specific entity and want to discover
    what it's connected to - related projects, people, technologies, etc.
    
    Args:
        ctx: RunContext with user context
        node_id: ID of the node to explore (from a previous graph_query result)
        depth: How many hops to traverse (1-5, default 2)
        rel_types: Optional relationship type filter (e.g., ["DEPENDS_ON", "BELONGS_TO"])
        limit: Maximum neighbors to return (default 30)
        
    Returns:
        GraphExploreOutput with the node, its neighbors, and relationships
    """
    driver = await _get_driver()
    if not driver:
        return GraphExploreOutput(
            found=False,
            neighbor_count=0,
            context="Knowledge graph is not available.",
            error="Neo4j not connected",
        )
    
    user_id = ctx.deps.user_id
    depth = min(max(depth, 1), 5)
    
    try:
        params: Dict[str, Any] = {
            "node_id": node_id,
            "user_id": user_id,
            "limit": min(limit, 100),
        }
        
        rel_filter = ""
        if rel_types:
            safe_types = [_sanitize_label(rt) for rt in rel_types]
            rel_filter = ":" + "|".join(safe_types)
        
        # Get center node
        center_cypher = (
            "MATCH (n:GraphNode {node_id: $node_id}) "
            "WHERE (n.owner_id = $user_id OR n.visibility = 'shared') "
            "RETURN properties(n) as node"
        )
        
        # Get neighbors
        neighbor_cypher = (
            f"MATCH (start:GraphNode {{node_id: $node_id}})"
            f"-[r{rel_filter}*1..{depth}]-(related:GraphNode) "
            f"WHERE (related.owner_id = $user_id OR related.visibility = 'shared') "
            f"AND related.node_id <> $node_id "
            f"WITH DISTINCT related "
            f"LIMIT $limit "
            f"RETURN collect(properties(related)) as neighbors"
        )
        
        # Get edges between all found nodes
        edge_cypher = (
            f"MATCH (start:GraphNode {{node_id: $node_id}})"
            f"-[r{rel_filter}]-(related:GraphNode) "
            f"WHERE (related.owner_id = $user_id OR related.visibility = 'shared') "
            f"RETURN type(r) as type, startNode(r).node_id as from_id, endNode(r).node_id as to_id"
        )
        
        async with driver.session() as session:
            # Get center
            result = await session.run(center_cypher, params)
            record = await result.single()
            if not record:
                return GraphExploreOutput(
                    found=False,
                    neighbor_count=0,
                    context=f"Node '{node_id}' not found or not accessible.",
                )
            
            center_node = record["node"]
            
            # Get neighbors
            result = await session.run(neighbor_cypher, params)
            record = await result.single()
            neighbors = record["neighbors"] if record else []
            
            # Get edges
            result = await session.run(edge_cypher, params)
            relationships = []
            async for rec in result:
                relationships.append({
                    "type": rec["type"],
                    "from": rec["from_id"],
                    "to": rec["to_id"],
                })
            
            # Build context
            center_name = center_node.get("name", node_id)
            center_type = center_node.get("entity_type", "Entity")
            
            context_parts = [f"Exploring '{center_name}' ({center_type}):"]
            for n in neighbors[:15]:
                name = n.get("name", n.get("node_id", "unknown"))
                ntype = n.get("entity_type", "Entity")
                context_parts.append(f"  - Connected to: {name} ({ntype})")
            
            return GraphExploreOutput(
                found=True,
                center_node=center_node,
                neighbor_count=len(neighbors),
                context="\n".join(context_parts),
                neighbors=neighbors,
                relationships=relationships,
            )
    except Exception as e:
        logger.warning("[GRAPH-TOOL] graph_explore failed", error=str(e))
        return GraphExploreOutput(
            found=False,
            neighbor_count=0,
            context="Graph exploration failed.",
            error=str(e),
        )


async def graph_relate(
    ctx: RunContext[BusiboxDeps],
    from_id: str,
    relationship: str,
    to_id: str,
) -> GraphRelateOutput:
    """
    Create a relationship between two entities in the knowledge graph.
    
    Use this tool to explicitly connect entities discovered during conversation,
    e.g., linking a person to a project or a technology to a concept.
    
    Args:
        ctx: RunContext with user context
        from_id: Source node ID
        relationship: Relationship type (e.g., "WORKS_ON", "DEPENDS_ON", "RELATED_TO")
        to_id: Target node ID
        
    Returns:
        GraphRelateOutput indicating success or failure
    """
    driver = await _get_driver()
    if not driver:
        return GraphRelateOutput(
            success=False,
            message="Knowledge graph is not available.",
            error="Neo4j not connected",
        )
    
    user_id = ctx.deps.user_id
    safe_rel = _sanitize_label(relationship)
    
    if not safe_rel:
        return GraphRelateOutput(
            success=False,
            message="Invalid relationship type.",
            error="Relationship type must be alphanumeric",
        )
    
    try:
        params = {
            "from_id": from_id,
            "to_id": to_id,
            "user_id": user_id,
        }
        
        # Verify both nodes exist and are accessible
        cypher = (
            f"MATCH (a:GraphNode {{node_id: $from_id}}) "
            f"WHERE (a.owner_id = $user_id OR a.visibility = 'shared') "
            f"MATCH (b:GraphNode {{node_id: $to_id}}) "
            f"WHERE (b.owner_id = $user_id OR b.visibility = 'shared') "
            f"MERGE (a)-[r:{safe_rel}]->(b) "
            f"SET r.created_by = $user_id "
            f"RETURN a.name as from_name, b.name as to_name"
        )
        
        async with driver.session() as session:
            result = await session.run(cypher, params)
            record = await result.single()
            
            if record:
                from_name = record.get("from_name", from_id)
                to_name = record.get("to_name", to_id)
                return GraphRelateOutput(
                    success=True,
                    message=f"Created relationship: {from_name} --{relationship}--> {to_name}",
                )
            else:
                return GraphRelateOutput(
                    success=False,
                    message="One or both nodes not found or not accessible.",
                )
    except Exception as e:
        logger.warning("[GRAPH-TOOL] graph_relate failed", error=str(e))
        return GraphRelateOutput(
            success=False,
            message="Failed to create relationship.",
            error=str(e),
        )


# =============================================================================
# PydanticAI Tool Objects (for agent registration)
# =============================================================================

graph_query_tool = Tool(
    graph_query,
    takes_ctx=True,
    name="graph_query",
    description="Search the knowledge graph for entities (people, organizations, technologies, concepts) and their relationships.",
)

graph_explore_tool = Tool(
    graph_explore,
    takes_ctx=True,
    name="graph_explore",
    description="Explore the neighborhood of a specific entity in the knowledge graph to discover connections.",
)

graph_relate_tool = Tool(
    graph_relate,
    takes_ctx=True,
    name="graph_relate",
    description="Create a relationship between two entities in the knowledge graph.",
)
