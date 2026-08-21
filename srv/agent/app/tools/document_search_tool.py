"""Document search tool for RAG agents."""
import structlog
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field
from pydantic_ai import Tool, RunContext

from app.agents.core import BusiboxDeps

logger = structlog.get_logger()

HIGH_RELEVANCY_THRESHOLD = 0.85
ADAPTIVE_MULTIPLIER = 2
# Results that score below this fraction of the top hit are dropped even if they
# cleared the absolute min_score floor.  Keeps multi-source answers (secondary
# hits at ≥0.7x the best score) while excluding off-topic docs.
RELATIVE_SCORE_RATIO = 0.7


class DocumentSearchInput(BaseModel):
    """Input schema for document search tool."""
    query: str = Field(description="Search query to find relevant documents")
    limit: int = Field(default=10, description="Maximum number of results (default 10, max 50)")
    min_score: float = Field(default=0.5, description="Minimum relevancy score to include (0-1, default 0.5)")
    mode: str = Field(default="hybrid", description="Search mode: hybrid, semantic, or keyword")
    file_ids: Optional[List[str]] = Field(default=None, description="Optional list of file IDs to filter")
    expand_graph: bool = Field(default=False, description="Expand graph relationships (adds latency, default false)")


class SearchResultItem(BaseModel):
    """Individual search result."""
    file_id: str = Field(description="Unique file identifier for citations")
    filename: str = Field(description="Name of the source document")
    text: str = Field(description="Relevant text excerpt from the document")
    score: float = Field(description="Relevance score")
    page_number: Optional[int] = Field(default=None, description="Page number if available")
    chunk_index: int = Field(default=0, description="Chunk index within the document")


class DocumentSearchOutput(BaseModel):
    """Output schema for document search tool."""
    found: bool = Field(description="Whether relevant documents were found")
    result_count: int = Field(description="Number of results returned")
    context: str = Field(description="Formatted context from search results for LLM")
    results: List[SearchResultItem] = Field(description="List of search results with metadata")
    graph_context: Optional[str] = Field(default=None, description="Graph entity context if available")
    error: Optional[str] = Field(default=None, description="Error message if search failed")


async def _resolve_knowledge_scope(
    ctx: RunContext[BusiboxDeps],
    requested_file_ids: Optional[List[str]],
) -> Tuple[Optional[List[str]], str]:
    """Resolve the server-provided chat knowledge scope to an RLS-safe filter."""
    metadata = ctx.deps.metadata or {}
    scope = metadata.get("knowledge_scope")

    if scope == "attachments":
        attachment_file_ids = metadata.get("attachment_file_ids") or []
        return [str(file_id) for file_id in attachment_file_ids if file_id], "attachments"

    if scope == "libraries":
        resolved_file_ids = metadata.get("selected_library_file_ids")
        if isinstance(resolved_file_ids, list):
            return [str(file_id) for file_id in resolved_file_ids if file_id], "libraries"
        library_ids = metadata.get("selected_library_ids") or []
        file_ids: List[str] = []
        for library_id in library_ids:
            documents = await ctx.deps.busibox_client.library_documents(str(library_id))
            for document in documents:
                file_id = document.get("fileId") or document.get("file_id") or document.get("id")
                if file_id:
                    file_ids.append(str(file_id))
        return list(dict.fromkeys(file_ids)), "libraries"

    if scope == "all":
        return None, "all_accessible"

    if requested_file_ids:
        return requested_file_ids, "tool_arg"
    context_file_ids = metadata.get("file_ids")
    if isinstance(context_file_ids, list) and context_file_ids:
        return [str(file_id) for file_id in context_file_ids], "metadata_context"
    return None, "all_accessible"


