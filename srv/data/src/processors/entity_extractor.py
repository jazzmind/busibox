"""
Entity Extractor for Knowledge Graph construction.

Extracts named entities (people, organizations, technologies, concepts)
from document text using LLM and creates graph nodes and relationships.

This is an optional pipeline step in the worker, controlled by
processing_config["entity_extraction_enabled"]. When enabled, it:

1. Sends text chunks to LLM for entity extraction
2. Creates graph nodes for each unique entity
3. Creates MENTIONED_IN relationships between entities and the document
4. Creates RELATED_TO relationships between co-occurring entities

Entity types:
- Person: Named individuals
- Organization: Companies, teams, departments
- Technology: Software, tools, platforms, languages
- Concept: Abstract ideas, methodologies, processes
- Location: Places, regions, countries
- Project: Named projects, initiatives

Usage:
    extractor = EntityExtractor(litellm_base_url, litellm_api_key)
    entities = await extractor.extract_entities(text, file_id)
    # Returns list of {"name": ..., "type": ..., "context": ...}
"""

import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple

import structlog

logger = structlog.get_logger()

# LLM is optional
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


# Prompt template for entity extraction
ENTITY_EXTRACTION_PROMPT = """Extract named entities and key concepts/keywords from the following text. Return a JSON array of entities.

Each entity should have:
- "name": The entity name (normalized, title case)
- "type": One of: Person, Organization, Technology, Concept, Location, Project, Keyword
- "context": A brief phrase describing how the entity appears in the text

For "Keyword" type: extract important terms, topics, and concepts that summarize the document (e.g., "machine learning", "budget planning", "compliance"). These should be meaningful phrases or single important terms, not generic words like "the" or "and".

Only extract clearly named entities and significant keywords. Skip generic terms.
Return ONLY valid JSON array, no other text.

Text:
{text}

JSON array of entities:"""


