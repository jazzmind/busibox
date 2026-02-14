"""
Extraction Schema Tool for AI Agents.

Creates data documents with schemas optimized for automated document extraction.
Includes graph node and relationship configuration for knowledge graph population.

Use cases:
- Schema builder agent creating extraction schemas
- Setting up automated document processing pipelines
- Creating graph-enabled data stores for extracted entities
"""

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Tool, RunContext

from app.agents.core import BusiboxDeps

logger = logging.getLogger(__name__)


# =============================================================================
# Input/Output Schemas
# =============================================================================

class GraphRelationshipDef(BaseModel):
    """Definition for a graph relationship."""
    source_label: str = Field(description="Source node label")
    target_field: str = Field(description="Field in the record that provides the target value")
    target_label: str = Field(description="Target node label in the graph")
    relationship: str = Field(description="Relationship type (e.g., RESUME_OF, WORKS_AT)")


class CreateExtractionSchemaInput(BaseModel):
    """Input for creating an extraction schema data document."""
    name: str = Field(description="Name for the data document (e.g., 'Parsed Resumes')")
    item_label: str = Field(description="Label for individual records (e.g., 'Resume', 'RFP')")
    graph_node_label: Optional[str] = Field(
        default=None,
        description="Neo4j node label for graph sync (e.g., 'Resume'). If set, records auto-sync to graph."
    )
    fields: Dict[str, Any] = Field(
        description="Schema field definitions. Each key is a field name, value is an object with 'type' (string, integer, number, boolean, array, enum, datetime), 'required' (bool), and optional 'description'."
    )
    graph_relationships: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="Graph relationship definitions: [{source_label, target_field, target_label, relationship}]"
    )
    description: Optional[str] = Field(
        default=None,
        description="Description of the schema's purpose"
    )


class CreateExtractionSchemaOutput(BaseModel):
    """Output from creating an extraction schema."""
    success: bool = Field(description="Whether creation succeeded")
    document_id: Optional[str] = Field(description="UUID of the created data document")
    name: str = Field(description="Document name")
    field_count: int = Field(default=0, description="Number of schema fields")
    message: str = Field(description="Status message")
    error: Optional[str] = Field(default=None, description="Error message if failed")


# =============================================================================
# Tool Functions
# =============================================================================

async def create_extraction_schema(
    ctx: Any,  # RunContext[BusiboxDeps]
    name: str,
    item_label: str,
    fields: Dict[str, Any],
    graph_node_label: Optional[str] = None,
    graph_relationships: Optional[List[Dict[str, str]]] = None,
    description: Optional[str] = None,
) -> CreateExtractionSchemaOutput:
    """
    Create a data document with an extraction schema for automated document processing.
    
    The schema defines the structure of data that will be extracted from documents.
    When combined with a library trigger, this enables automated extraction pipelines:
    1. Document uploaded to library
    2. Document processed (parsed, chunked, embedded)
    3. Agent triggered with document content + this schema
    4. Agent extracts structured data and inserts records
    5. Records auto-sync to Neo4j graph (if graphNode is configured)
    
    Args:
        ctx: Agent context with dependencies
        name: Data document name
        item_label: Label for records
        fields: Schema field definitions
        graph_node_label: Optional Neo4j node label
        graph_relationships: Optional graph relationship definitions
        description: Optional description
        
    Returns:
        CreateExtractionSchemaOutput with document details
    """
    logger.info(f"Creating extraction schema '{name}' with {len(fields)} fields")
    
    try:
        deps = ctx.deps if hasattr(ctx, 'deps') else None
        if not deps:
            return CreateExtractionSchemaOutput(
                success=False,
                document_id=None,
                name=name,
                message="Failed to create schema",
                error="No dependencies available",
            )
        
        # Build the schema object in the format expected by the data-api
        schema = {
            "displayName": name,
            "itemLabel": item_label,
            "fields": fields,
        }
        
        if description:
            schema["description"] = description
        
        if graph_node_label:
            schema["graphNode"] = graph_node_label
        
        if graph_relationships:
            schema["graphRelationships"] = graph_relationships
        
        # Use the BusiboxClient to call the data-api
        client = deps.get_client()
        
        # Create a data document with the schema
        payload = {
            "name": name,
            "schema": schema,
            "visibility": "shared",  # Extraction schemas should be shared
        }
        
        response = await client.post(
            "/data",
            json=payload,
        )
        
        if response.status_code in (200, 201):
            data = response.json()
            document_id = data.get("document_id") or data.get("data", {}).get("document_id")
            
            logger.info(f"Extraction schema created: {document_id}")
            
            graph_info = ""
            if graph_node_label:
                graph_info = f" Records will auto-sync to Neo4j as '{graph_node_label}' nodes."
            if graph_relationships:
                rel_names = [r.get("relationship", "?") for r in graph_relationships]
                graph_info += f" Relationships: {', '.join(rel_names)}."
            
            return CreateExtractionSchemaOutput(
                success=True,
                document_id=document_id,
                name=name,
                field_count=len(fields),
                message=f"Extraction schema '{name}' created with {len(fields)} fields.{graph_info}",
            )
        else:
            error_data = response.json() if response.status_code < 500 else {}
            error_msg = error_data.get("error", f"HTTP {response.status_code}")
            
            return CreateExtractionSchemaOutput(
                success=False,
                document_id=None,
                name=name,
                message="Failed to create schema",
                error=error_msg,
            )
    
    except Exception as e:
        logger.error(f"Failed to create extraction schema: {e}", exc_info=True)
        return CreateExtractionSchemaOutput(
            success=False,
            document_id=None,
            name=name,
            message="Failed to create schema",
            error=str(e),
        )


# =============================================================================
# PydanticAI Tool Objects
# =============================================================================

create_extraction_schema_tool = Tool(
    create_extraction_schema,
    takes_ctx=True,
    name="create_extraction_schema",
    description=(
        "Create a data document with an extraction schema for automated document processing. "
        "The schema defines fields to extract from documents and optionally configures "
        "graph node labels and relationships for automatic Neo4j knowledge graph population. "
        "Use this when setting up a document extraction pipeline."
    ),
)
