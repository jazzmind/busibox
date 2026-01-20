"""
Web search tool supporting multiple providers.

Supports:
- DuckDuckGo (free, no API key required) - needs keyword optimization
- Tavily (requires API key) - AI-powered, handles natural language
- Perplexity (requires API key) - AI-powered, handles natural language
- Brave Search (requires API key) - traditional search, benefits from keywords

When multiple providers are enabled, searches run in parallel and results are merged.
Each provider receives an appropriately formatted query.
"""
import asyncio
import logging
import os
import re
import urllib.parse
from typing import List, Optional, Dict, Any

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, Tool
from pydantic_ai.models.openai import OpenAIChatModel

from app.config.settings import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# Provider categories based on query handling capabilities
AI_POWERED_PROVIDERS = {"tavily", "perplexity"}  # Can handle natural language
KEYWORD_PROVIDERS = {"duckduckgo", "brave"}  # Need keyword-focused queries

# Query optimization prompt for keyword-based search providers
QUERY_OPTIMIZATION_PROMPT = """You are a search query optimizer. Convert the user's natural language request into 1-3 concise keyword-based search queries.

Rules:
- Extract the core topic/keywords the user wants to find
- Keep each query under 10 words
- Remove instructional phrases like "find me", "search for", "use X to"
- If specific sites are mentioned (e.g., "use boardgamegeek.com"), use site: operator
- For recent/news queries, include time-relevant terms like "2026", "latest", "news"
- Return ONLY the search queries, one per line
- No explanations or numbering

Example input: "Use boardgamegeek.com to find the latest board game releases and reviews"
Example output:
site:boardgamegeek.com board game releases 2026
site:boardgamegeek.com board game reviews latest"""


class WebSearchResult(BaseModel):
    """Individual web search result."""
    title: str = Field(description="Title of the web page")
    url: str = Field(description="URL of the web page")
    snippet: str = Field(description="Text snippet from the page")
    source: str = Field(default="unknown", description="Provider that returned this result")


class WebSearchOutput(BaseModel):
    """Output schema for web search tool."""
    found: bool = Field(description="Whether results were found")
    result_count: int = Field(description="Total number of results returned across all providers")
    results: List[WebSearchResult] = Field(description="List of search results")
    query: str = Field(description="The original search query")
    optimized_queries: List[str] = Field(default_factory=list, description="Optimized keyword queries used for search")
    providers_used: List[str] = Field(default_factory=list, description="Providers that returned results")
    results_per_provider: Dict[str, int] = Field(default_factory=dict, description="Number of results from each provider")
    error: Optional[str] = Field(default=None, description="Error message if search failed")


# Provider configuration - reads from app settings as defaults
def get_provider_config() -> Dict[str, Dict[str, Any]]:
    """Get provider configuration from app settings (defaults)."""
    from app.config.settings import get_settings
    settings = get_settings()
    
    return {
        "duckduckgo": {
            "enabled": settings.search_duckduckgo_enabled,
        },
        "tavily": {
            "enabled": settings.search_tavily_enabled,
            "api_key": settings.tavily_api_key or "",
        },
        "perplexity": {
            "enabled": settings.search_perplexity_enabled,
            "api_key": settings.perplexity_api_key or "",
        },
        "brave": {
            "enabled": settings.search_brave_enabled,
            "api_key": settings.brave_api_key or "",
        },
    }


