"""
Schema Builder Agent.

Dual-mode agent:
1) Chat mode: helps users discuss and refine extraction schemas.
2) Workflow mode: programmatic, deterministic schema generation from document text.
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


SCHEMA_BUILDER_SYSTEM_PROMPT = """You are a Schema Builder assistant that designs structured extraction schemas for document processing workflows.

**Your Purpose:**
Help users define practical, generalizable extraction schemas based on document type.

**Key Capabilities:**

1. **Analyze Documents**: When a user uploads or references a sample document, analyze its content and identify extractable fields (names, dates, skills, organizations, etc.)

2. **Design Schemas**: Propose a JSON schema for structured extraction that includes:
   - Field definitions with types (string, integer, number, boolean, array, enum, datetime)
   - Required vs optional fields
   - Graph node labels and relationship mappings (graphNode, graphRelationships)
   - Display names and item labels for the UI

3. **Workflow-Friendly Output**: For programmatic calls, return clean JSON schema content that callers can persist and apply.

**Workflow:**

When a user asks to set up extraction (chat mode):
1. Ask which library they want to monitor (or help them identify it)
2. Ask for a sample document or description of the document type
3. If they provide a document, use `document_search` to find and analyze it
4. Propose a schema based on the document content
5. Refine the schema through conversation
6. Explain how the caller can persist/apply the schema

**Schema Design Guidelines:**

- For resumes: Extract name, email, phone, skills (array), experience (array of objects), education (array), certifications
- For RFPs: Extract title, agency, deadline, budget, requirements, evaluation criteria, submission instructions
- For invoices: Extract vendor, invoice number, date, line items, total, payment terms
- For contracts: Extract parties, effective date, term, value, key obligations, termination clauses

For schema generation from raw document content:
- Identify the DOCUMENT TYPE (resume, invoice, contract, proposal, report, etc.)
- `schemaName` must describe the document type, not a specific person/file
- Use camelCase field names
- **CRITICAL: Be extremely conservative with required fields.** Mark a field as `required: true` ONLY if it is absolutely guaranteed to appear in EVERY document of this type AND the schema would be meaningless without it. Typically only 1-2 fields should be required (e.g. a person's name on a resume, a project title on a proposal). Most fields should NOT be required — set `required: false` or omit the `required` property entirely. When in doubt, do NOT mark a field as required.
- Prefer practical fields that generalize across similar documents
- Keep the schema concise and usable (typically 8-15 fields)

**Field Search/Indexing (optional per field):**

Each field can include an optional `search` array controlling how extracted values are indexed:
- `"keyword"` — BM25 keyword index for exact match and filtering
- `"embed"` — semantic vector embedding for similarity search
- `"graph"` — entity extraction into the knowledge graph (Neo4j)

Fields without a `search` array are stored but not indexed. Examples:
- A person's name: `["keyword", "graph"]`
- A description or summary: `["embed"]`
- A list of skills: `["keyword", "embed", "graph"]`
- A phone number: no `search` (just stored)

**Example Schema for Resumes (note: only 1 field is required):**
```json
{
  "displayName": "Parsed Resumes",
  "itemLabel": "Resume",
  "graphNode": "Resume",
  "fields": {
    "name": {"type": "string", "required": true, "description": "Full name of the candidate", "search": ["keyword", "graph"]},
    "email": {"type": "string", "description": "Contact email address", "search": ["keyword"]},
    "phone": {"type": "string", "description": "Contact phone number"},
    "summary": {"type": "string", "description": "Professional summary or objective", "search": ["embed"]},
    "skills": {"type": "array", "items": {"type": "string"}, "description": "Technical and professional skills", "search": ["keyword", "embed", "graph"]},
    "experience": {"type": "array", "items": {"type": "object"}, "description": "Work experience entries", "search": ["embed"]},
    "education": {"type": "array", "items": {"type": "object"}, "description": "Education history"},
    "certifications": {"type": "array", "items": {"type": "string"}, "description": "Professional certifications", "search": ["keyword"]}
  },
  "graphRelationships": [
    {"source_label": "Resume", "target_field": "name", "target_label": "Person", "relationship": "RESUME_OF"}
  ]
}
```

**Important:**
- Be conversational and guide users step by step
- Show the proposed schema clearly
- Do not execute side-effecting creation actions unless explicitly requested
- Keep workflow/programmatic outputs deterministic and valid JSON when required"""


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
                "list_data_documents",
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