class EntityExtractor:
    """
    Extracts named entities from document text using LLM.
    
    Entities are used to build knowledge graphs in Neo4j.
    """
    
    def __init__(
        self,
        litellm_base_url: Optional[str] = None,
        litellm_api_key: Optional[str] = None,
        model: str = "agent",
        max_text_length: int = 4000,
    ):
        """
        Initialize entity extractor.
        
        Args:
            litellm_base_url: LiteLLM API base URL
            litellm_api_key: LiteLLM API key
            model: Model name for extraction
            max_text_length: Max characters per extraction request
        """
        self._base_url = litellm_base_url or os.getenv("LITELLM_BASE_URL", "http://litellm:4000")
        self._api_key = litellm_api_key or os.getenv("LITELLM_API_KEY", "")
        self._model = model
        self._max_text_length = max_text_length
    
    async def extract_entities(
        self,
        text: str,
        file_id: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """
        Extract entities from text using LLM.
        
        Args:
            text: Document text to extract entities from
            file_id: Optional file ID for logging
            
        Returns:
            List of entity dicts with name, type, context
        """
        if not HTTPX_AVAILABLE:
            logger.warning("[ENTITY] httpx not available, skipping extraction")
            return []
        
        if not text or len(text.strip()) < 50:
            return []
        
        # Truncate very long texts - extract from first portion
        extract_text = text[:self._max_text_length]
        
        try:
            prompt = ENTITY_EXTRACTION_PROMPT.format(text=extract_text)
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self._base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.1,
                        "max_tokens": 2000,
                    },
                )
                response.raise_for_status()
                
                result = response.json()
                content = result["choices"][0]["message"]["content"].strip()
                
                # Parse JSON response
                entities = self._parse_entities(content)
                
                # Deduplicate by normalized name
                entities = self._deduplicate(entities)
                
                logger.info(
                    "[ENTITY] Extracted entities",
                    file_id=file_id,
                    entity_count=len(entities),
                    types=[e["type"] for e in entities],
                )
                
                return entities
        except Exception as e:
            logger.warning(
                "[ENTITY] Entity extraction failed",
                file_id=file_id,
                error=str(e),
            )
            return []
    
    async def extract_and_store_graph(
        self,
        text: str,
        file_id: str,
        filename: str,
        owner_id: str,
        visibility: str = "personal",
        graph_service=None,
        library_id: Optional[str] = None,
    ) -> int:
        """
        Extract entities and store them in the graph database.
        
        Creates:
        - A Document node for the file (with library_id for filtering)
        - Entity nodes for each extracted entity
        - MENTIONED_IN relationships (Entity -> Document)
        - KEYWORD_OF relationships (Keyword -> Document)
        - RELATED_TO and CO_OCCURS_WITH between co-occurring entities
        
        Args:
            text: Document text
            file_id: File ID
            filename: Original filename
            owner_id: Owner user ID
            visibility: Document visibility
            graph_service: GraphService instance
            library_id: Optional library ID for graph filtering
            
        Returns:
            Number of entities extracted
        """
        if not graph_service or not graph_service.available:
            return 0
        
        entities = await self.extract_entities(text, file_id)
        if not entities:
            return 0
        
        # Create document node with library_id for filtering
        doc_props: Dict[str, Any] = {
            "id": file_id,
            "name": filename,
            "doc_type": "file",
        }
        if library_id:
            doc_props["library_id"] = library_id
        
        await graph_service.upsert_node(
            label="Document",
            properties=doc_props,
            owner_id=owner_id,
            visibility=visibility,
        )
        
        # Create entity nodes and relationships
        entity_ids = []
        keyword_ids = []
        for entity in entities:
            entity_id = f"entity:{entity['type'].lower()}:{entity['name'].lower().replace(' ', '_')}"
            
            await graph_service.upsert_node(
                label=entity["type"],
                properties={
                    "id": entity_id,
                    "name": entity["name"],
                    "entity_type": entity["type"],
                    "context": entity.get("context", ""),
                },
                node_id=entity_id,
                owner_id=owner_id,
                visibility=visibility,
            )
            
            if entity["type"] == "Keyword":
                # Keyword KEYWORD_OF Document
                await graph_service.create_relationship(
                    from_id=entity_id,
                    rel_type="KEYWORD_OF",
                    to_id=file_id,
                )
                keyword_ids.append(entity_id)
            else:
                # Entity MENTIONED_IN Document
                await graph_service.create_relationship(
                    from_id=entity_id,
                    rel_type="MENTIONED_IN",
                    to_id=file_id,
                )
            
            entity_ids.append(entity_id)
        
        # Create CO_OCCURS_WITH between keywords in the same document
        for i, kid1 in enumerate(keyword_ids):
            for kid2 in keyword_ids[i + 1:]:
                await graph_service.create_relationship(
                    from_id=kid1,
                    rel_type="CO_OCCURS_WITH",
                    to_id=kid2,
                    properties={"source_document": file_id},
                )
        
        # Create RELATED_TO between co-occurring non-keyword entities (within same document)
        # Only link entities of different types to avoid noise
        non_keyword_ids = [(i, eid) for i, eid in enumerate(entity_ids) if entities[i]["type"] != "Keyword"]
        for idx, (i, eid1) in enumerate(non_keyword_ids):
            for j, eid2 in non_keyword_ids[idx + 1:]:
                type1 = entities[i]["type"]
                type2 = entities[j]["type"]
                if type1 != type2:
                    await graph_service.create_relationship(
                        from_id=eid1,
                        rel_type="RELATED_TO",
                        to_id=eid2,
                        properties={"source_document": file_id},
                    )
        
        logger.info(
            "[ENTITY] Stored entities in graph",
            file_id=file_id,
            entity_count=len(entities),
        )
        
        return len(entities)
    
    def _parse_entities(self, content: str) -> List[Dict[str, str]]:
        """Parse LLM response into entity list."""
        # Try to extract JSON array from response
        content = content.strip()
        
        # Handle markdown code blocks
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        
        try:
            entities = json.loads(content)
            if isinstance(entities, list):
                valid = []
                for e in entities:
                    if isinstance(e, dict) and "name" in e and "type" in e:
                        # Normalize type
                        e["type"] = self._normalize_type(e["type"])
                        if e["type"]:
                            valid.append(e)
                return valid
        except json.JSONDecodeError:
            pass
        
        return []
    
    def _normalize_type(self, entity_type: str) -> str:
        """Normalize entity type to one of the allowed types."""
        type_map = {
            "person": "Person",
            "people": "Person",
            "individual": "Person",
            "organization": "Organization",
            "company": "Organization",
            "org": "Organization",
            "team": "Organization",
            "department": "Organization",
            "technology": "Technology",
            "tech": "Technology",
            "software": "Technology",
            "tool": "Technology",
            "platform": "Technology",
            "language": "Technology",
            "framework": "Technology",
            "concept": "Concept",
            "methodology": "Concept",
            "process": "Concept",
            "idea": "Concept",
            "location": "Location",
            "place": "Location",
            "city": "Location",
            "country": "Location",
            "region": "Location",
            "project": "Project",
            "initiative": "Project",
            "program": "Project",
            "keyword": "Keyword",
            "key concept": "Keyword",
            "topic": "Keyword",
            "term": "Keyword",
        }
        
        normalized = type_map.get(entity_type.lower().strip(), "")
        if not normalized:
            # Try exact match
            allowed = {"Person", "Organization", "Technology", "Concept", "Location", "Project", "Keyword"}
            if entity_type in allowed:
                return entity_type
        return normalized
    
    def _deduplicate(self, entities: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Remove duplicate entities by normalized name."""
        seen: Set[str] = set()
        unique = []
        for e in entities:
            key = f"{e['type']}:{e['name'].lower().strip()}"
            if key not in seen:
                seen.add(key)
                unique.append(e)
        return unique
