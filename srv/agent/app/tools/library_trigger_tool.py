"""
Library Trigger Tool for AI Agents.

Allows agents to create library triggers that automatically fire when
documents complete processing in a specific library. This enables
automated extraction pipelines.

Use cases:
- Schema builder agent setting up extraction for a library
- Users requesting automated document processing via chat
"""

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Tool, RunContext

from app.agents.core import BusiboxDeps

logger = logging.getLogger(__name__)


# =============================================================================
# Input/Output Schemas
# =============================================================================

class CreateLibraryTriggerInput(BaseModel):
    """Input for creating a library trigger."""
    library_id: str = Field(description="UUID of the library to watch for new documents")
    name: str = Field(description="Human-readable name for the trigger (e.g., 'Resume Extractor')")
    description: Optional[str] = Field(
        default=None,
        description="Description of what this trigger does"
    )
    agent_id: str = Field(
        description="UUID of the agent to execute when a document completes. Use the schema-builder agent's own ID or another extraction agent."
    )
    prompt: str = Field(
        description="Prompt/instructions for the agent when processing documents. Should include extraction instructions."
    )
    schema_document_id: Optional[str] = Field(
        default=None,
        description="UUID of the data document containing the extraction schema. The agent will use this schema to structure extracted data."
    )


class CreateLibraryTriggerOutput(BaseModel):
    """Output from creating a library trigger."""
    success: bool = Field(description="Whether creation succeeded")
    trigger_id: Optional[str] = Field(description="UUID of the created trigger")
    name: str = Field(description="Name of the trigger")
    library_id: str = Field(description="Library being watched")
    message: str = Field(description="Status message")
    error: Optional[str] = Field(default=None, description="Error message if failed")


# =============================================================================
# Tool Functions
# =============================================================================

async def create_library_trigger(
    ctx: Any,  # RunContext[BusiboxDeps]
    library_id: str,
    name: str,
    agent_id: str,
    prompt: str,
    description: Optional[str] = None,
    schema_document_id: Optional[str] = None,
) -> CreateLibraryTriggerOutput:
    """
    Create a library trigger that fires when documents complete processing.
    
    When a document is uploaded to the specified library and finishes processing
    (parsing, chunking, embedding), the configured agent will automatically run
    with the document content and extraction instructions.
    
    Args:
        ctx: Agent context with dependencies
        library_id: Library UUID to watch
        name: Trigger name
        agent_id: Agent UUID to execute
        prompt: Instructions for the agent
        description: Optional description
        schema_document_id: Optional schema document UUID
        
    Returns:
        CreateLibraryTriggerOutput with trigger details
    """
    logger.info(f"Creating library trigger '{name}' for library {library_id}")
    
    try:
        deps = ctx.deps if hasattr(ctx, 'deps') else None
        if not deps:
            return CreateLibraryTriggerOutput(
                success=False,
                trigger_id=None,
                name=name,
                library_id=library_id,
                message="Failed to create trigger",
                error="No dependencies available",
            )
        
        # Use the BusiboxClient to call the data-api
        client = deps.get_client()
        
        payload = {
            "name": name,
            "description": description,
            "agentId": agent_id,
            "prompt": prompt,
            "schemaDocumentId": schema_document_id,
        }
        
        # Call data-api: POST /libraries/{library_id}/triggers
        response = await client.post(
            f"/libraries/{library_id}/triggers",
            json=payload,
        )
        
        if response.status_code in (200, 201):
            data = response.json().get("data", {})
            trigger_id = data.get("id")
            
            logger.info(f"Library trigger created: {trigger_id}")
            
            return CreateLibraryTriggerOutput(
                success=True,
                trigger_id=trigger_id,
                name=name,
                library_id=library_id,
                message=f"Library trigger '{name}' created successfully. When documents are uploaded to this library and finish processing, the agent will automatically extract structured data.",
            )
        else:
            error_data = response.json() if response.status_code < 500 else {}
            error_msg = error_data.get("error", f"HTTP {response.status_code}")
            
            return CreateLibraryTriggerOutput(
                success=False,
                trigger_id=None,
                name=name,
                library_id=library_id,
                message="Failed to create trigger",
                error=error_msg,
            )
    
    except Exception as e:
        logger.error(f"Failed to create library trigger: {e}", exc_info=True)
        return CreateLibraryTriggerOutput(
            success=False,
            trigger_id=None,
            name=name,
            library_id=library_id,
            message="Failed to create trigger",
            error=str(e),
        )


# =============================================================================
# PydanticAI Tool Objects
# =============================================================================

create_library_trigger_tool = Tool(
    create_library_trigger,
    takes_ctx=True,
    name="create_library_trigger",
    description=(
        "Create a library trigger that automatically fires an agent when documents "
        "complete processing in a specific library. Use this to set up automated "
        "extraction pipelines. The agent will receive the document's markdown content "
        "and extraction schema when triggered."
    ),
)