async def search_documents(
    ctx: RunContext[BusiboxDeps],
    query: str,
    limit: int = 10,
    min_score: float = 0.5,
    mode: str = "hybrid",
    file_ids: Optional[List[str]] = None,
    expand_graph: bool = False,
) -> DocumentSearchOutput:
    """
    Search through user documents to find relevant information.
    
    This tool performs semantic, keyword, or hybrid search across the user's
    document library. Results are automatically filtered based on user permissions
    and a minimum relevancy score threshold.
    
    When called from a document-scoped chat, file_ids are automatically injected
    from the application context so the search is restricted to the relevant documents.
    
    Args:
        ctx: RunContext with authenticated BusiboxClient
        query: Search query string
        limit: Maximum number of results (default: 10, max: 50)
        min_score: Minimum relevancy score to include (default: 0.35)
        mode: Search mode - "hybrid" (recommended), "semantic", or "keyword"
        file_ids: Optional list of file IDs to restrict search
        expand_graph: Whether to expand graph relationships (default: False)
    """
    try:
        effective_file_ids, file_ids_source = await _resolve_knowledge_scope(ctx, file_ids)

        if file_ids_source in {"attachments", "libraries"} and not effective_file_ids:
            scope_label = "attachments" if file_ids_source == "attachments" else "selected library"
            return DocumentSearchOutput(
                found=False,
                result_count=0,
                context=f"No searchable documents are available in the {scope_label} scope.",
                results=[],
            )

        # Diagnostics: log effective scoping so we can confirm shared-library
        # docs are in scope vs. only personal docs.
        logger.info(
            "document_search: starting search",
            extra={
                "query": query[:200],
                "mode": mode,
                "effective_file_ids": effective_file_ids,
                "file_ids_source": file_ids_source,
                "limit": min(limit, 50),
            },
        )

        capped_limit = min(limit, 50)
        response = await ctx.deps.busibox_client.search(
            query=query,
            top_k=capped_limit,
            mode=mode,
            file_ids=effective_file_ids,
            rerank=True,
            expand_graph=expand_graph,
        )
        
        results = response.get("results", [])
        
        if not results or len(results) == 0:
            logger.info(
                "document_search: search API returned 0 results",
                extra={"query": query, "mode": mode},
            )
            return DocumentSearchOutput(
                found=False,
                result_count=0,
                context="No relevant documents found for your query.",
                results=[],
            )

        score_summary = [round(r.get("score", 0.0), 4) for r in results[:5]]
        top_result_details = [
            {
                "file_id": r.get("file_id", ""),
                "filename": r.get("filename", ""),
                "score": round(r.get("score", 0.0), 4),
                "page": r.get("page_number"),
            }
            for r in results[:10]
        ]
        logger.info(
            "document_search: raw results from search API",
            extra={
                "total_results": len(results),
                "min_score_filter": min_score,
                "top_5_scores": score_summary,
                "top_10_results": top_result_details,
            },
        )

        # Filter by minimum relevancy score
        relevant_results = [r for r in results if r.get("score", 0.0) >= min_score]

        # Adaptive fetching: if all results score above the high-relevancy threshold
        # and we got exactly `limit` back, there may be more good results -- fetch again.
        if (
            relevant_results
            and len(relevant_results) == len(results)
            and all(r.get("score", 0.0) >= HIGH_RELEVANCY_THRESHOLD for r in relevant_results)
            and len(results) >= capped_limit
            and capped_limit * ADAPTIVE_MULTIPLIER <= 50
        ):
            logger.info(
                "document_search: all results highly relevant, fetching additional batch",
                extra={"original_limit": capped_limit, "new_limit": capped_limit * ADAPTIVE_MULTIPLIER},
            )
            expanded_response = await ctx.deps.busibox_client.search(
                query=query,
                top_k=capped_limit * ADAPTIVE_MULTIPLIER,
                mode=mode,
                file_ids=effective_file_ids,
                rerank=True,
                expand_graph=expand_graph,
            )
            expanded_results = expanded_response.get("results", [])
            if expanded_results:
                relevant_results = [r for r in expanded_results if r.get("score", 0.0) >= min_score]
                response = expanded_response

        if not relevant_results:
            logger.warning(
                "document_search: all results filtered by min_score",
                extra={
                    "total_raw": len(results),
                    "min_score": min_score,
                    "lowest_score": min(r.get("score", 0.0) for r in results) if results else None,
                    "highest_score": max(r.get("score", 0.0) for r in results) if results else None,
                },
            )
            return DocumentSearchOutput(
                found=False,
                result_count=0,
                context="Search returned results but none met the relevancy threshold.",
                results=[],
            )
        
        # Relative gate: drop any result that is far below the best hit.
        # This removes off-topic documents that cleared the absolute floor but are
        # clearly weaker than the top match (e.g. dredging docs for a per-diem query).
        if relevant_results:
            relevant_results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
            top_score = relevant_results[0].get("score", 0.0)
            relative_cutoff = max(min_score, top_score * RELATIVE_SCORE_RATIO)
            before_gate = len(relevant_results)
            relevant_results = [r for r in relevant_results if r.get("score", 0.0) >= relative_cutoff]
            if len(relevant_results) != before_gate:
                logger.info(
                    "document_search: relative gate applied",
                    extra={
                        "top_score": round(top_score, 4),
                        "cutoff": round(relative_cutoff, 4),
                        "kept": len(relevant_results),
                        "dropped": before_gate - len(relevant_results),
                    },
                )

        formatted_results = []
        context_parts = []
        
        for idx, result in enumerate(relevant_results, 1):
            fid = result.get("file_id", "unknown")
            fname = result.get("filename") or f"Document {fid[:8]}"
            page_num = result.get("page_number") if result.get("page_number", 0) > 0 else None
            score = result.get("score", 0.0)

            result_item = SearchResultItem(
                file_id=fid,
                filename=fname,
                text=result.get("text", ""),
                score=score,
                page_number=page_num,
                chunk_index=result.get("chunk_index", 0),
            )
            formatted_results.append(result_item)
            
            source_parts = [fname]
            if page_num:
                source_parts.append(f"p.{page_num}")
            source_ref = ", ".join(source_parts)
            # Citation URL includes page number so the UI can scroll directly to it.
            citation_url = f"doc:{fid}:{page_num}" if page_num else f"doc:{fid}"

            context_parts.append(
                f"--- Source {idx} [{source_ref}] (score:{score:.2f}, file_id:{fid}, citation_url:{citation_url}) ---\n{result.get('text', '')}"
            )
        
        citation_rule = (
            "SOURCE DISPLAY RULE: The chat UI will render one structured Sources section after "
            "the answer using these results. Do NOT add inline document links, citation markers, "
            "or another Sources section to the prose.\n\n"
        )
        relevancy_rule = (
            "RELEVANCY CHECK: Before using ANY result, verify it is actually relevant to the "
            "user's query. If the documents below are about a completely different topic, do NOT "
            "use them — say you didn't find relevant documents instead.\n\n"
        )
        full_context = citation_rule + relevancy_rule
        full_context += "\n\n".join(context_parts)
        full_context += "\n\nREMINDER: Keep document source labels out of the prose; the UI adds them."

        graph_context_str: Optional[str] = None
        graph_data = response.get("graph")
        if graph_data and graph_data.get("graph_context"):
            graph_context_str = graph_data["graph_context"]
            full_context += f"\n\n--- Graph Context ---\n{graph_context_str}"
        
        return DocumentSearchOutput(
            found=True,
            result_count=len(formatted_results),
            context=full_context,
            results=formatted_results,
            graph_context=graph_context_str,
        )
    
    except Exception as e:
        error_msg = f"Search failed: {str(e)}"
        return DocumentSearchOutput(
            found=False,
            result_count=0,
            context="",
            results=[],
            error=error_msg,
        )


document_search_tool = Tool(
    search_documents,
    takes_ctx=True,
    name="document_search",
    description="""Search through the user's uploaded documents to find relevant information.
Use this tool when:
- The user asks a question that might be answered by their documents
- You need to find specific information from uploaded files
- You want to provide context-aware answers based on the user's data

The tool performs hybrid search (combining semantic and keyword matching) for best results.
Results are automatically filtered based on user permissions (RLS).

The UI renders structured source chips after the answer. Do not emit inline document links,
numbered document citation markers, or a second Sources section in the prose.
""",
)





