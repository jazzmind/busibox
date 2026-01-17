"""
Document Search Agent.

A document Q&A agent that streams its thoughts and progress in real-time,
allowing users to see exactly what it's doing as it searches documents
and synthesizes answers.

This agent extends BaseStreamingAgent with document-specific configuration
and synthesis prompts.
"""

import logging
from typing import List, Optional

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)
from app.tools.document_search_tool import DocumentSearchOutput

logger = logging.getLogger(__name__)


# Document-specific synthesis prompt
DOCUMENT_SYNTHESIS_PROMPT = """You are an intelligent document assistant. Given a user's question and document excerpts, create a helpful, accurate answer.

Guidelines:
- Base your answer ONLY on the provided document content
- Start with a direct answer to the question
- Use **bold** for emphasis on key terms
- Use bullet points (- ) for lists
- Always cite your sources with format: (Source: filename, Page X)
- If the documents don't contain the answer, say so clearly
- Never make up information not present in the documents

IMPORTANT: Output clean, well-formatted markdown without extra blank lines."""


class DocumentAgent(BaseStreamingAgent):
    """
    A streaming document search agent that:
    1. Searches through the user's documents
    2. Retrieves relevant content with citations
    3. Synthesizes an answer based on document context
    
    All steps stream their progress to the user in real-time.
    """
    
    def __init__(self):
        config = AgentConfig(
            name="document-agent",
            display_name="Document Assistant",
            instructions=DOCUMENT_SYNTHESIS_PROMPT,
            tools=["document_search"],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.PREDEFINED_PIPELINE,
        )
        super().__init__(config)
    
    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        """
        Define the document search pipeline.
        
        For document search, we have a simple single-step pipeline:
        1. Search documents with the user's query
        """
        return [
            PipelineStep(
                tool="document_search",
                args={
                    "query": query,
                    "limit": 5,
                    "mode": "hybrid",
                }
            )
        ]
    
    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        """
        Build context for synthesis from document search results.
        
        Formats results with source citations for the synthesis agent.
        """
        search_result = context.tool_results.get("document_search")
        
        if not search_result:
            return f"User Question: {query}\n\nNo documents were found."
        
        # Handle DocumentSearchOutput
        if hasattr(search_result, 'results'):
            context_parts = [f"User Question: {query}\n\nDocument Excerpts:\n"]
            
            for i, result in enumerate(search_result.results, 1):
                source_info = result.filename if hasattr(result, 'filename') else f"Document {i}"
                if hasattr(result, 'page_number') and result.page_number:
                    source_info += f", Page {result.page_number}"
                
                score = result.score if hasattr(result, 'score') else 0.0
                text = result.text if hasattr(result, 'text') else str(result)
                
                context_parts.append(f"""
---
Source {i}: {source_info}
Relevance Score: {score:.2f}

Content:
{text}
---
""")
            
            context_parts.append("\nPlease answer the user's question based only on these document excerpts.")
            return "\n".join(context_parts)
        
        # Fallback for other result types
        return f"User Question: {query}\n\nResults:\n{search_result}"
    
    def _build_fallback_response(self, query: str, context: AgentContext) -> str:
        """
        Build fallback response if synthesis fails.
        
        Returns raw document excerpts with citations.
        """
        search_result = context.tool_results.get("document_search")
        
        if not search_result or not hasattr(search_result, 'results'):
            return f"I found some information about **{query}** but couldn't process it properly."
        
        parts = [f"Here's what I found in your documents about **{query}**:\n"]
        
        for result in search_result.results:
            source_info = result.filename if hasattr(result, 'filename') else "Unknown"
            if hasattr(result, 'page_number') and result.page_number:
                source_info += f", Page {result.page_number}"
            
            score = result.score if hasattr(result, 'score') else 0.0
            text = result.text if hasattr(result, 'text') else str(result)
            
            parts.append(f"\n### From: {source_info}")
            parts.append(f"*Relevance: {score:.2f}*\n")
            parts.append(text[:500] + ("..." if len(text) > 500 else ""))
            parts.append("")
        
        return "\n".join(parts)


# Singleton instances
document_agent = DocumentAgent()
# Alias for backward compatibility during migration
document_agent_streaming = document_agent
