"""
Core Pydantic AI agents for Busibox operations.

Provides:
- ChatAgent: General-purpose conversational agent with tool access
- RagAgent: RAG-focused agent for document Q&A with citations
- SearchAgent: Specialized agent for semantic search operations

All agents use BusiboxDeps for dependency injection of auth and HTTP clients.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent, RunContext

from app.clients.busibox import BusiboxClient
from app.schemas.auth import Principal


@dataclass
class BusiboxDeps:
    """
    Dependencies injected into agent execution context.
    
    Provides:
    - principal: Authenticated user with roles/scopes
    - busibox_client: HTTP client for Busibox services (search/ingest/RAG)
    """

    principal: Principal
    busibox_client: BusiboxClient


class ChatOutput(BaseModel):
    """Output from chat agent with conversational message."""

    message: str = Field(description="Agent's response message")
    tool_calls: List[Dict[str, Any]] = Field(
        default_factory=list, description="Tools called during execution"
    )

    @field_validator("message")
    @classmethod
    def validate_message_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Message cannot be empty")
        return v.strip()


class SearchOutput(BaseModel):
    """Output from search agent with ranked results."""

    query: str = Field(description="Original search query")
    hits: List[Dict[str, Any]] = Field(
        default_factory=list, description="Search hits returned by search service"
    )
    total_hits: int = Field(default=0, description="Total number of hits found")

    @field_validator("query")
    @classmethod
    def validate_query_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Query cannot be empty")
        return v.strip()


class RagOutput(BaseModel):
    """Output from RAG agent with answer and citations."""

    answer: str = Field(description="Generated answer from RAG")
    citations: List[Dict[str, Any]] = Field(
        default_factory=list, description="Source citations for answer"
    )
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Confidence score for answer"
    )

    @field_validator("answer")
    @classmethod
    def validate_answer_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Answer cannot be empty")
        return v.strip()


# ============================================================================
# Tool Definitions (shared across agents)
# ============================================================================


@dataclass
class SearchToolResult:
    """Result from search tool execution."""

    hits: List[Dict[str, Any]]
    total: int
    query: str


async def search_tool(ctx: RunContext[BusiboxDeps], query: str, top_k: int = 5) -> SearchToolResult:
    """
    Search Busibox documents using semantic search.
    
    Args:
        query: Search query text
        top_k: Number of results to return (default 5, max 50)
        
    Returns:
        SearchToolResult with hits and metadata
    """
    if not query or not query.strip():
        raise ValueError("Search query cannot be empty")
    if top_k < 1 or top_k > 50:
        raise ValueError("top_k must be between 1 and 50")

    result = await ctx.deps.busibox_client.search(query=query.strip(), top_k=top_k)
    return SearchToolResult(
        hits=result.get("hits", []), total=result.get("total", 0), query=query.strip()
    )


async def ingest_tool(
    ctx: RunContext[BusiboxDeps], path: str, metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Ingest a document into Busibox for processing and indexing.
    
    Args:
        path: Document path or identifier
        metadata: Optional metadata to attach to document
        
    Returns:
        Ingestion result with document ID and status
    """
    if not path or not path.strip():
        raise ValueError("Document path cannot be empty")

    return await ctx.deps.busibox_client.ingest_document(path=path.strip(), metadata=metadata or {})


async def rag_tool(
    ctx: RunContext[BusiboxDeps], database: str, query: str, top_k: int = 5
) -> Dict[str, Any]:
    """
    Query RAG database for relevant content with semantic search.
    
    Args:
        database: RAG database name
        query: Query text
        top_k: Number of results to return (default 5, max 50)
        
    Returns:
        RAG query result with relevant chunks and metadata
    """
    if not database or not database.strip():
        raise ValueError("Database name cannot be empty")
    if not query or not query.strip():
        raise ValueError("Query cannot be empty")
    if top_k < 1 or top_k > 50:
        raise ValueError("top_k must be between 1 and 50")

    return await ctx.deps.busibox_client.rag_query(
        database=database.strip(), query=query.strip(), top_k=top_k
    )


# ============================================================================
# Agent Definitions
# ============================================================================


chat_agent = Agent[BusiboxDeps, ChatOutput](
    model=None,  # Model set at runtime via config
    instructions="""You are a Busibox assistant with access to document search, ingestion, and RAG tools.

Your role:
- Help users find, process, and analyze documents
- Use search_tool to find relevant documents
- Use ingest_tool to add new documents to the system
- Use rag_tool to answer questions using document context
- Keep responses concise, actionable, and well-structured
- Always cite sources when using RAG or search results
- If a tool call fails, explain the error clearly to the user

Guidelines:
- Prefer search_tool for broad document discovery
- Use rag_tool for specific questions requiring document context
- Always validate tool inputs before calling
- Return structured responses with clear formatting
""",
    tools=[search_tool, ingest_tool, rag_tool],
)


@chat_agent.instructions
async def add_role_context(ctx: RunContext[BusiboxDeps]) -> str:
    """Inject user role context into agent instructions."""
    roles = ", ".join(ctx.deps.principal.roles) if ctx.deps.principal.roles else "user"
    scopes = ", ".join(ctx.deps.principal.scopes) if ctx.deps.principal.scopes else "none"
    return f"\nUser context:\n- Roles: {roles}\n- Scopes: {scopes}\n- Subject: {ctx.deps.principal.sub}"


rag_agent = Agent[BusiboxDeps, RagOutput](
    model=None,  # Model set at runtime via config
    instructions="""You are a Busibox RAG assistant specialized in answering questions using document context.

Your role:
- Answer questions using relevant document chunks from RAG database
- Always provide citations for your answers
- Use search_tool to find relevant documents first if needed
- Use rag_tool to retrieve specific context for answering
- Be precise and cite specific sources
- If information is not in the documents, say so clearly

Guidelines:
- Always include citations in your response
- Prefer direct quotes when appropriate
- Indicate confidence level in your answers
- If multiple sources conflict, note the discrepancy
- Keep answers focused and relevant to the query
""",
    tools=[search_tool, rag_tool],
)


search_agent = Agent[BusiboxDeps, SearchOutput](
    model=None,  # Model set at runtime via config
    instructions="""You are a Busibox search assistant specialized in semantic document search.

Your role:
- Execute semantic searches across Busibox documents
- Return ranked results with relevance scores
- Help users refine queries for better results
- Explain search results and relevance

Guidelines:
- Use search_tool for all search operations
- Return results with clear relevance indicators
- Suggest query refinements if results are poor
- Explain why results are relevant to the query
""",
    tools=[search_tool],
)
