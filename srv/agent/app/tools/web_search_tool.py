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
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # DuckDuckGo lite HTML search
            response = await client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; BusiboxBot/1.0)",
                },
            )
            response.raise_for_status()
            
            # Parse HTML results (simplified)
            html = response.text
            results = []
            
            # Basic HTML parsing (in production, use BeautifulSoup or similar)
            # This is a simplified version that looks for result patterns
            import re
            
            # Find result blocks (simplified pattern matching)
            # Format: <a href="URL">Title</a> followed by snippet
            pattern = r'<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>'
            matches = re.findall(pattern, html)
            
            seen_urls = set()
            for url, title in matches[:max_results * 2]:  # Get extra to filter
                # Skip DuckDuckGo internal links
                if url.startswith("/") or "duckduckgo.com" in url:
                    continue
                
                # Skip duplicates
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                # Create result (snippet extraction would need more parsing)
                results.append(
                    WebSearchResult(
                        title=title.strip(),
                        url=url,
                        snippet=f"Result from {url}",  # Simplified
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
                    error="No results found or parsing failed",
                )
            
            return WebSearchOutput(
                found=True,
                result_count=len(results),
                results=results,
                query=query,
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








