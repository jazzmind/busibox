"""
Graph Search Service for Neo4j-enhanced search.

Provides graph-based search operations:
- Find entities related to search results (Graph-RAG context expansion)
- Find paths between entities
- Direct graph queries
- Expand search results with graph context

All methods degrade gracefully if Neo4j is unavailable.
"""

import os
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()

# Neo4j driver is optional
try:
    from neo4j import AsyncGraphDatabase
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False


class GraphSearchService:
    """
    Graph-enhanced search service using Neo4j.
    
    Provides supplementary graph context for vector/keyword search results.
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize graph search service.
        
        Args:
            config: Configuration dict with neo4j_uri, neo4j_user, neo4j_password
        """
        config = config or {}
        self._uri = config.get("neo4j_uri", os.getenv("NEO4J_URI", ""))
        self._user = config.get("neo4j_user", os.getenv("NEO4J_USER", "neo4j"))
        self._password = config.get("neo4j_password", os.getenv("NEO4J_PASSWORD", ""))
        self._driver = None
        self._available = False
    
    @property
    def available(self) -> bool:
        return self._available
    
    async def connect(self) -> bool:
        """Connect to Neo4j. Returns True if successful."""
        if not NEO4J_AVAILABLE:
            logger.info("[GRAPH-SEARCH] neo4j driver not installed, graph search disabled")
            return False
        
        if not self._uri:
            logger.info("[GRAPH-SEARCH] NEO4J_URI not configured, graph search disabled")
            return False
        
        try:
            self._driver = AsyncGraphDatabase.driver(
                self._uri,
                auth=(self._user, self._password),
                max_connection_pool_size=10,
                connection_acquisition_timeout=5.0,
            )
            await self._driver.verify_connectivity()
            self._available = True
            logger.info("[GRAPH-SEARCH] Connected to Neo4j", uri=self._uri)
            return True
        except Exception as e:
            logger.warning(
                "[GRAPH-SEARCH] Failed to connect to Neo4j",
                error=str(e),
            )
            self._available = False
            return False
    
    async def disconnect(self):
        """Close Neo4j connection."""
        if self._driver:
            try:
                await self._driver.close()
            except Exception:
                pass
            self._driver = None
            self._available = False
    
    async def find_related_entities(
        self,
        query: str,
        user_id: str,
        readable_role_ids: Optional[List[str]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Find graph entities related to a search query.
        
        Searches entity names for matches with the query terms.
        
        Args:
            query: Search query string
            user_id: User ID for access control
            readable_role_ids: Role IDs the user can read
            limit: Max entities to return
            
        Returns:
            List of matching entities with their relationships
        """
        if not self._available:
            return []
        
        try:
            # Split query into terms for fuzzy matching
            terms = [t.strip().lower() for t in query.split() if len(t.strip()) > 2]
            if not terms:
                return []
            
            # Build CONTAINS conditions for entity name matching
            where_clauses = " OR ".join([f"toLower(e.name) CONTAINS $term{i}" for i in range(len(terms))])
            params: Dict[str, Any] = {"user_id": user_id, "limit": limit}
            for i, term in enumerate(terms):
                params[f"term{i}"] = term
            
            # Access control: personal nodes or shared nodes
            access_clause = "(e.owner_id = $user_id OR e.visibility = 'shared')"
            
            cypher = (
                f"MATCH (e:GraphNode) "
                f"WHERE ({where_clauses}) AND {access_clause} "
                f"OPTIONAL MATCH (e)-[r]-(related:GraphNode) "
                f"WHERE {access_clause.replace('e.', 'related.')} "
                f"RETURN e, collect(DISTINCT {{node: properties(related), rel_type: type(r)}}) as connections "
                f"LIMIT $limit"
            )
            
            async with self._driver.session() as session:
                result = await session.run(cypher, params)
                entities = []
                async for record in result:
                    entity_node = record["e"]
                    connections = record.get("connections", [])
                    entities.append({
                        "entity": dict(entity_node),
                        "connections": [c for c in connections if c.get("node")],
                    })
                return entities
        except Exception as e:
            logger.warning("[GRAPH-SEARCH] find_related_entities failed", error=str(e))
            return []
    
    async def expand_context(
        self,
        document_ids: List[str],
        user_id: str,
        depth: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Expand search results with graph context (Graph-RAG pattern).
        
        Given a list of document IDs from vector/keyword search results,
        find related entities and documents through graph traversal.
        
        Args:
            document_ids: List of document file_ids from search results
            user_id: User ID for access control
            depth: Traversal depth from documents
            limit: Max related nodes to return
            
        Returns:
            Dict with related_entities, related_documents, and graph_context
        """
        if not self._available or not document_ids:
            return {"related_entities": [], "related_documents": [], "graph_context": ""}
        
        try:
            params = {
                "doc_ids": document_ids,
                "user_id": user_id,
                "limit": limit,
            }
            
            # Find entities mentioned in the matched documents
            cypher = (
                "MATCH (entity:GraphNode)-[:MENTIONED_IN]->(doc:GraphNode) "
                "WHERE doc.node_id IN $doc_ids "
                "AND (entity.owner_id = $user_id OR entity.visibility = 'shared') "
                "WITH entity, count(doc) as doc_count "
                "ORDER BY doc_count DESC "
                "LIMIT $limit "
                "OPTIONAL MATCH (entity)-[:MENTIONED_IN]->(other_doc:GraphNode) "
                "WHERE NOT other_doc.node_id IN $doc_ids "
                "AND (other_doc.owner_id = $user_id OR other_doc.visibility = 'shared') "
                "RETURN properties(entity) as entity, doc_count, "
                "collect(DISTINCT properties(other_doc))[0..5] as other_documents"
            )
            
            async with self._driver.session() as session:
                result = await session.run(cypher, params)
                
                related_entities = []
                related_documents = []
                seen_docs = set(document_ids)
                
                async for record in result:
                    entity = record["entity"]
                    doc_count = record["doc_count"]
                    other_docs = record.get("other_documents", [])
                    
                    related_entities.append({
                        "name": entity.get("name", ""),
                        "type": entity.get("entity_type", "Entity"),
                        "relevance": doc_count,
                        "context": entity.get("context", ""),
                    })
                    
                    for doc in other_docs:
                        doc_id = doc.get("node_id", "")
                        if doc_id and doc_id not in seen_docs:
                            seen_docs.add(doc_id)
                            related_documents.append({
                                "id": doc_id,
                                "name": doc.get("name", ""),
                                "relationship": f"Also mentions {entity.get('name', '')}",
                            })
                
                # Build text context for RAG
                context_parts = []
                if related_entities:
                    entity_names = [e["name"] for e in related_entities[:5]]
                    context_parts.append(f"Related entities: {', '.join(entity_names)}")
                if related_documents:
                    doc_names = [d["name"] for d in related_documents[:3]]
                    context_parts.append(f"Related documents: {', '.join(doc_names)}")
                
                return {
                    "related_entities": related_entities,
                    "related_documents": related_documents,
                    "graph_context": ". ".join(context_parts) if context_parts else "",
                }
        except Exception as e:
            logger.warning("[GRAPH-SEARCH] expand_context failed", error=str(e))
            return {"related_entities": [], "related_documents": [], "graph_context": ""}
    
    async def find_path(
        self,
        from_entity: str,
        to_entity: str,
        user_id: str,
        max_depth: int = 5,
    ) -> Dict[str, Any]:
        """
        Find shortest path between two entities.
        
        Args:
            from_entity: Source entity name or ID
            to_entity: Target entity name or ID
            user_id: User ID for access control
            max_depth: Maximum path length
            
        Returns:
            Dict with nodes and relationships in the path
        """
        if not self._available:
            return {"nodes": [], "relationships": []}
        
        try:
            params = {
                "from_name": from_entity.lower(),
                "to_name": to_entity.lower(),
                "user_id": user_id,
            }
            
            cypher = (
                f"MATCH (a:GraphNode), (b:GraphNode) "
                f"WHERE (toLower(a.name) = $from_name OR a.node_id = $from_name) "
                f"AND (toLower(b.name) = $to_name OR b.node_id = $to_name) "
                f"AND (a.owner_id = $user_id OR a.visibility = 'shared') "
                f"AND (b.owner_id = $user_id OR b.visibility = 'shared') "
                f"MATCH path = shortestPath((a)-[*..{max_depth}]-(b)) "
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
            logger.warning("[GRAPH-SEARCH] find_path failed", error=str(e))
            return {"nodes": [], "relationships": []}
    
    async def graph_query(
        self,
        query_text: str,
        user_id: str,
        entity_type: Optional[str] = None,
        depth: int = 2,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        High-level graph query for search endpoint.
        
        Finds entities matching the query and returns their graph neighborhood.
        
        Args:
            query_text: Natural language query
            user_id: User ID for access control
            entity_type: Optional entity type filter
            depth: Traversal depth
            limit: Max results
            
        Returns:
            Dict with nodes and edges for visualization
        """
        if not self._available:
            return {"nodes": [], "edges": [], "query": query_text}
        
        try:
            terms = [t.strip().lower() for t in query_text.split() if len(t.strip()) > 2]
            if not terms:
                return {"nodes": [], "edges": [], "query": query_text}
            
            # Build match conditions
            where_parts = []
            params: Dict[str, Any] = {"user_id": user_id, "limit": limit}
            
            for i, term in enumerate(terms):
                where_parts.append(f"toLower(n.name) CONTAINS $term{i}")
                params[f"term{i}"] = term
            
            name_clause = " OR ".join(where_parts)
            access_clause = "(n.owner_id = $user_id OR n.visibility = 'shared')"
            
            type_clause = ""
            if entity_type:
                safe_type = "".join(c for c in entity_type if c.isalnum() or c == "_")
                type_clause = f"AND n:{safe_type}"
            
            cypher = (
                f"MATCH (n:GraphNode) "
                f"WHERE ({name_clause}) AND {access_clause} {type_clause} "
                f"WITH n LIMIT $limit "
                f"OPTIONAL MATCH path = (n)-[r*1..{depth}]-(related:GraphNode) "
                f"WHERE (related.owner_id = $user_id OR related.visibility = 'shared') "
                f"WITH n, collect(DISTINCT related)[0..50] as neighbors, "
                f"collect(DISTINCT r) as all_rels "
                f"RETURN collect(DISTINCT properties(n)) + "
                f"[x IN neighbors | properties(x)] as nodes"
            )
            
            async with self._driver.session() as session:
                result = await session.run(cypher, params)
                record = await result.single()
                
                nodes = record["nodes"] if record else []
                
                # Get edges between returned nodes
                if nodes:
                    node_ids = [n.get("node_id", "") for n in nodes if n.get("node_id")]
                    if node_ids:
                        edge_cypher = (
                            "MATCH (a:GraphNode)-[r]->(b:GraphNode) "
                            "WHERE a.node_id IN $node_ids AND b.node_id IN $node_ids "
                            "RETURN DISTINCT type(r) as type, a.node_id as from_id, b.node_id as to_id"
                        )
                        edge_result = await session.run(edge_cypher, {"node_ids": node_ids})
                        edges = []
                        async for edge_record in edge_result:
                            edges.append({
                                "type": edge_record["type"],
                                "from": edge_record["from_id"],
                                "to": edge_record["to_id"],
                            })
                    else:
                        edges = []
                else:
                    edges = []
                
                return {
                    "nodes": nodes,
                    "edges": edges,
                    "query": query_text,
                }
        except Exception as e:
            logger.warning("[GRAPH-SEARCH] graph_query failed", error=str(e))
            return {"nodes": [], "edges": [], "query": query_text}
