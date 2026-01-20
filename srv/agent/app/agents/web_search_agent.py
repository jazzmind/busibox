"""
Web Search Agent.

A web research agent that streams its thoughts and progress in real-time,
allowing users to see exactly what it's doing as it searches, scrapes,
and synthesizes information.

This agent extends BaseStreamingAgent with web search-specific configuration
and a dynamic pipeline that scrapes URLs based on search results.

Query optimization is handled by the web_search tool itself, which:
- Passes natural language queries directly to AI-powered providers (Tavily, Perplexity)
- Optimizes queries into keywords for traditional providers (DuckDuckGo, Brave)
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)

logger = logging.getLogger(__name__)

# Web search synthesis prompt
WEB_SEARCH_SYNTHESIS_PROMPT = """You are a research synthesis specialist. Given a user's question and scraped web content, create a comprehensive, well-organized answer.

Guidelines:
- Start with a brief, direct answer to the question
- Use **bold** for emphasis on key terms
- Use bullet points (- ) for lists, not numbered lists
- Keep paragraphs short (2-3 sentences max)
- Cite sources inline as linked text like [source name](url)
- Group related information under ## headings
- End with a "Sources" section listing URLs as bullet points

IMPORTANT: Output clean, compact markdown without extra blank lines or spaces. Do not use multiple consecutive newlines."""


class WebSearchAgent(BaseStreamingAgent):
    """
    A streaming web search agent that:
    1. Searches the web for relevant pages (query optimization is handled by the tool)
    2. Scrapes top results for full content (dynamic pipeline)
    3. Synthesizes findings into a comprehensive answer
    
    All steps stream their progress to the user in real-time.
    
    Note: The web_search tool automatically optimizes queries for each provider:
    - AI providers (Tavily, Perplexity) receive the original natural language query
    - Keyword providers (DuckDuckGo, Brave) receive optimized keyword queries
    """
    
    def __init__(self):
        config = AgentConfig(
            name="web-search-agent",
            display_name="Web Search Agent",
            instructions=WEB_SEARCH_SYNTHESIS_PROMPT,
            tools=["web_search", "web_scraper"],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.SEQUENTIAL,  # Sequential to allow dynamic pipeline
        )
        super().__init__(config)
        
        # Store scraped content for synthesis
        self._scraped_content: List[Dict[str, Any]] = []
    
    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        """
        Define the initial web search pipeline.
        
        The query is passed directly to web_search, which handles query
        optimization internally based on which providers are enabled.
        """
        # Reset scraped content for new query
        self._scraped_content = []
        
        return [
            PipelineStep(
                tool="web_search",
                args={
                    "query": query,
                    "max_results": 5,
                }
            )
        ]
    
    async def process_tool_result(
        self, 
        step: PipelineStep, 
        result: Any, 
        context: AgentContext
    ) -> List[PipelineStep]:
        """
        Process tool results and add dynamic scrape steps.
        
        After web_search completes, adds scrape steps for top 3 results.
        After web_scraper completes, stores the scraped content.
        """
        if step.tool == "web_search":
            # Add scrape steps for top search results
            additional_steps = []
            
            if hasattr(result, 'results') and result.results:
                # Scrape top 3 results
                for search_result in result.results[:3]:
                    url = search_result.url if hasattr(search_result, 'url') else str(search_result)
                    title = search_result.title if hasattr(search_result, 'title') else url
                    
                    additional_steps.append(PipelineStep(
                        tool="web_scraper",
                        args={
                            "url": url,
                            "max_content_length": 8000,
                        }
                    ))
                    
                    # Store search result metadata for later
                    domain = urlparse(url).netloc
                    self._scraped_content.append({
                        "url": url,
                        "title": title,
                        "domain": domain,
                        "content": search_result.snippet if hasattr(search_result, 'snippet') else "",
                        "snippet_only": True,  # Will be updated if scrape succeeds
                    })
            
            return additional_steps
        
        elif step.tool == "web_scraper":
            # Update scraped content with full content
            url = step.args.get("url", "")
            
            for entry in self._scraped_content:
                if entry["url"] == url:
                    if hasattr(result, 'success') and result.success:
                        entry["content"] = result.content if hasattr(result, 'content') else ""
                        entry["title"] = result.title if hasattr(result, 'title') else entry["title"]
                        entry["snippet_only"] = False
                    elif hasattr(result, 'error'):
                        logger.warning(f"Scrape failed for {url}: {result.error}")
                    break
        
        return []
    
    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        """
        Build context for synthesis from scraped web content.
        """
        if not self._scraped_content:
            return f"User Question: {query}\n\nNo web content was found."
        
        context_parts = [f"User Question: {query}\n\nResearch Results:\n"]
        
        for i, source in enumerate(self._scraped_content, 1):
            is_snippet = source.get("snippet_only", False)
            content_type = "snippet" if is_snippet else "full article"
            
            content = source.get("content", "")[:4000]
            
            context_parts.append(f"""
---
Source {i}: {source['title']}
URL: {source['url']}
Type: {content_type}

Content:
{content}
---
""")
        
        context_parts.append("\nPlease synthesize a comprehensive answer based on these sources.")
        return "\n".join(context_parts)
    
    def _build_fallback_response(self, query: str, context: AgentContext) -> str:
        """
        Build fallback response if synthesis fails.
        """
        parts = [f"Here's what I found about **{query}**:\n"]
        
        for source in self._scraped_content:
            parts.append(f"\n### {source['title']}")
            parts.append(f"*Source: {source['domain']}*\n")
            content = source.get('content', '')
            parts.append(content[:500] + "..." if len(content) > 500 else content)
            parts.append(f"\n[Read more]({source['url']})\n")
        
        return "\n".join(parts)
    
    def _format_tool_result_message(self, tool_name: str, result: Any) -> str:
        """Format human-readable message for tool results."""
        if tool_name == "web_search":
            if hasattr(result, 'result_count'):
                providers = ""
                if hasattr(result, 'providers_used') and result.providers_used:
                    providers = f" (via {', '.join(result.providers_used)})"
                return f"Found **{result.result_count} results**{providers}"
            return "Search completed"
        
        elif tool_name == "web_scraper":
            if hasattr(result, 'success') and result.success:
                word_count = result.word_count if hasattr(result, 'word_count') else 0
                url = result.url if hasattr(result, 'url') else "page"
                domain = urlparse(url).netloc if url.startswith("http") else url
                return f"Extracted **{word_count} words** from {domain}"
            elif hasattr(result, 'error'):
                return f"Could not read page: {result.error}"
            return "Scrape completed"
        
        return super()._format_tool_result_message(tool_name, result)
    
    def _clean_output(self, output: str) -> str:
        """Clean up the synthesis output."""
        # Remove any AgentRunResult wrapper
        if "AgentRunResult(output=" in output:
            match = re.search(r"AgentRunResult\(output=['\"](.+)['\"]\)", output, re.DOTALL)
            if match:
                output = match.group(1).replace("\\n", "\n")
        return output.strip()


# Singleton instances
web_search_agent = WebSearchAgent()
# Alias for backward compatibility during migration
web_search_agent_streaming = web_search_agent