async def get_provider_config_for_context(
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Get provider configuration with hierarchical lookup.
    
    Priority (highest first):
    1. User-level config (if user_id provided)
    2. Agent-level config (if agent_id provided)
    3. System-level config
    4. App settings defaults
    """
    from app.db.session import get_session_context
    from app.models.domain import ToolConfig
    from sqlalchemy import select, and_
    import uuid as uuid_module
    
    # Get default config from settings
    default_config = get_provider_config()
    
    try:
        async with get_session_context() as session:
            found_config = None
            
            # 1. Try user-level config
            if user_id:
                stmt = select(ToolConfig).where(
                    and_(
                        ToolConfig.tool_name == "web_search",
                        ToolConfig.scope == "user",
                        ToolConfig.user_id == user_id
                    )
                )
                result = await session.execute(stmt)
                found_config = result.scalar_one_or_none()
            
            # 2. Try agent-level config
            if not found_config and agent_id:
                try:
                    agent_uuid = uuid_module.UUID(agent_id) if isinstance(agent_id, str) else agent_id
                    stmt = select(ToolConfig).where(
                        and_(
                            ToolConfig.tool_name == "web_search",
                            ToolConfig.scope == "agent",
                            ToolConfig.agent_id == agent_uuid
                        )
                    )
                    result = await session.execute(stmt)
                    found_config = result.scalar_one_or_none()
                except (ValueError, TypeError):
                    pass
            
            # 3. Try system-level config
            if not found_config:
                stmt = select(ToolConfig).where(
                    and_(
                        ToolConfig.tool_name == "web_search",
                        ToolConfig.scope == "system"
                    )
                )
                result = await session.execute(stmt)
                found_config = result.scalar_one_or_none()
            
            # Apply found config over defaults
            if found_config and found_config.config.get("providers"):
                db_providers = found_config.config["providers"]
                merged_config = {}
                
                for provider, defaults in default_config.items():
                    db_prov = db_providers.get(provider, {})
                    merged_config[provider] = {
                        "enabled": db_prov.get("enabled", defaults.get("enabled", False)),
                        "api_key": db_prov.get("api_key") or defaults.get("api_key", ""),
                    }
                
                return merged_config
                
    except Exception as e:
        print(f"Error loading tool config: {e}")
    
    return default_config


# Lazy-initialized query optimizer (created on first use)
_query_optimizer: Optional[Agent] = None


def _get_query_optimizer() -> Agent:
    """
    Get or create the query optimizer agent.
    
    Uses a fast/cheap model since query optimization is a simple task.
    The model is configured via FAST_MODEL env var (defaults to 'fast' purpose in LiteLLM).
    """
    global _query_optimizer
    if _query_optimizer is None:
        settings = get_settings()
        # Use fast model for simple query optimization task
        model = OpenAIChatModel(
            model_name=settings.fast_model,
            provider="openai",
        )
        _query_optimizer = Agent(
            model=model,
            system_prompt=QUERY_OPTIMIZATION_PROMPT,
        )
        logger.info(f"Query optimizer initialized with fast model: {settings.fast_model}")
    return _query_optimizer


async def optimize_query_for_keywords(query: str) -> List[str]:
    """
    Optimize a natural language query for keyword-based search providers.
    
    Uses an LLM to convert instructional/conversational queries into
    concise keyword queries suitable for DuckDuckGo, Brave, etc.
    
    Args:
        query: Natural language query from user
        
    Returns:
        List of 1-3 optimized keyword queries
    """
    # Quick check: if query is already short and keyword-like, use as-is
    if len(query.split()) <= 8 and not any(phrase in query.lower() for phrase in [
        "find me", "search for", "look for", "use ", "please ", "can you", "i want"
    ]):
        logger.debug(f"Query already keyword-like, using as-is: {query}")
        return [query]
    
    try:
        optimizer = _get_query_optimizer()
        result = await optimizer.run(query)
        
        # Extract output
        output_text = ""
        if hasattr(result, 'output') and result.output:
            output_text = str(result.output)
        elif hasattr(result, 'data') and result.data:
            output_text = str(result.data)
        else:
            output_text = str(result)
        
        # Parse queries (one per line)
        queries = []
        for line in output_text.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                # Clean up numbering
                line = re.sub(r'^[\d]+[.\)]\s*', '', line)
                line = re.sub(r'^[-*]\s*', '', line)
                if line:
                    queries.append(line)
        
        if queries:
            logger.info(f"Optimized query '{query[:50]}...' into {len(queries)} keyword queries: {queries}")
            return queries[:3]
        
        # Fallback: truncate original
        logger.warning("Query optimization returned no results, using truncated original")
        return [query[:100]]
        
    except Exception as e:
        logger.error(f"Query optimization failed: {e}")
        # Fallback: use original truncated
        return [query[:100]]


async def search_duckduckgo(query: str, max_results: int = 5) -> List[WebSearchResult]:
    """Search using DuckDuckGo HTML (free, no API key)."""
    results = []
    
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                },
            )
            response.raise_for_status()
            
            html = response.text
            
            # Parse results
            result_pattern = r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>'
            snippet_pattern = r'<a[^>]*class="result__snippet"[^>]*>([^<]+(?:<[^>]+>[^<]*</[^>]+>)*[^<]*)</a>'
            
            matches = re.findall(result_pattern, html)
            snippets = re.findall(snippet_pattern, html)
            
            if not matches:
                alt_pattern = r'<a[^>]*href="(https?://[^"]+)"[^>]*>([^<]+)</a>'
                matches = re.findall(alt_pattern, html)
            
            seen_urls = set()
            snippet_idx = 0
            
            for url, title in matches[:max_results * 3]:
                # Extract actual URL from DuckDuckGo redirect
                if "duckduckgo.com" in url and "uddg=" in url:
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                    if "uddg" in parsed:
                        url = urllib.parse.unquote(parsed["uddg"][0])
                
                if "duckduckgo.com" in url or url.startswith("/"):
                    continue
                if not url.startswith("http"):
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                snippet = f"Result from {url}"
                if snippet_idx < len(snippets):
                    raw_snippet = snippets[snippet_idx]
                    clean_snippet = re.sub(r'<[^>]+>', '', raw_snippet).strip()
                    if clean_snippet:
                        snippet = clean_snippet[:200]
                    snippet_idx += 1
                
                results.append(WebSearchResult(
                    title=title.strip(),
                    url=url,
                    snippet=snippet,
                    source="duckduckgo"
                ))
                
                if len(results) >= max_results:
                    break
                    
    except Exception as e:
        logger.error(f"DuckDuckGo search error for query '{query[:50]}...': {e}")
    
    logger.info(f"DuckDuckGo search for '{query[:50]}...' returned {len(results)} results")
    return results


async def search_tavily(query: str, max_results: int = 5, api_key: str = "") -> List[WebSearchResult]:
    """Search using Tavily API."""
    if not api_key:
        return []
    
    results = []
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": max_results,
                    "include_answer": False,
                },
            )
            response.raise_for_status()
            
            data = response.json()
            
            for result in data.get("results", [])[:max_results]:
                results.append(WebSearchResult(
                    title=result.get("title", ""),
                    url=result.get("url", ""),
                    snippet=result.get("content", "")[:200],
                    source="tavily"
                ))
                
    except Exception as e:
        print(f"Tavily search error: {e}")
    
    return results


async def search_perplexity(query: str, max_results: int = 5, api_key: str = "") -> List[WebSearchResult]:
    """Search using Perplexity API (uses their sonar model for search)."""
    if not api_key:
        return []
    
    results = []
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a search assistant. Return search results as a JSON array with objects containing 'title', 'url', and 'snippet' fields. Return only the JSON array, no other text."
                        },
                        {
                            "role": "user",
                            "content": f"Search for: {query}. Return top {max_results} results."
                        }
                    ],
                    "max_tokens": 1000,
                    "return_citations": True,
                },
            )
            response.raise_for_status()
            
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # Try to parse JSON from response
            try:
                import json
                # Find JSON array in response
                json_match = re.search(r'\[.*\]', content, re.DOTALL)
                if json_match:
                    search_results = json.loads(json_match.group())
                    for result in search_results[:max_results]:
                        if isinstance(result, dict):
                            results.append(WebSearchResult(
                                title=result.get("title", ""),
                                url=result.get("url", ""),
                                snippet=result.get("snippet", "")[:200],
                                source="perplexity"
                            ))
            except json.JSONDecodeError:
                pass
            
            # Also extract citations if available
            citations = data.get("citations", [])
            for citation in citations[:max_results - len(results)]:
                if isinstance(citation, str) and citation.startswith("http"):
                    results.append(WebSearchResult(
                        title=urllib.parse.urlparse(citation).netloc,
                        url=citation,
                        snippet="Source from Perplexity search",
                        source="perplexity"
                    ))
                    
    except Exception as e:
        print(f"Perplexity search error: {e}")
    
    return results


async def search_brave(query: str, max_results: int = 5, api_key: str = "") -> List[WebSearchResult]:
    """Search using Brave Search API."""
    if not api_key:
        return []
    
    results = []
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={
                    "q": query,
                    "count": max_results,
                },
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
            )
            response.raise_for_status()
            
            data = response.json()
            
            for result in data.get("web", {}).get("results", [])[:max_results]:
                results.append(WebSearchResult(
                    title=result.get("title", ""),
                    url=result.get("url", ""),
                    snippet=result.get("description", "")[:200],
                    source="brave"
                ))
                
    except Exception as e:
        print(f"Brave search error: {e}")
    
    return results


async def search_web(
    query: str, 
    max_results: int = 5,
    providers: Optional[Dict[str, Dict[str, Any]]] = None,
    optimize_query: bool = True,
) -> WebSearchOutput:
    """
    Search the web using multiple providers in parallel.
    
    Automatically optimizes queries for each provider type:
    - AI-powered providers (Tavily, Perplexity): receive the original natural language query
    - Keyword providers (DuckDuckGo, Brave): receive optimized keyword queries
    
    Args:
        query: Search query string (can be natural language)
        max_results: Maximum number of results PER PROVIDER (default: 5).
                     When multiple providers are enabled, each returns up to this many results.
        providers: Optional provider configuration override
        optimize_query: Whether to optimize queries for keyword-based providers (default: True)
        
    Returns:
        WebSearchOutput with merged, deduplicated results from all providers
    """
    # Get provider config
    config = providers or get_provider_config()
    
    # Determine which provider types are enabled
    has_keyword_providers = (
        config.get("duckduckgo", {}).get("enabled", True) or
        (config.get("brave", {}).get("enabled", False) and config.get("brave", {}).get("api_key"))
    )
    has_ai_providers = (
        (config.get("tavily", {}).get("enabled", False) and config.get("tavily", {}).get("api_key")) or
        (config.get("perplexity", {}).get("enabled", False) and config.get("perplexity", {}).get("api_key"))
    )
    
    # Optimize query for keyword-based providers if needed
    keyword_queries = [query]  # Default to original
    if optimize_query and has_keyword_providers:
        keyword_queries = await optimize_query_for_keywords(query)
    
    logger.info(f"search_web: original='{query[:50]}...', keyword_queries={keyword_queries}, has_ai={has_ai_providers}, has_keyword={has_keyword_providers}")
    
    # Build list of search tasks
    tasks = []
    task_names = []
    
    # Keyword-based providers get optimized queries
    if config.get("duckduckgo", {}).get("enabled", True):
        # Run search for each optimized query
        for kw_query in keyword_queries:
            tasks.append(search_duckduckgo(kw_query, max_results))
            task_names.append("duckduckgo")
    
    if config.get("brave", {}).get("enabled", False):
        api_key = config.get("brave", {}).get("api_key", "")
        if api_key:
            for kw_query in keyword_queries:
                tasks.append(search_brave(kw_query, max_results, api_key))
                task_names.append("brave")
    
    # AI-powered providers get the original natural language query
    if config.get("tavily", {}).get("enabled", False):
        api_key = config.get("tavily", {}).get("api_key", "")
        if api_key:
            tasks.append(search_tavily(query, max_results, api_key))
            task_names.append("tavily")
    
    if config.get("perplexity", {}).get("enabled", False):
        api_key = config.get("perplexity", {}).get("api_key", "")
        if api_key:
            tasks.append(search_perplexity(query, max_results, api_key))
            task_names.append("perplexity")
    
    if not tasks:
        # Fallback to DuckDuckGo with optimized queries if nothing enabled
        logger.warning(f"No search providers configured, falling back to DuckDuckGo")
        for kw_query in keyword_queries:
            tasks.append(search_duckduckgo(kw_query, max_results))
            task_names.append("duckduckgo")
    
    logger.info(f"search_web: Running {len(tasks)} search tasks: {task_names}")
    
    # Run all searches in parallel
    all_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Merge and deduplicate results, tracking counts per provider
    merged_results: List[WebSearchResult] = []
    seen_urls = set()
    providers_used = []
    results_per_provider: Dict[str, int] = {}
    
    for i, result_set in enumerate(all_results):
        if isinstance(result_set, Exception):
            logger.error(f"Search provider {task_names[i]} failed with exception: {result_set}")
            continue
        
        provider_name = task_names[i]
        provider_count = 0
        
        if result_set:
            providers_used.append(provider_name)
            
        for result in result_set:
            # Normalize URL for deduplication
            normalized_url = result.url.lower().rstrip("/")
            if normalized_url not in seen_urls:
                seen_urls.add(normalized_url)
                merged_results.append(result)
                provider_count += 1
        
        if provider_count > 0:
            results_per_provider[provider_name] = provider_count
    
    # Sort by source priority (paid APIs first as they tend to have better results)
    source_priority = {"tavily": 0, "perplexity": 1, "brave": 2, "duckduckgo": 3}
    merged_results.sort(key=lambda r: source_priority.get(r.source, 99))
    
    # NOTE: We no longer limit to max_results since each provider already returns max_results
    # This allows users to see all results from all providers
    final_results = merged_results
    
    if not final_results:
        return WebSearchOutput(
            found=False,
            result_count=0,
            results=[],
            query=query,
            optimized_queries=keyword_queries,
            providers_used=providers_used,
            results_per_provider=results_per_provider,
            error="No results found from any provider.",
        )
    
    return WebSearchOutput(
        found=True,
        result_count=len(final_results),
        results=final_results,
        query=query,
        optimized_queries=keyword_queries,
        providers_used=providers_used,
        results_per_provider=results_per_provider,
    )


# Create the Pydantic AI tool
web_search_tool = Tool(
    search_web,
    takes_ctx=False,
    name="web_search",
    description="""Search the web for current, up-to-date information.

Use this tool when:
- The user asks about recent events or news
- You need current information (prices, weather, etc.)
- The question requires information beyond your training data
- You need to verify or supplement your knowledge with current sources

The tool supports multiple search providers (DuckDuckGo, Tavily, Perplexity, Brave).
When multiple providers are enabled, searches run in parallel and each provider returns
up to max_results results, which are then merged and deduplicated.

Always cite the URLs when using information from search results.""",
)
