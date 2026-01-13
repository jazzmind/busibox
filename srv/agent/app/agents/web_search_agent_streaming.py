"""
Streaming Web Search Agent.

A web research agent that streams its thoughts and progress in real-time,
allowing users to see exactly what it's doing as it searches, scrapes,
and synthesizes information.
"""

import asyncio
import os
from typing import List, Optional
from urllib.parse import urlparse

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from app.agents.streaming_agent import StreamingAgent, StreamCallback
from app.config.settings import get_settings
from app.schemas.streaming import StreamEvent, thought, tool_start, tool_result, content, error
from app.tools.web_search_tool import search_web, WebSearchResult
from app.tools.web_scraper_tool import scrape_webpage

settings = get_settings()

# Configure OpenAI client to use LiteLLM
os.environ["OPENAI_BASE_URL"] = str(settings.litellm_base_url)
litellm_api_key = settings.litellm_api_key or "sk-1234"
os.environ["OPENAI_API_KEY"] = litellm_api_key


class WebSearchAgentStreaming(StreamingAgent):
    """
    A streaming web search agent that:
    1. Searches the web for relevant pages
    2. Scrapes top results for full content
    3. Synthesizes findings into a comprehensive answer
    
    All steps stream their progress to the user in real-time.
    """
    
    name = "Web Search Agent"
    
    def __init__(self):
        # Create synthesis model for generating summaries
        self.synthesis_model = OpenAIModel(
            model_name=settings.default_model,
            provider="openai",
        )
        
        self.synthesis_agent = Agent(
            model=self.synthesis_model,
            system_prompt="""You are a research synthesis specialist. Given a user's question and scraped web content, create a comprehensive, well-organized answer.

Guidelines:
- Start with a brief, direct answer to the question
- Use **bold** for emphasis on key terms
- Use bullet points (- ) for lists, not numbered lists
- Keep paragraphs short (2-3 sentences max)
- Cite sources inline as linked text like [source name](url)
- Group related information under ## headings
- End with a "Sources" section listing URLs as bullet points

IMPORTANT: Output clean, compact markdown without extra blank lines or spaces. Do not use multiple consecutive newlines.""",
        )
    
    async def run_with_streaming(
        self,
        query: str,
        stream: StreamCallback,
        cancel: asyncio.Event,
        context: Optional[dict] = None,
    ) -> str:
        """
        Execute web search with real-time streaming of progress.
        
        Steps:
        1. Search the web
        2. Scrape top results
        3. Synthesize findings
        """
        
        # Step 1: Search the web
        await stream(thought(
            source=self.name,
            message=f"Searching the web for: **{query}**"
        ))
        
        if cancel.is_set():
            return ""
        
        # Get provider config for context (user/agent/system hierarchy)
        from app.tools.web_search_tool import get_provider_config_for_context
        user_id = context.get("user_id") if context else None
        agent_id = context.get("agent_id") if context else None
        provider_config = await get_provider_config_for_context(user_id=user_id, agent_id=agent_id)
        
        search_results = await search_web(query, max_results=5, providers=provider_config)
        
        if not search_results.found or not search_results.results:
            await stream(error(
                source=self.name,
                message=f"No search results found for '{query}'. {search_results.error or ''}"
            ))
            return f"I couldn't find any web results for '{query}'. Please try rephrasing your question."
        
        # Report search results with provider info
        result_count = len(search_results.results)
        providers_info = ""
        if search_results.providers_used:
            providers_info = f" (via {', '.join(search_results.providers_used)})"
        
        await stream(thought(
            source=self.name,
            message=f"Found **{result_count} results**{providers_info}. Analyzing the most relevant sources...",
            data={
                "results": [r.model_dump() for r in search_results.results],
                "providers_used": search_results.providers_used
            }
        ))
        
        if cancel.is_set():
            return ""
        
        # Step 2: Scrape top results
        scraped_content: List[dict] = []
        results_to_scrape = search_results.results[:3]  # Top 3
        
        for i, result in enumerate(results_to_scrape):
            if cancel.is_set():
                break
            
            domain = urlparse(result.url).netloc
            
            await stream(tool_start(
                source="web_scraper",
                message=f"Reading: **{result.title}** ({domain})",
                data={"url": result.url, "title": result.title}
            ))
            
            # Scrape the page
            scrape_result = await scrape_webpage(result.url, max_content_length=8000)
            
            if scrape_result.success:
                await stream(tool_result(
                    source="web_scraper",
                    message=f"Extracted **{scrape_result.word_count} words** from {domain}",
                    data={
                        "url": result.url,
                        "title": scrape_result.title or result.title,
                        "word_count": scrape_result.word_count,
                        "success": True
                    }
                ))
                
                scraped_content.append({
                    "url": result.url,
                    "title": scrape_result.title or result.title,
                    "content": scrape_result.content,
                    "domain": domain,
                })
            else:
                await stream(tool_result(
                    source="web_scraper",
                    message=f"Could not read {domain}: {scrape_result.error}. Using snippet instead.",
                    data={
                        "url": result.url,
                        "error": scrape_result.error,
                        "success": False
                    }
                ))
                
                # Fall back to snippet
                scraped_content.append({
                    "url": result.url,
                    "title": result.title,
                    "content": result.snippet,
                    "domain": domain,
                    "snippet_only": True,
                })
        
        if cancel.is_set():
            return ""
        
        if not scraped_content:
            await stream(error(
                source=self.name,
                message="Could not retrieve content from any sources."
            ))
            return "I found search results but couldn't retrieve the content. Please try again."
        
        # Step 3: Synthesize findings
        await stream(thought(
            source=self.name,
            message=f"Synthesizing information from **{len(scraped_content)} sources**..."
        ))
        
        # Build context for synthesis
        synthesis_context = self._build_synthesis_context(query, scraped_content)
        
        try:
            # Run synthesis with streaming
            full_output = ""
            
            async with self.synthesis_agent.run_stream(synthesis_context) as result:
                async for chunk in result.stream_text(delta=True):
                    if cancel.is_set():
                        break
                    
                    # Stream each chunk as content
                    full_output += chunk
                    await stream(content(
                        source=self.name,
                        message=chunk,
                        data={"streaming": True, "partial": True}
                    ))
            
            # Clean up output
            final_output = self._clean_output(full_output)
            
            # Send final content marker with sources
            await stream(content(
                source=self.name,
                message="",  # Empty - content already streamed
                data={
                    "streaming": False, 
                    "partial": False,
                    "complete": True,
                    "sources": [s["url"] for s in scraped_content]
                }
            ))
            
            return final_output
            
        except Exception as e:
            await stream(error(
                source=self.name,
                message=f"Error synthesizing results: {str(e)}"
            ))
            
            # Fallback: return raw snippets
            fallback = self._build_fallback_response(query, scraped_content)
            await stream(content(source=self.name, message=fallback))
            return fallback
    
    def _build_synthesis_context(self, query: str, scraped_content: List[dict]) -> str:
        """Build the context string for the synthesis agent."""
        context_parts = [f"User Question: {query}\n\nResearch Results:\n"]
        
        for i, source in enumerate(scraped_content, 1):
            is_snippet = source.get("snippet_only", False)
            content_type = "snippet" if is_snippet else "full article"
            
            context_parts.append(f"""
---
Source {i}: {source['title']}
URL: {source['url']}
Type: {content_type}

Content:
{source['content'][:4000]}
---
""")
        
        context_parts.append("\nPlease synthesize a comprehensive answer based on these sources.")
        return "\n".join(context_parts)
    
    def _clean_output(self, output: str) -> str:
        """Clean up the synthesis output."""
        # Remove any AgentRunResult wrapper
        if "AgentRunResult(output=" in output:
            import re
            match = re.search(r"AgentRunResult\(output=['\"](.+)['\"]\)", output, re.DOTALL)
            if match:
                output = match.group(1).replace("\\n", "\n")
        return output.strip()
    
    def _build_fallback_response(self, query: str, scraped_content: List[dict]) -> str:
        """Build a fallback response if synthesis fails."""
        parts = [f"Here's what I found about **{query}**:\n"]
        
        for source in scraped_content:
            parts.append(f"\n### {source['title']}")
            parts.append(f"*Source: {source['domain']}*\n")
            parts.append(source['content'][:500] + "...")
            parts.append(f"\n[Read more]({source['url']})\n")
        
        return "\n".join(parts)


# Singleton instance
web_search_agent_streaming = WebSearchAgentStreaming()
