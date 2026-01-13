"""
Web search tool supporting multiple providers.

Supports:
- DuckDuckGo (free, no API key required)
- Tavily (requires API key)
- Perplexity (requires API key)
- Brave Search (requires API key)

When multiple providers are enabled, searches run in parallel and results are merged.
"""
import asyncio
import os
import re
import urllib.parse
from typing import List, Optional, Dict, Any

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Tool

from app.config.settings import get_settings

settings = get_settings()


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
    query: str = Field(description="The search query used")
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
        print(f"DuckDuckGo search error: {e}")
    
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
) -> WebSearchOutput:
    """
    Search the web using multiple providers in parallel.
    
    Args:
        query: Search query string
        max_results: Maximum number of results PER PROVIDER (default: 5).
                     When multiple providers are enabled, each returns up to this many results.
        providers: Optional provider configuration override
        
    Returns:
        WebSearchOutput with merged, deduplicated results from all providers
    """
    # Get provider config
    config = providers or get_provider_config()
    
    # Build list of search tasks
    tasks = []
    task_names = []
    
    if config.get("duckduckgo", {}).get("enabled", True):
        tasks.append(search_duckduckgo(query, max_results))
        task_names.append("duckduckgo")
    
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
    
    if config.get("brave", {}).get("enabled", False):
        api_key = config.get("brave", {}).get("api_key", "")
        if api_key:
            tasks.append(search_brave(query, max_results, api_key))
            task_names.append("brave")
    
    if not tasks:
        # Fallback to DuckDuckGo if nothing enabled
        tasks.append(search_duckduckgo(query, max_results))
        task_names.append("duckduckgo")
    
    # Run all searches in parallel
    all_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Merge and deduplicate results, tracking counts per provider
    merged_results: List[WebSearchResult] = []
    seen_urls = set()
    providers_used = []
    results_per_provider: Dict[str, int] = {}
    
    for i, result_set in enumerate(all_results):
        if isinstance(result_set, Exception):
            print(f"Search provider {task_names[i]} failed: {result_set}")
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
            providers_used=providers_used,
            results_per_provider=results_per_provider,
            error="No results found from any provider.",
        )
    
    return WebSearchOutput(
        found=True,
        result_count=len(final_results),
        results=final_results,
        query=query,
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
