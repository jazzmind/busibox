"""Web scraper tool for fetching and extracting content from web pages."""
import re
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Tool


class ExtractedLink(BaseModel):
    """Extracted link from a web page."""
    text: str = Field(description="Link text")
    url: str = Field(description="Absolute URL")


class WebScraperOutput(BaseModel):
    """Output schema for web scraper tool."""
    success: bool = Field(description="Whether the page was successfully scraped")
    url: str = Field(description="The URL that was scraped")
    title: str = Field(default="", description="Page title")
    content: str = Field(default="", description="Extracted text content from the page")
    word_count: int = Field(default=0, description="Number of words in the content")
    links: List[ExtractedLink] = Field(default_factory=list, description="Extracted links (if requested)")
    error: Optional[str] = Field(default=None, description="Error message if scraping failed")


def clean_html(html: str) -> str:
    """
    Remove HTML tags and clean up the text content.
    
    Args:
        html: Raw HTML string
        
    Returns:
        Cleaned text content
    """
    # Remove script and style elements
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Remove HTML comments
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    
    # Remove navigation, header, footer elements (common noise)
    html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<header[^>]*>.*?</header>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<aside[^>]*>.*?</aside>', '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Replace block elements with newlines
    html = re.sub(r'<(p|div|br|h[1-6]|li|tr)[^>]*>', '\n', html, flags=re.IGNORECASE)
    
    # Remove all remaining HTML tags
    html = re.sub(r'<[^>]+>', '', html)
    
    # Decode HTML entities
    html = html.replace('&nbsp;', ' ')
    html = html.replace('&amp;', '&')
    html = html.replace('&lt;', '<')
    html = html.replace('&gt;', '>')
    html = html.replace('&quot;', '"')
    html = html.replace('&#39;', "'")
    
    # Clean up whitespace
    html = re.sub(r'\n\s*\n', '\n\n', html)  # Multiple newlines to double
    html = re.sub(r'[ \t]+', ' ', html)  # Multiple spaces to single
    html = '\n'.join(line.strip() for line in html.split('\n'))  # Strip each line
    html = html.strip()
    
    return html


def extract_title(html: str) -> str:
    """Extract the page title from HTML."""
    # Try <title> tag first
    match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    
    # Try <h1> as fallback
    match = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    
    return ""


def extract_links(html: str, base_url: str) -> List[ExtractedLink]:
    """
    Extract links from HTML content.
    
    Args:
        html: Raw HTML string
        base_url: Base URL for resolving relative links
        
    Returns:
        List of ExtractedLink objects
    """
    links = []
    seen_urls = set()
    
    # Find all <a> tags with href
    pattern = r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>'
    matches = re.findall(pattern, html, re.IGNORECASE)
    
    for href, text in matches:
        # Skip anchors, javascript, and mailto
        if href.startswith('#') or href.startswith('javascript:') or href.startswith('mailto:'):
            continue
        
        # Resolve relative URLs
        absolute_url = urljoin(base_url, href)
        
        # Skip if already seen
        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)
        
        # Clean text
        clean_text = re.sub(r'\s+', ' ', text).strip()
        if not clean_text:
            # Use URL as text if no text provided
            clean_text = urlparse(absolute_url).path.split('/')[-1] or absolute_url
        
        links.append(ExtractedLink(
            text=clean_text[:100],  # Limit text length
            url=absolute_url
        ))
        
        # Limit number of links
        if len(links) >= 50:
            break
    
    return links


async def scrape_webpage(
    url: str,
    extract_links: bool = False,
    max_content_length: int = 10000,
) -> WebScraperOutput:
    """
    Fetch and extract content from a web page.
    
    This tool retrieves a web page and extracts its text content, removing
    HTML markup, scripts, and other non-content elements. Useful for reading
    full articles, documentation, or any web page content.
    
    Args:
        url: URL of the web page to scrape
        extract_links: Whether to extract links from the page (default: False)
        max_content_length: Maximum characters to return (default: 10000)
        
    Returns:
        WebScraperOutput with title, content, and optionally links
        
    Note:
        - Respects basic web standards but doesn't check robots.txt
        - Some websites may block automated requests
        - JavaScript-rendered content will not be captured
    """
    # Validate URL
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return WebScraperOutput(
            success=False,
            url=url,
            error="Invalid URL. Please provide a complete URL starting with http:// or https://",
        )
    
    if parsed.scheme not in ('http', 'https'):
        return WebScraperOutput(
            success=False,
            url=url,
            error="Only HTTP and HTTPS URLs are supported",
        )
    
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            
            # Check content type
            content_type = response.headers.get('content-type', '')
            if 'text/html' not in content_type and 'text/plain' not in content_type:
                return WebScraperOutput(
                    success=False,
                    url=str(response.url),
                    error=f"Unsupported content type: {content_type}. This tool only supports HTML and text pages.",
                )
            
            html = response.text
            
            # Extract title
            title = extract_title(html)
            
            # Extract and clean content
            content = clean_html(html)
            
            # Truncate if too long
            if len(content) > max_content_length:
                content = content[:max_content_length] + "\n\n[Content truncated...]"
            
            # Word count
            word_count = len(content.split())
            
            # Extract links if requested
            links = []
            if extract_links:
                links = extract_links(html, str(response.url))
            
            return WebScraperOutput(
                success=True,
                url=str(response.url),
                title=title,
                content=content,
                word_count=word_count,
                links=links,
            )
    
    except httpx.TimeoutException:
        return WebScraperOutput(
            success=False,
            url=url,
            error="Request timed out. The server took too long to respond.",
        )
    
    except httpx.HTTPStatusError as e:
        return WebScraperOutput(
            success=False,
            url=url,
            error=f"HTTP error {e.response.status_code}: {e.response.reason_phrase}",
        )
    
    except httpx.RequestError as e:
        return WebScraperOutput(
            success=False,
            url=url,
            error=f"Request failed: {str(e)}",
        )
    
    except Exception as e:
        return WebScraperOutput(
            success=False,
            url=url,
            error=f"Scraping failed: {str(e)}",
        )


# Create the Pydantic AI tool
web_scraper_tool = Tool(
    scrape_webpage,
    takes_ctx=False,
    name="web_scraper",
    description="""Fetch and extract content from a web page URL.
Use this tool when:
- You have a specific URL and need to read its full content
- You want to get more details from a search result
- You need to extract information from a documentation page or article

The tool:
- Fetches the HTML content
- Removes scripts, styles, and navigation elements
- Extracts clean text content
- Can optionally extract links from the page

Note: This only works for static HTML pages. JavaScript-rendered content won't be captured.""",
)
