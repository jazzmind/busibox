"""Document search tool for RAG agents."""
from typing import List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Tool

from app.clients.search_client import SearchClient


class DocumentSearchInput(BaseModel):
    """Input schema for document search tool."""
    query: str = Field(description="Search query to find relevant documents")
    limit: int = Field(default=5, description="Maximum number of results (default 5, max 50)")
    mode: str = Field(default="hybrid", description="Search mode: hybrid, semantic, or keyword")
    file_ids: Optional[List[str]] = Field(default=None, description="Optional list of file IDs to filter")


class SearchResultItem(BaseModel):
    """Individual search result."""
    filename: str = Field(description="Name of the source document")
    text: str = Field(description="Relevant text excerpt from the document")
    score: float = Field(description="Relevance score")
    page_number: Optional[int] = Field(default=None, description="Page number if available")


class DocumentSearchOutput(BaseModel):
    """Output schema for document search tool."""
    found: bool = Field(description="Whether relevant documents were found")
    result_count: int = Field(description="Number of results returned")
    context: str = Field(description="Formatted context from search results for LLM")
    results: List[SearchResultItem] = Field(description="List of search results with metadata")
    error: Optional[str] = Field(default=None, description="Error message if search failed")


async def search_documents(
    query: str,
    limit: int = 5,
    mode: str = "hybrid",
    file_ids: Optional[List[str]] = None,
) -> DocumentSearchOutput:
    """
    Search through user documents to find relevant information.
    
    This tool performs semantic, keyword, or hybrid search across the user's
    document library. Results are automatically filtered based on user permissions.
    
    Args:
        query: Search query string
        limit: Maximum number of results (default: 5, max: 50)
        mode: Search mode - "hybrid" (recommended), "semantic", or "keyword"
        file_ids: Optional list of file IDs to restrict search
        
    Returns:
        DocumentSearchOutput with formatted context and result metadata
        
    Raises:
        Exception: If search API is unavailable or returns an error
    """
    try:
        # Create search client (auth token should be set via context/environment)
        async with SearchClient() as client:
            # Perform search with reranking enabled
            response = await client.search(
                query=query,
                limit=min(limit, 50),
                mode=mode,
                file_ids=file_ids,
                rerank=True,  # Enable reranking for better results
            )
        
        # Check if we got results
        if not response.results or len(response.results) == 0:
            return DocumentSearchOutput(
                found=False,
                result_count=0,
                context="No relevant documents found for your query.",
                results=[],
            )
        
        # Format results for LLM consumption
        formatted_results = []
        context_parts = []
        
        for idx, result in enumerate(response.results, 1):
            # Create result item
            result_item = SearchResultItem(
                filename=result.filename or f"Document {result.file_id[:8]}",
                text=result.text,
                score=result.score,
                page_number=result.page_number if result.page_number > 0 else None,
            )
            formatted_results.append(result_item)
            
            # Build context string
            source_info = result_item.filename
            if result_item.page_number:
                source_info += f", Page {result_item.page_number}"
            
            context_parts.append(
                f"--- Document {idx} [Source: {source_info}] ---\n{result.text}"
            )
        
        # Combine context
        full_context = "\n\n".join(context_parts)
        
        return DocumentSearchOutput(
            found=True,
            result_count=len(formatted_results),
            context=full_context,
            results=formatted_results,
        )
    
    except Exception as e:
        # Return error information
        error_msg = f"Search failed: {str(e)}"
        return DocumentSearchOutput(
            found=False,
            result_count=0,
            context="",
            results=[],
            error=error_msg,
        )


# Create the Pydantic AI tool
document_search_tool = Tool(
    search_documents,
    takes_ctx=False,
    name="document_search",
    description="""Search through the user's uploaded documents to find relevant information.
Use this tool when:
- The user asks a question that might be answered by their documents
- You need to find specific information from uploaded files
- You want to provide context-aware answers based on the user's data

The tool performs hybrid search (combining semantic and keyword matching) for best results.
Results are automatically filtered based on user permissions.""",
)








