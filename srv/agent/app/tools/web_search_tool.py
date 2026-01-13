"""Web search tool using DuckDuckGo."""
from typing import List, Optional
import httpx

from pydantic import BaseModel, Field
from pydantic_ai import Tool


class WebSearchResult(BaseModel):
    """Individual web search result."""
    title: str = Field(description="Title of the web page")
    url: str = Field(description="URL of the web page")
    snippet: str = Field(description="Text snippet from the page")


class WebSearchOutput(BaseModel):
    """Output schema for web search tool."""
    found: bool = Field(description="Whether results were found")
    result_count: int = Field(description="Number of results returned")
    results: List[WebSearchResult] = Field(description="List of search results")
    query: str = Field(description="The search query used")
    error: Optional[str] = Field(default=None, description="Error message if search failed")


async def search_web(query: str, max_results: int = 5) -> WebSearchOutput:
    """
    Search the web for up-to-date information using DuckDuckGo.
    
    This tool performs web searches to find current information that may not be
    in the AI's training data. Useful for recent events, current prices, weather,
    news, and other time-sensitive information.
    
    Args:
        query: Search query string
        max_results: Maximum number of results to return (default: 5)
        
    Returns:
        WebSearchOutput with search results and metadata
        
    Note:
        Uses DuckDuckGo's HTML search (no API key required).
        Results may be limited compared to dedicated search APIs.
    """
    try:
        # Use DuckDuckGo HTML search
        # This is a simple implementation - for production, consider using:
        # - duckduckgo-search library
        # - SerpAPI
        # - Tavily API
        # - Brave Search API
        
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # Use DuckDuckGo HTML search (more reliable than lite)
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
            
            # Parse HTML results
            html = response.text
            results = []
            
            import re
            
            # DuckDuckGo HTML results pattern:
            # Results are in <a class="result__a" href="...">Title</a>
            # with snippets in <a class="result__snippet">...</a>
            
            # Find result links - DuckDuckGo wraps URLs in redirect
            result_pattern = r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>'
            snippet_pattern = r'<a[^>]*class="result__snippet"[^>]*>([^<]+(?:<[^>]+>[^<]*</[^>]+>)*[^<]*)</a>'
            
            # Alternative pattern for direct links
            alt_pattern = r'<a[^>]*href="(https?://[^"]+)"[^>]*>([^<]+)</a>'
            
            matches = re.findall(result_pattern, html)
            snippets = re.findall(snippet_pattern, html)
            
            # If no results from result pattern, try alternative
            if not matches:
                matches = re.findall(alt_pattern, html)
            
            seen_urls = set()
            snippet_idx = 0
            
            for url, title in matches[:max_results * 3]:  # Get extra to filter
                # Extract actual URL from DuckDuckGo redirect
                if "duckduckgo.com" in url and "uddg=" in url:
                    # Extract the actual URL from the redirect
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                    if "uddg" in parsed:
                        url = urllib.parse.unquote(parsed["uddg"][0])
                
                # Skip DuckDuckGo internal links
                if "duckduckgo.com" in url or url.startswith("/"):
                    continue
                
                # Skip non-http URLs
                if not url.startswith("http"):
                    continue
                
                # Skip duplicates
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                # Get snippet if available
                snippet = f"Result from {url}"
                if snippet_idx < len(snippets):
                    # Clean HTML tags from snippet
                    raw_snippet = snippets[snippet_idx]
                    clean_snippet = re.sub(r'<[^>]+>', '', raw_snippet).strip()
                    if clean_snippet:
                        snippet = clean_snippet[:200]
                    snippet_idx += 1
                
                # Create result
                results.append(
                    WebSearchResult(
                        title=title.strip(),
                        url=url,
                        snippet=snippet,
                    )
                )
                
                if len(results) >= max_results:
                    break
            
            if not results:
                return WebSearchOutput(
                    found=False,
                    result_count=0,
                    results=[],
                    query=query,
                    error="No results found. The search may have been blocked or returned no matches.",
                )
            
            return WebSearchOutput(
                found=True,
                result_count=len(results),
                results=results,
                query=query,
            )
    
    except httpx.TimeoutException:
        return WebSearchOutput(
            found=False,
            result_count=0,
            results=[],
            query=query,
            error="Web search timed out. Please try again.",
        )
    
    except Exception as e:
        return WebSearchOutput(
            found=False,
            result_count=0,
            results=[],
            query=query,
            error=f"Web search failed: {str(e)}",
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

The tool uses DuckDuckGo search and returns titles, URLs, and snippets.
Always cite the URLs when using information from search results.""",
)








