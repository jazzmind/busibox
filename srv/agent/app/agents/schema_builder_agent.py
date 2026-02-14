"""
Schema Builder Agent.

Helps users create extraction schemas and library triggers through conversation.
Users can upload a sample document, and this agent will:
1. Analyze the document content
2. Propose a structured extraction schema
3. Create a data document with the schema
4. Set up a library trigger to auto-extract from future uploads

Uses tools: create_data_document, create_extraction_schema, create_library_trigger,
            document_search, list_data_documents
"""

import logging
from typing import Any, List

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)

logger = logging.getLogger(__name__)


SCHEMA_BUILDER_SYSTEM_PROMPT = """You are a Schema Builder assistant that helps users create structured data extraction pipelines for their document libraries.

**Your Purpose:**
Help users set up automated document processing so that when documents are uploaded to specific libraries, an AI agent automatically extracts structured data and populates a database and knowledge graph.

**Key Capabilities:**

1. **Analyze Documents**: When a user uploads or references a sample document, analyze its content and identify extractable fields (names, dates, skills, organizations, etc.)

2. **Design Schemas**: Propose a JSON schema for structured extraction that includes:
   - Field definitions with types (string, integer, number, boolean, array, enum, datetime)
   - Required vs optional fields
   - Graph node labels and relationship mappings (graphNode, graphRelationships)
   - Display names and item labels for the UI

3. **Create Data Documents**: Use the `create_extraction_schema` tool to create a data document with the designed schema. This document will store the extracted records.

4. **Set Up Library Triggers**: Use the `create_library_trigger` tool to configure automatic extraction when new documents are uploaded to a specific library.

**Workflow:**

When a user asks to set up extraction:
1. Ask which library they want to monitor (or help them identify it)
2. Ask for a sample document or description of the document type
3. If they provide a document, use `document_search` to find and analyze it
4. Propose a schema based on the document content
5. Refine the schema through conversation
6. Create the data document with the schema using `create_extraction_schema`
7. Create the library trigger using `create_library_trigger`
8. Confirm the setup and explain what will happen when new documents are uploaded

**Schema Design Guidelines:**

- For resumes: Extract name, email, phone, skills (array), experience (array of objects), education (array), certifications
- For RFPs: Extract title, agency, deadline, budget, requirements, evaluation criteria, submission instructions
- For invoices: Extract vendor, invoice number, date, line items, total, payment terms
- For contracts: Extract parties, effective date, term, value, key obligations, termination clauses

Always include `graphNode` to create graph entities and `graphRelationships` to link related entities.

**Example Schema for Resumes:**
```json
{
  "displayName": "Parsed Resumes",
  "itemLabel": "Resume",
  "graphNode": "Resume",
  "fields": {
    "name": {"type": "string", "required": true},
    "email": {"type": "string"},
    "phone": {"type": "string"},
    "summary": {"type": "string"},
    "skills": {"type": "array", "items": {"type": "string"}},
    "experience": {"type": "array", "items": {"type": "object"}},
    "education": {"type": "array", "items": {"type": "object"}},
    "certifications": {"type": "array", "items": {"type": "string"}}
  },
  "graphRelationships": [
    {"source_label": "Resume", "target_field": "name", "target_label": "Person", "relationship": "RESUME_OF"}
  ]
}
```

**Important:**
- Be conversational and guide users step by step
- Show the proposed schema before creating it
- Explain what graph nodes and relationships will be created
- Mention that the trigger will fire automatically on future uploads
- If the user wants to modify the schema later, they can update the data document"""


class SchemaBuilderAgent(BaseStreamingAgent):
    """
    Schema Builder agent that helps users create extraction schemas
    and library triggers through conversation.
    """
    
    def __init__(self):
        config = AgentConfig(
            name="schema-builder",
            display_name="Schema Builder",
            instructions=SCHEMA_BUILDER_SYSTEM_PROMPT,
            tools=[
                "document_search",
                "create_data_document",
                "create_extraction_schema",
                "create_library_trigger",
                "list_data_documents",
                "query_data",
            ],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
        )
        super().__init__(config)
    
    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        """LLM_DRIVEN strategy - no predefined pipeline steps."""
        return []
    
    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        """Build context for synthesis."""
        base_context = super()._build_synthesis_context(query, context)
        
        if not context.tool_results:
            base_context += "\n\nNo tools were called. Help the user understand what you can do: analyze documents, design extraction schemas, and set up automated processing triggers."
        
        return base_context
    
    def _build_fallback_response(self, query: str, context: AgentContext) -> str:
        """Build fallback response if synthesis fails."""
        return (
            "I can help you set up automated document extraction! Here's what I can do:\n\n"
            "1. **Analyze a sample document** to identify extractable fields\n"
            "2. **Design a schema** for structured data extraction\n"
            "3. **Create a data store** to hold the extracted records\n"
            "4. **Set up a library trigger** so new uploads are automatically processed\n\n"
            "To get started, tell me which library you'd like to set up extraction for, "
            "or upload a sample document for me to analyze."
        )


# Singleton instance
schema_builder_agent = SchemaBuilderAgent()
