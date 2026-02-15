"""
Graph Database Service for Neo4j integration.

Provides optional graph database operations for building knowledge graphs
from data documents and file documents. All methods are no-ops if Neo4j
is unavailable (graceful degradation).

Key features:
- Upsert nodes with labels and properties
- Create typed relationships between nodes
- Delete nodes and relationships
- Execute arbitrary Cypher queries
- Traverse neighbors at configurable depth
- Multi-tenant: nodes carry owner_id and visibility for access control

Usage:
    graph = GraphService()
    await graph.connect()
    await graph.upsert_node("Project", {"id": "p1", "name": "Alpha"}, owner_id="user1")
    await graph.create_relationship("p1", "DEPENDS_ON", "p2")
    neighbors = await graph.get_neighbors("p1", depth=2, owner_id="user1")
"""

import os
import re
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()

# Neo4j driver is optional - graceful degradation if not installed
try:
    from neo4j import AsyncGraphDatabase, AsyncDriver
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False
    AsyncDriver = None


class GraphService:
    """
    Optional graph database integration for data documents.
    
    All methods degrade gracefully if Neo4j is unavailable or not configured.
    This ensures that core data operations are never blocked by graph failures.
    """
    
    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """
        Initialize the graph service.
        
        Args:
            uri: Neo4j Bolt URI (e.g., bolt://neo4j:7687)
            user: Neo4j username
            password: Neo4j password
        """
        self._uri = uri or os.getenv("NEO4J_URI", "")
        self._user = user or os.getenv("NEO4J_USER", "neo4j")
        self._password = password or os.getenv("NEO4J_PASSWORD", "")
        self._driver: Optional[Any] = None
        self._available = False
    
    @property
    def available(self) -> bool:
        """Whether the graph database is connected and available."""
        return self._available
    
    async def connect(self) -> bool:
        """
        Connect to Neo4j. Returns True if successful, False otherwise.
        Never raises - logs warnings on failure.
        """
        if not NEO4J_AVAILABLE:
            logger.info("[GRAPH] neo4j Python driver not installed, graph features disabled")
            return False
        
        if not self._uri:
            logger.info("[GRAPH] NEO4J_URI not configured, graph features disabled")
            return False
        
        try:
            self._driver = AsyncGraphDatabase.driver(
                self._uri,
                auth=(self._user, self._password),
                max_connection_pool_size=25,
                connection_acquisition_timeout=5.0,
            )
            # Verify connectivity
            await self._driver.verify_connectivity()
            self._available = True
            logger.info(
                "[GRAPH] Connected to Neo4j",
                uri=self._uri,
            )
            
            # Create indexes for performance
            await self._ensure_indexes()
            
            return True
        except Exception as e:
            logger.warning(
                "[GRAPH] Failed to connect to Neo4j, graph features disabled",
                uri=self._uri,
                error=str(e),
            )
            self._available = False
            return False
    
    async def disconnect(self):
        """Close the Neo4j driver connection."""
        if self._driver:
            try:
                await self._driver.close()
            except Exception:
                pass
            self._driver = None
            self._available = False
    
    async def _ensure_indexes(self):
        """Create indexes for efficient lookups."""
        if not self._available:
            return
        
        try:
            async with self._driver.session() as session:
                # Index on node_id for fast lookups
                await session.run(
                    "CREATE INDEX node_id_index IF NOT EXISTS "
                    "FOR (n:GraphNode) ON (n.node_id)"
                )
                # Index on owner_id for tenant filtering
                await session.run(
                    "CREATE INDEX owner_id_index IF NOT EXISTS "
                    "FOR (n:GraphNode) ON (n.owner_id)"
                )
                # Index on document nodes
                await session.run(
                    "CREATE INDEX document_node_index IF NOT EXISTS "
                    "FOR (n:Document) ON (n.node_id)"
                )
                # Index on entity nodes
                await session.run(
                    "CREATE INDEX entity_node_index IF NOT EXISTS "
                    "FOR (n:Entity) ON (n.name)"
                )
                logger.debug("[GRAPH] Indexes ensured")
        except Exception as e:
            logger.warning("[GRAPH] Failed to create indexes", error=str(e))
    
    # ========================================================================
    # Node Operations
    # ========================================================================
    
    async def upsert_node(
        self,
        label: str,
        properties: Dict[str, Any],
        node_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        visibility: str = "personal",
    ) -> Optional[str]:
        """
        Create or update a graph node.
        
        Args:
            label: Node label (e.g., "Project", "Task", "Person")
            properties: Node properties dict
            node_id: Unique node identifier (defaults to properties["id"])
            owner_id: Owner user ID for multi-tenancy
            visibility: "personal" or "shared"
            
        Returns:
            The node_id if successful, None if graph is unavailable
        """
        if not self._available:
            return None
        
        nid = node_id or properties.get("id", "")
        if not nid:
            logger.warning("[GRAPH] Cannot upsert node without id", label=label)
            return None
        
        try:
            # Sanitize label to prevent injection
            safe_label = self._sanitize_label(label)
            
            # Build properties with metadata
            node_props = {
                "node_id": nid,
                "owner_id": owner_id or "",
                "visibility": visibility,
            }
            # Add user properties (filter out None values)
            for k, v in properties.items():
                if v is not None and k != "id":
                    # Neo4j only supports primitive types and lists of primitives
                    if isinstance(v, (str, int, float, bool)):
                        node_props[k] = v
                    elif isinstance(v, list) and all(isinstance(i, (str, int, float, bool)) for i in v):
                        node_props[k] = v
                    else:
                        # Convert complex types to string
                        node_props[k] = str(v)
            
            async with self._driver.session() as session:
                await session.run(
                    f"MERGE (n:GraphNode:{safe_label} {{node_id: $node_id}}) "
                    f"SET n += $props",
                    node_id=nid,
                    props=node_props,
                )
            
            logger.debug(
                "[GRAPH] Node upserted",
                label=label,
                node_id=nid,
            )
            return nid
        except Exception as e:
            logger.warning(
                "[GRAPH] Failed to upsert node",
                label=label,
                node_id=nid,
                error=str(e),
            )
            return None
    
    async def create_relationship(
        self,
        from_id: str,
        rel_type: str,
        to_id: str,
        properties: Optional[Dict[str, Any]] = None,
        owner_id: Optional[str] = None,
    ) -> bool:
        """
        Create a relationship between two nodes.
        
        When owner_id is provided, both nodes must belong to the same owner
        (or have visibility='shared') for the relationship to be created.
        Internal callers (e.g. sync_data_document_records) that have already
        verified ownership can omit owner_id.
        
        Args:
            from_id: Source node ID
            rel_type: Relationship type (e.g., "BELONGS_TO", "DEPENDS_ON")
            to_id: Target node ID
            properties: Optional relationship properties
            owner_id: Optional owner filter for tenant isolation
            
        Returns:
            True if successful, False otherwise
        """
        if not self._available:
            return False
        
        try:
            safe_rel = self._sanitize_label(rel_type)
            rel_props = properties or {}
            
            # Build owner validation clause
            owner_clause = ""
            params: Dict[str, Any] = {
                "from_id": from_id,
                "to_id": to_id,
                "props": rel_props,
            }
            if owner_id:
                owner_clause = (
                    "WHERE (a.owner_id = $owner_id OR a.visibility = 'shared') "
                    "AND (b.owner_id = $owner_id OR b.visibility = 'shared') "
                )
                params["owner_id"] = owner_id
            
            async with self._driver.session() as session:
                result = await session.run(
                    f"MATCH (a:GraphNode {{node_id: $from_id}}) "
                    f"MATCH (b:GraphNode {{node_id: $to_id}}) "
                    f"{owner_clause}"
                    f"MERGE (a)-[r:{safe_rel}]->(b) "
                    f"SET r += $props "
                    f"RETURN count(r) as created",
                    **params,
                )
                record = await result.single()
                if owner_id and (not record or record["created"] == 0):
                    logger.warning(
                        "[GRAPH] Relationship creation blocked by ownership check",
                        from_id=from_id,
                        to_id=to_id,
                        owner_id=owner_id,
                    )
                    return False
            
            logger.debug(
                "[GRAPH] Relationship created",
                from_id=from_id,
                rel_type=rel_type,
                to_id=to_id,
            )
            return True
        except Exception as e:
            logger.warning(
                "[GRAPH] Failed to create relationship",
                from_id=from_id,
                rel_type=rel_type,
                to_id=to_id,
                error=str(e),
            )
            return False
    
    async def delete_node(
        self,
        node_id: str,
        owner_id: Optional[str] = None,
    ) -> bool:
        """
        Delete a node and all its relationships.
        
        When owner_id is provided, only deletes the node if it belongs to
        the specified owner (or has visibility='shared'). Internal callers
        that have already verified ownership can omit owner_id.
        
        Args:
            node_id: Node identifier to delete
            owner_id: Optional owner filter for tenant isolation
            
        Returns:
            True if successful, False otherwise
        """
        if not self._available:
            return False
        
        try:
            owner_clause = ""
            params: Dict[str, Any] = {"node_id": node_id}
            if owner_id:
                owner_clause = "AND (n.owner_id = $owner_id OR n.visibility = 'shared') "
                params["owner_id"] = owner_id
            
            async with self._driver.session() as session:
                result = await session.run(
                    f"MATCH (n:GraphNode {{node_id: $node_id}}) "
                    f"{owner_clause}"
                    f"DETACH DELETE n "
                    f"RETURN count(n) as deleted",
                    **params,
                )
                record = await result.single()
                if owner_id and (not record or record["deleted"] == 0):
                    logger.warning(
                        "[GRAPH] Node deletion blocked by ownership check",
                        node_id=node_id,
                        owner_id=owner_id,
                    )
                    return False
            
            logger.debug("[GRAPH] Node deleted", node_id=node_id)
            return True
        except Exception as e:
            logger.warning(
                "[GRAPH] Failed to delete node",
                node_id=node_id,
                error=str(e),
            )
            return False
    
    async def delete_relationships(
        self,
        node_id: str,
        rel_type: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> bool:
        """
        Delete relationships for a node.
        
        When owner_id is provided, only deletes relationships where the
        source node belongs to the specified owner.
        
        Args:
            node_id: Node identifier
            rel_type: Optional relationship type to filter (deletes all if None)
            owner_id: Optional owner filter for tenant isolation
            
        Returns:
            True if successful, False otherwise
        """
        if not self._available:
            return False
        
        try:
            owner_clause = ""
            params: Dict[str, Any] = {"node_id": node_id}
            if owner_id:
                owner_clause = "AND (n.owner_id = $owner_id OR n.visibility = 'shared') "
                params["owner_id"] = owner_id
            
            async with self._driver.session() as session:
                if rel_type:
                    safe_rel = self._sanitize_label(rel_type)
                    await session.run(
                        f"MATCH (n:GraphNode {{node_id: $node_id}})-[r:{safe_rel}]-() "
                        f"WHERE true {owner_clause}"
                        f"DELETE r",
                        **params,
                    )
                else:
                    await session.run(
                        f"MATCH (n:GraphNode {{node_id: $node_id}})-[r]-() "
                        f"WHERE true {owner_clause}"
                        f"DELETE r",
                        **params,
                    )
            
            logger.debug(
                "[GRAPH] Relationships deleted",
                node_id=node_id,
                rel_type=rel_type,
            )
            return True
        except Exception as e:
            logger.warning(
                "[GRAPH] Failed to delete relationships",
                node_id=node_id,
                error=str(e),
            )
            return False
    
    # ========================================================================
    # Query Operations
    # ========================================================================
    
    async def query(
        self,
        cypher: str,
        params: Optional[Dict[str, Any]] = None,
        owner_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute a Cypher query and return results.
        
        Args:
            cypher: Cypher query string
            params: Query parameters
            owner_id: Optional owner filter for multi-tenancy
            
        Returns:
            List of result records as dicts
        """
        if not self._available:
            return []
        
        try:
            query_params = params or {}
            if owner_id:
                query_params["_owner_id"] = owner_id
            
            async with self._driver.session() as session:
                result = await session.run(cypher, query_params)
                records = []
                async for record in result:
                    records.append(dict(record))
                return records
        except Exception as e:
            logger.warning(
                "[GRAPH] Query failed",
                cypher=cypher[:200],
                error=str(e),
            )
            return []
    
    async def get_neighbors(
        self,
        node_id: str,
        rel_types: Optional[List[str]] = None,
        depth: int = 1,
        owner_id: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        Get neighbors of a node up to a given depth.
        
        Args:
            node_id: Starting node ID
            rel_types: Optional relationship type filter
            depth: Maximum traversal depth (1-5)
            owner_id: Optional owner filter
            limit: Maximum number of nodes to return
            
        Returns:
            Dict with "nodes" and "relationships" lists
        """
        if not self._available:
            return {"nodes": [], "relationships": []}
        
        depth = min(max(depth, 1), 5)  # Clamp 1-5
        
        try:
            # Build relationship filter
            rel_filter = ""
            if rel_types:
                safe_types = [self._sanitize_label(rt) for rt in rel_types]
                rel_filter = ":" + "|".join(safe_types)
            
            # Build owner filter
            owner_clause = ""
            params: Dict[str, Any] = {"node_id": node_id, "limit": limit}
            if owner_id:
                owner_clause = (
                    "AND (related.owner_id = $owner_id "
                    "OR related.visibility = 'shared')"
                )
                params["owner_id"] = owner_id
            
            cypher = (
                f"MATCH (start:GraphNode {{node_id: $node_id}}) "
                f"CALL apoc.path.subgraphAll(start, {{maxLevel: {depth}, "
                f"relationshipFilter: '{rel_filter.lstrip(':')}'}}) "
                f"YIELD nodes, relationships "
                f"RETURN nodes, relationships LIMIT $limit"
            )
            
            # Fallback to simpler query if APOC is not available
            fallback_cypher = (
                f"MATCH path = (start:GraphNode {{node_id: $node_id}})"
                f"-[r{rel_filter}*1..{depth}]-(related:GraphNode) "
                f"WHERE related.node_id <> start.node_id {owner_clause} "
                f"WITH DISTINCT related, r "
                f"RETURN related LIMIT $limit"
            )
            
            async with self._driver.session() as session:
                try:
                    result = await session.run(cypher, params)
                    records = []
                    async for record in result:
                        records.append(dict(record))
                    
                    if records:
                        return self._format_subgraph(records)
                except Exception:
                    # APOC not available, use fallback
                    pass
                
                # Fallback query
                result = await session.run(fallback_cypher, params)
                nodes = []
                async for record in result:
                    node = record.get("related")
                    if node:
                        nodes.append(dict(node))
                
                return {
                    "nodes": nodes,
                    "relationships": [],
                    "center_node_id": node_id,
                }
        except Exception as e:
            logger.warning(
                "[GRAPH] get_neighbors failed",
                node_id=node_id,
                error=str(e),
            )
            return {"nodes": [], "relationships": []}
    
    async def find_path(
        self,
        from_id: str,
        to_id: str,
        max_depth: int = 5,
        owner_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Find shortest path between two nodes.
        
        When owner_id is provided, both start and end nodes must belong to
        the specified owner, and all intermediate nodes in the path are
        filtered to only include owned/shared nodes.
        
        Args:
            from_id: Start node ID
            to_id: End node ID
            max_depth: Maximum path length
            owner_id: Optional owner filter for tenant isolation
            
        Returns:
            Dict with "nodes" and "relationships" lists
        """
        if not self._available:
            return {"nodes": [], "relationships": []}
        
        try:
            params: Dict[str, Any] = {"from_id": from_id, "to_id": to_id}
            
            # Build owner filter for start/end nodes and path nodes
            start_owner_clause = ""
            path_filter = ""
            if owner_id:
                start_owner_clause = (
                    "WHERE (a.owner_id = $owner_id OR a.visibility = 'shared') "
                    "AND (b.owner_id = $owner_id OR b.visibility = 'shared') "
                )
                path_filter = (
                    "WITH path "
                    "WHERE ALL(n IN nodes(path) WHERE "
                    "n.owner_id = $owner_id OR n.visibility = 'shared') "
                )
                params["owner_id"] = owner_id
            
            cypher = (
                f"MATCH (a:GraphNode {{node_id: $from_id}}), "
                f"(b:GraphNode {{node_id: $to_id}}) "
                f"{start_owner_clause}"
                f"MATCH path = shortestPath((a)-[*..{max_depth}]-(b)) "
                f"{path_filter}"
                f"RETURN [n IN nodes(path) | properties(n)] as nodes, "
                f"[r IN relationships(path) | "
                f"{{type: type(r), from: startNode(r).node_id, to: endNode(r).node_id}}] as rels"
            )
            
            async with self._driver.session() as session:
                result = await session.run(cypher, params)
                record = await result.single()
                if record:
                    return {
                        "nodes": record["nodes"],
                        "relationships": record["rels"],
                    }
                return {"nodes": [], "relationships": []}
        except Exception as e:
            logger.warning(
                "[GRAPH] find_path failed",
                from_id=from_id,
                to_id=to_id,
                error=str(e),
            )
            return {"nodes": [], "relationships": []}
    
    # ========================================================================
    # Bulk Operations (for data document sync)
    # ========================================================================
    
    async def sync_data_document_records(
        self,
        document_id: str,
        document_name: str,
        schema: Optional[Dict] = None,
        records: List[Dict] = None,
        owner_id: str = "",
        visibility: str = "personal",
    ) -> int:
        """
        Sync data document records to graph nodes.
        
        Creates/updates nodes for each record if the schema has graphNode defined,
        and creates relationships based on graphRelationships.
        
        Args:
            document_id: Data document ID
            document_name: Data document name
            schema: Document schema (may contain graphNode/graphRelationships)
            records: List of records to sync
            owner_id: Owner user ID
            visibility: Document visibility
            
        Returns:
            Number of nodes created/updated
        """
        if not self._available or not schema:
            return 0
        
        graph_node_label = schema.get("graphNode")
        if not graph_node_label:
            return 0
        
        records = records or []
        count = 0
        
        # Create a node for the document itself
        await self.upsert_node(
            label="DataDocument",
            properties={
                "id": document_id,
                "name": document_name,
                "doc_type": "data",
            },
            owner_id=owner_id,
            visibility=visibility,
        )
        
        # Create nodes for each record
        for record in records:
            record_id = record.get("id")
            if not record_id:
                continue
            
            node_id = await self.upsert_node(
                label=graph_node_label,
                properties=record,
                node_id=record_id,
                owner_id=owner_id,
                visibility=visibility,
            )
            
            if node_id:
                count += 1
                # Create RECORD_OF relationship to parent document
                await self.create_relationship(
                    from_id=record_id,
                    rel_type="RECORD_OF",
                    to_id=document_id,
                )
        
        # Create relationships based on graphRelationships schema
        graph_rels = schema.get("graphRelationships", [])
        for rel_def in graph_rels:
            source_label = rel_def.get("source_label", graph_node_label)
            target_field = rel_def.get("target_field")
            target_label = rel_def.get("target_label")
            relationship = rel_def.get("relationship")
            
            if not (target_field and relationship):
                continue
            
            for record in records:
                record_id = record.get("id")
                target_id = record.get(target_field)
                if record_id and target_id:
                    await self.create_relationship(
                        from_id=record_id,
                        rel_type=relationship,
                        to_id=target_id,
                    )
        
        logger.info(
            "[GRAPH] Synced data document records",
            document_id=document_id,
            node_label=graph_node_label,
            nodes_created=count,
            relationships_defined=len(graph_rels),
        )
        
        return count
    
    async def delete_document_graph(
        self,
        document_id: str,
        owner_id: Optional[str] = None,
    ) -> bool:
        """
        Delete all graph nodes associated with a data document.
        
        When owner_id is provided, only deletes if the document node
        belongs to the specified owner. Internal callers that have already
        verified ownership via PostgreSQL RLS can omit owner_id.
        
        Args:
            document_id: Data document ID
            owner_id: Optional owner filter for tenant isolation
            
        Returns:
            True if successful
        """
        if not self._available:
            return False
        
        try:
            owner_clause = ""
            params: Dict[str, Any] = {"doc_id": document_id}
            if owner_id:
                owner_clause = "AND (d.owner_id = $owner_id OR d.visibility = 'shared') "
                params["owner_id"] = owner_id
            
            async with self._driver.session() as session:
                # Verify ownership of the document node first (if owner_id given)
                if owner_id:
                    check = await session.run(
                        f"MATCH (d:GraphNode {{node_id: $doc_id}}) "
                        f"WHERE d.owner_id = $owner_id OR d.visibility = 'shared' "
                        f"RETURN count(d) as cnt",
                        **params,
                    )
                    record = await check.single()
                    if not record or record["cnt"] == 0:
                        logger.warning(
                            "[GRAPH] Document graph deletion blocked by ownership check",
                            document_id=document_id,
                            owner_id=owner_id,
                        )
                        return False
                
                # Delete all records that belong to this document
                await session.run(
                    f"MATCH (r:GraphNode)-[:RECORD_OF]->(d:GraphNode {{node_id: $doc_id}}) "
                    f"{owner_clause}"
                    f"DETACH DELETE r",
                    **params,
                )
                # Delete the document node itself
                await session.run(
                    f"MATCH (d:GraphNode {{node_id: $doc_id}}) "
                    f"{owner_clause}"
                    f"DETACH DELETE d",
                    **params,
                )
            
            logger.info(
                "[GRAPH] Deleted document graph",
                document_id=document_id,
            )
            return True
        except Exception as e:
            logger.warning(
                "[GRAPH] Failed to delete document graph",
                document_id=document_id,
                error=str(e),
            )
            return False
    
    # ========================================================================
    # Document Entity Queries
    # ========================================================================
    
    async def get_document_entities(
        self,
        document_id: str,
        owner_id: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        Get all entities connected to a specific document.
        
        Uses direct MENTIONED_IN/KEYWORD_OF relationship traversal
        rather than generic graph expansion, which is more reliable
        for document-specific entity views.
        
        Args:
            document_id: The document's file_id (used as node_id)
            owner_id: Owner filter for multi-tenancy
            limit: Maximum entities to return
            
        Returns:
            Dict with "nodes" and "edges" lists
        """
        if not self._available:
            return {"nodes": [], "edges": []}
        
        try:
            params: Dict[str, Any] = {
                "document_id": document_id,
                "limit": limit,
            }
            
            owner_filter = ""
            if owner_id:
                owner_filter = "AND (d.owner_id = $owner_id OR d.visibility = 'shared')"
                params["owner_id"] = owner_id
            
            # Find the Document node and all entities connected via
            # MENTIONED_IN or KEYWORD_OF relationships
            cypher = (
                f"MATCH (d:Document {{node_id: $document_id}}) {owner_filter} "
                f"OPTIONAL MATCH (e)-[:MENTIONED_IN|KEYWORD_OF]->(d) "
                f"WITH d, collect(DISTINCT e)[0..$limit] as entities "
                f"WITH [d] + entities as allNodes "
                f"UNWIND allNodes as n "
                f"WITH DISTINCT n WHERE n IS NOT NULL "
                f"WITH collect(n) as nodeList "
                f"UNWIND nodeList as n1 "
                f"UNWIND nodeList as n2 "
                f"WITH nodeList, n1, n2 WHERE id(n1) < id(n2) "
                f"OPTIONAL MATCH (n1)-[r]-(n2) "
                f"WITH nodeList, collect(CASE WHEN r IS NOT NULL THEN "
                f"{{type: type(r), from: n1.node_id, to: n2.node_id}} END) as rels "
                f"RETURN [x IN nodeList | properties(x)] as nodes, "
                f"[r IN rels WHERE r IS NOT NULL] as edges"
            )
            
            async with self._driver.session() as session:
                result = await session.run(cypher, params)
                record = await result.single()
                
                if record:
                    nodes = record.get("nodes", [])
                    edges = record.get("edges", [])
                    edges = [e for e in edges if e and e.get("from") and e.get("to")]
                    return {
                        "nodes": nodes,
                        "edges": edges,
                    }
                
                return {"nodes": [], "edges": []}
        except Exception as e:
            logger.warning(
                "[GRAPH] Document entity query failed",
                document_id=document_id,
                error=str(e),
            )
            return {"nodes": [], "edges": []}
    
    # ========================================================================
    # Visualization
    # ========================================================================

    async def compute_project_similarities(
        self,
        owner_id: Optional[str] = None,
        label: str = "StatusProject",
        threshold: float = 0.30,
    ) -> Dict[str, Any]:
        """
        Compute cross-project similarity edges and upsert SIMILAR_TO relationships.

        Similarity combines:
        - tags overlap (Jaccard)
        - team overlap (Jaccard)
        - text overlap across name + description (Jaccard)

        Args:
            owner_id: Optional owner filter for tenant isolation
            label: Graph label for project nodes (default: StatusProject)
            threshold: Minimum similarity score to create an edge

        Returns:
            Counts of created/updated/removed relationships and candidates processed
        """
        if not self._available:
            return {
                "available": False,
                "created": 0,
                "updated": 0,
                "removed": 0,
                "pairs_evaluated": 0,
                "pairs_above_threshold": 0,
            }

        safe_label = self._sanitize_label(label)
        owner_filter = ""
        params: Dict[str, Any] = {}
        if owner_id:
            owner_filter = "WHERE (p.owner_id = $owner_id OR p.visibility = 'shared') "
            params["owner_id"] = owner_id

        try:
            async with self._driver.session() as session:
                # 1) Fetch candidate projects
                project_result = await session.run(
                    f"MATCH (p:{safe_label}) "
                    f"{owner_filter}"
                    f"RETURN p.node_id as node_id, "
                    f"coalesce(p.name, '') as name, "
                    f"coalesce(p.description, '') as description, "
                    f"coalesce(p.tags, []) as tags, "
                    f"coalesce(p.team, []) as team",
                    **params,
                )
                projects = [dict(record) async for record in project_result]

                if len(projects) < 2:
                    return {
                        "available": True,
                        "created": 0,
                        "updated": 0,
                        "removed": 0,
                        "pairs_evaluated": 0,
                        "pairs_above_threshold": 0,
                    }

                created = 0
                updated = 0
                pairs_evaluated = 0
                pairs_above_threshold = 0
                keep_pairs: List[str] = []
                timestamp = datetime.now(timezone.utc).isoformat()

                # 2) Compute pairwise similarity and upsert relationships
                for project_a, project_b in combinations(projects, 2):
                    a_id = project_a.get("node_id")
                    b_id = project_b.get("node_id")
                    if not a_id or not b_id:
                        continue

                    pairs_evaluated += 1

                    tags_score = self._jaccard_similarity(
                        {str(v).strip().lower() for v in project_a.get("tags", []) if str(v).strip()},
                        {str(v).strip().lower() for v in project_b.get("tags", []) if str(v).strip()},
                    )
                    team_score = self._jaccard_similarity(
                        {str(v).strip().lower() for v in project_a.get("team", []) if str(v).strip()},
                        {str(v).strip().lower() for v in project_b.get("team", []) if str(v).strip()},
                    )
                    text_score = self._jaccard_similarity(
                        self._normalize_text_tokens(
                            f"{project_a.get('name', '')} {project_a.get('description', '')}"
                        ),
                        self._normalize_text_tokens(
                            f"{project_b.get('name', '')} {project_b.get('description', '')}"
                        ),
                    )

                    score = round((0.45 * tags_score) + (0.25 * team_score) + (0.30 * text_score), 4)
                    if score < threshold:
                        continue

                    pairs_above_threshold += 1
                    left_id, right_id = sorted([str(a_id), str(b_id)])
                    pair_key = f"{left_id}::{right_id}"
                    keep_pairs.append(pair_key)

                    upsert_params: Dict[str, Any] = {
                        "left_id": left_id,
                        "right_id": right_id,
                        "score": score,
                        "tags_score": round(tags_score, 4),
                        "team_score": round(team_score, 4),
                        "text_score": round(text_score, 4),
                        "computed_at": timestamp,
                    }
                    if owner_id:
                        upsert_params["owner_id"] = owner_id

                    owner_check = ""
                    if owner_id:
                        owner_check = (
                            "WHERE (a.owner_id = $owner_id OR a.visibility = 'shared') "
                            "AND (b.owner_id = $owner_id OR b.visibility = 'shared') "
                        )

                    upsert_result = await session.run(
                        "MATCH (a:GraphNode {node_id: $left_id}) "
                        "MATCH (b:GraphNode {node_id: $right_id}) "
                        f"{owner_check}"
                        "OPTIONAL MATCH (a)-[existing:SIMILAR_TO]->(b) "
                        "WITH a, b, existing "
                        "MERGE (a)-[r:SIMILAR_TO]->(b) "
                        "ON CREATE SET r.created_at = $computed_at "
                        "SET r.updated_at = $computed_at, "
                        "r.similarity_score = $score, "
                        "r.tags_similarity = $tags_score, "
                        "r.team_similarity = $team_score, "
                        "r.text_similarity = $text_score "
                        "RETURN (existing IS NULL) as created",
                        **upsert_params,
                    )
                    upsert_record = await upsert_result.single()
                    if upsert_record and upsert_record.get("created"):
                        created += 1
                    else:
                        updated += 1

                # 3) Remove stale similarity edges that are no longer above threshold
                cleanup_params: Dict[str, Any] = {"keep_pairs": keep_pairs}
                if owner_id:
                    cleanup_params["owner_id"] = owner_id
                    cleanup_owner = (
                        "WHERE (a.owner_id = $owner_id OR a.visibility = 'shared') "
                        "AND (b.owner_id = $owner_id OR b.visibility = 'shared') "
                    )
                else:
                    cleanup_owner = ""

                cleanup_result = await session.run(
                    "MATCH (a:GraphNode)-[r:SIMILAR_TO]->(b:GraphNode) "
                    f"{cleanup_owner}"
                    "WITH r, "
                    "CASE WHEN a.node_id < b.node_id "
                    "THEN a.node_id + '::' + b.node_id "
                    "ELSE b.node_id + '::' + a.node_id END as pair_key "
                    "WHERE NOT pair_key IN $keep_pairs "
                    "DELETE r "
                    "RETURN count(r) as removed",
                    **cleanup_params,
                )
                cleanup_record = await cleanup_result.single()
                removed = int(cleanup_record["removed"]) if cleanup_record and cleanup_record.get("removed") else 0

            logger.info(
                "[GRAPH] Project similarity computation complete",
                owner_id=owner_id,
                threshold=threshold,
                pairs_evaluated=pairs_evaluated,
                pairs_above_threshold=pairs_above_threshold,
                created=created,
                updated=updated,
                removed=removed,
            )
            return {
                "available": True,
                "created": created,
                "updated": updated,
                "removed": removed,
                "pairs_evaluated": pairs_evaluated,
                "pairs_above_threshold": pairs_above_threshold,
            }
        except Exception as e:
            logger.warning(
                "[GRAPH] Failed to compute project similarities",
                owner_id=owner_id,
                threshold=threshold,
                error=str(e),
            )
            return {
                "available": True,
                "created": 0,
                "updated": 0,
                "removed": 0,
                "pairs_evaluated": 0,
                "pairs_above_threshold": 0,
                "error": str(e),
            }
    
    async def get_graph_visualization(
        self,
        center_id: Optional[str] = None,
        label: Optional[str] = None,
        depth: int = 2,
        owner_id: Optional[str] = None,
        limit: int = 100,
        library_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get graph data formatted for frontend visualization.
        
        Returns nodes and edges in a format suitable for
        react-force-graph or vis-network.
        
        Args:
            center_id: Optional center node ID to expand from
            label: Optional label filter
            depth: Traversal depth from center
            owner_id: Owner filter for multi-tenancy
            limit: Maximum nodes to return
            library_ids: Optional list of library IDs to filter (Document nodes only)
            
        Returns:
            Dict with "nodes" and "edges" lists
        """
        if not self._available:
            return {"nodes": [], "edges": []}
        
        try:
            params: Dict[str, Any] = {"limit": limit}
            owner_clause = ""
            if owner_id:
                owner_clause = (
                    "WHERE (n.owner_id = $owner_id "
                    "OR n.visibility = 'shared')"
                )
                params["owner_id"] = owner_id
            
            # Helper: RETURN clause for nodes and edges
            # Return properties and labels separately, merged in post-processing
            node_return = "[x IN nodeList | {props: properties(x), lbls: labels(x)}]"
            edge_collect = (
                "collect(DISTINCT CASE WHEN r IS NOT NULL THEN "
                "{type: type(r), from: startNode(r).node_id, to: endNode(r).node_id, props: properties(r)} END)"
            )
            
            if library_ids:
                # Filter by library: Document nodes with library_id in list + connected entities
                lib_clause = "AND (d.owner_id = $owner_id OR d.visibility = 'shared')" if owner_id else ""
                params["library_ids"] = library_ids
                cypher = (
                    f"MATCH (d:Document) WHERE d.library_id IN $library_ids {lib_clause} "
                    f"WITH d LIMIT $limit "
                    f"OPTIONAL MATCH (e)-[:MENTIONED_IN|KEYWORD_OF]->(d) "
                    f"WITH collect(DISTINCT d) + collect(DISTINCT e) as rawNodes "
                    f"UNWIND rawNodes as n "
                    f"WITH DISTINCT n WHERE n IS NOT NULL "
                    f"WITH collect(n) as nodeList "
                    f"WITH nodeList, [n IN nodeList | n.node_id] as nodeIds "
                    f"UNWIND nodeList as n1 "
                    f"OPTIONAL MATCH (n1)-[r]-(m:GraphNode) WHERE m.node_id IN nodeIds "
                    f"RETURN {node_return} as nodes, "
                    f"{edge_collect} as edges"
                )
            elif center_id:
                # Expand from a center node
                params["center_id"] = center_id
                cypher = (
                    f"MATCH (start:GraphNode {{node_id: $center_id}}) "
                    f"OPTIONAL MATCH path = (start)-[*1..{depth}]-(related:GraphNode) "
                    f"{owner_clause.replace('n.', 'related.')} "
                    f"WITH start, collect(DISTINCT related)[0..$limit] as neighbors "
                    f"WITH [start] + neighbors as nodeList "
                    f"WITH nodeList, [n IN nodeList | n.node_id] as nodeIds "
                    f"UNWIND nodeList as n "
                    f"WITH DISTINCT n, nodeIds "
                    f"WITH collect(DISTINCT n) as nodeList, nodeIds "
                    f"UNWIND nodeList as n1 "
                    f"OPTIONAL MATCH (n1)-[r]-(m:GraphNode) WHERE m.node_id IN nodeIds "
                    f"RETURN {node_return} as nodes, "
                    f"{edge_collect} as edges"
                )
            elif label:
                # Support comma-separated labels (e.g. "StatusProject,StatusTask,StatusUpdate")
                label_parts = [self._sanitize_label(l.strip()) for l in label.split(",") if l.strip()]
                if len(label_parts) == 1:
                    # Single label: direct label match for efficiency
                    safe_label = label_parts[0]
                    cypher = (
                        f"MATCH (n:{safe_label}) {owner_clause} "
                        f"WITH n LIMIT $limit "
                        f"WITH collect(n) as nodeList "
                        f"WITH nodeList, [n IN nodeList | n.node_id] as nodeIds "
                        f"UNWIND nodeList as n1 "
                        f"OPTIONAL MATCH (n1)-[r]-(m:GraphNode) WHERE m.node_id IN nodeIds "
                        f"RETURN {node_return} as nodes, "
                        f"{edge_collect} as edges"
                    )
                else:
                    # Multiple labels: use ANY() filter
                    params["label_filter"] = label_parts
                    multi_owner = ""
                    if owner_id:
                        multi_owner = (
                            "AND (n.owner_id = $owner_id "
                            "OR n.visibility = 'shared') "
                        )
                    cypher = (
                        f"MATCH (n:GraphNode) "
                        f"WHERE ANY(l IN labels(n) WHERE l IN $label_filter) "
                        f"{multi_owner}"
                        f"WITH n LIMIT $limit "
                        f"WITH collect(n) as nodeList "
                        f"WITH nodeList, [n IN nodeList | n.node_id] as nodeIds "
                        f"UNWIND nodeList as n1 "
                        f"OPTIONAL MATCH (n1)-[r]-(m:GraphNode) WHERE m.node_id IN nodeIds "
                        f"RETURN {node_return} as nodes, "
                        f"{edge_collect} as edges"
                    )
            else:
                # Default: get all nodes with their relationships
                cypher = (
                    f"MATCH (n:GraphNode) {owner_clause} "
                    f"WITH n LIMIT $limit "
                    f"WITH collect(n) as nodeList "
                    f"WITH nodeList, [n IN nodeList | n.node_id] as nodeIds "
                    f"UNWIND nodeList as n1 "
                    f"OPTIONAL MATCH (n1)-[r]-(m:GraphNode) WHERE m.node_id IN nodeIds "
                    f"RETURN {node_return} as nodes, "
                    f"{edge_collect} as edges"
                )
            
            async with self._driver.session() as session:
                result = await session.run(cypher, params)
                record = await result.single()
                
                if record:
                    raw_nodes = record.get("nodes", [])
                    raw_edges = record.get("edges", [])
                    # Filter out null edges
                    edges = []
                    for edge in raw_edges:
                        if not edge or not edge.get("from") or not edge.get("to"):
                            continue
                        edge_data = {
                            "type": edge.get("type"),
                            "from": edge.get("from"),
                            "to": edge.get("to"),
                        }
                        props = edge.get("props", {})
                        if isinstance(props, dict):
                            edge_data.update(props)
                        edges.append(edge_data)
                    # Merge labels into node properties
                    nodes = []
                    for item in raw_nodes:
                        if isinstance(item, dict) and "props" in item:
                            # New format: {props: {...}, lbls: [...]}
                            node = dict(item["props"])
                            node["_labels"] = item.get("lbls", [])
                            nodes.append(node)
                        else:
                            # Fallback: plain properties dict
                            nodes.append(item)
                    return {
                        "nodes": nodes,
                        "edges": edges,
                    }
                
                return {"nodes": [], "edges": []}
        except Exception as e:
            logger.warning(
                "[GRAPH] Visualization query failed",
                center_id=center_id,
                label=label,
                error=str(e),
            )
            return {"nodes": [], "edges": []}
    
    # ========================================================================
    # Internal Helpers
    # ========================================================================
    
    @staticmethod
    def _normalize_text_tokens(text: str) -> set[str]:
        """Tokenize text into normalized words for lightweight similarity checks."""
        words = re.findall(r"[a-z0-9]+", (text or "").lower())
        stop_words = {
            "the",
            "and",
            "for",
            "with",
            "from",
            "that",
            "this",
            "into",
            "over",
            "under",
            "about",
            "project",
            "status",
            "report",
        }
        return {w for w in words if len(w) > 2 and w not in stop_words}

    @staticmethod
    def _jaccard_similarity(left: set[str], right: set[str]) -> float:
        """Compute Jaccard similarity (0..1) between two sets."""
        if not left or not right:
            return 0.0
        union_size = len(left | right)
        if union_size == 0:
            return 0.0
        return len(left & right) / union_size

    @staticmethod
    def _sanitize_label(label: str) -> str:
        """Sanitize a label/type string to prevent Cypher injection."""
        # Only allow alphanumeric and underscore
        return "".join(c for c in label if c.isalnum() or c == "_")
    
    def _format_subgraph(self, records: List[Dict]) -> Dict[str, Any]:
        """Format APOC subgraph results into a standard format."""
        all_nodes = []
        all_rels = []
        
        for record in records:
            nodes = record.get("nodes", [])
            rels = record.get("relationships", [])
            
            for node in nodes:
                all_nodes.append(dict(node) if hasattr(node, "__iter__") else node)
            
            for rel in rels:
                all_rels.append({
                    "type": rel.type if hasattr(rel, "type") else str(rel),
                    "from": rel.start_node.get("node_id", "") if hasattr(rel, "start_node") else "",
                    "to": rel.end_node.get("node_id", "") if hasattr(rel, "end_node") else "",
                })
        
        return {
            "nodes": all_nodes,
            "relationships": all_rels,
        }


# Singleton instance (initialized lazily)
_graph_service: Optional[GraphService] = None


async def get_graph_service() -> GraphService:
    """
    Get or create the singleton GraphService instance.
    
    Returns:
        GraphService instance (may not be connected if Neo4j is unavailable)
    """
    global _graph_service
    if _graph_service is None:
        _graph_service = GraphService()
        await _graph_service.connect()
    return _graph_service
