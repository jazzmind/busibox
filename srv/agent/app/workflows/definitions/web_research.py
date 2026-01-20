"""
Web Research Workflow Definition.

A multi-step workflow that:
1. Uses web_search_agent to search for query (gets N x 3 results)
2. Uses document_search agent to check if URLs exist in user's library
3. Filters out already-stored URLs from scrape list
4. If remaining < min_results, loops back for more search results
5. Uses web_scraper to fetch remaining URLs
6. LLM analyzes scraped content for gateway pages (pages with links to follow)
7. Follows gateway links if needed (up to scrape_depth levels)
8. Stores parsed content in user's documents (personal research folder)
9. Synthesizes final summary

Configuration options (passed via input_data):
- query: str - The search query
- deep: bool - If True, get more results (15) before summarizing; if False, use 5 results
- min_results: int - Minimum results before summarizing (default based on deep)
- recency: str - Filter results by recency (e.g., "7d" for last 7 days)
- scrape_depth: int - How many levels of gateway links to follow (default 1)
- store_results: bool - Whether to store results in user's documents (default True)
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import uuid

# The workflow definition as a dict (matches WorkflowDefinition model)
WEB_RESEARCH_WORKFLOW_DEFINITION: Dict[str, Any] = {
    "name": "web-research-workflow",
    "description": "Deep web research with deduplication, gateway page handling, and document storage",
    "steps": [
        # Step 1: Initial web search using the web_search tool directly
        # This returns structured results with query optimization info
        {
            "id": "initial_search",
            "type": "tool",
            "tool": "web_search",
            "tool_args": {
                "query": "$.input.query",
            },
            "description": "Search the web using query optimization",
        },
        
        # Step 2: Extract URLs from search results
        {
            "id": "extract_urls",
            "type": "tool",
            "tool": "extract_urls_from_search_results",
            "tool_args": {
                "search_results": "$.initial_search",
            },
            "description": "Extract URLs from web search results",
        },
        
        # Step 3: Check which URLs already exist in user's document library
        {
            "id": "check_existing",
            "type": "tool",
            "tool": "check_urls_in_library",
            "tool_args": {
                "urls": "$.extract_urls.urls",
            },
            "description": "Check which URLs already exist in user's document library",
        },
        
        # Step 4: Filter to get new URLs only
        {
            "id": "filter_urls",
            "type": "tool",
            "tool": "filter_new_urls",
            "tool_args": {
                "urls": "$.extract_urls.urls",
                "existing_urls": "$.check_existing.existing_urls",
            },
            "description": "Filter out URLs that already exist in the library",
        },
        
        # Step 5: Check if we have any new URLs to scrape
        {
            "id": "check_has_urls",
            "type": "condition",
            "condition": {
                "field": "$.filter_urls.remaining_count",
                "operator": "gt",
                "value": 0,
                "then_step": "scrape_loop",
                "else_step": "synthesize",
            },
            "description": "Check if there are new URLs to scrape",
        },
        
        # Step 6: Scrape URLs in a loop
        {
            "id": "scrape_loop",
            "type": "loop",
            "loop_config": {
                "items_path": "$.filter_urls.new_urls",
                "item_variable": "current_url",
                "max_iterations": 20,
                "steps": [
                    {
                        "id": "scrape_url",
                        "type": "tool",
                        "tool": "web_scraper",
                        "tool_args": {
                            "url": "$.current_url",
                            "max_content_length": 8000,
                        },
                    },
                    {
                        "id": "analyze_content",
                        "type": "agent",
                        "agent": "chat-agent",
                        "agent_prompt": """Analyze this scraped content and determine:
1. Is this a "gateway page" (a page that mainly links to other articles/content)?
2. If gateway: extract the URLs to follow (max 3 most relevant)
3. If content page: summarize the key information (2-3 sentences)

Return JSON: {"is_gateway": bool, "follow_urls": [], "summary": ""}

Content from $.scrape_url.url:
$.scrape_url.content""",
                    },
                    {
                        "id": "check_gateway",
                        "type": "condition",
                        "condition": {
                            "field": "$.analyze_content.is_gateway",
                            "operator": "eq",
                            "value": True,
                            "then_step": "follow_gateway_links",
                            "else_step": "store_content",
                        },
                    },
                    {
                        "id": "follow_gateway_links",
                        "type": "loop",
                        "loop_config": {
                            "items_path": "$.analyze_content.follow_urls",
                            "item_variable": "gateway_url",
                            "max_iterations": 3,
                            "steps": [
                                {
                                    "id": "scrape_gateway_url",
                                    "type": "tool",
                                    "tool": "web_scraper",
                                    "tool_args": {
                                        "url": "$.gateway_url",
                                        "max_content_length": 8000,
                                    },
                                },
                            ],
                        },
                    },
                    {
                        "id": "store_content",
                        "type": "condition",
                        "condition": {
                            "field": "$.input.store_results",
                            "operator": "eq",
                            "value": True,
                            "then_step": "ingest_content",
                            "else_step": None,
                        },
                    },
                    {
                        "id": "ingest_content",
                        "type": "tool",
                        "tool": "ingest",
                        "tool_args": {
                            "content": "$.scrape_url.content",
                            "title": "$.scrape_url.title",
                            "url": "$.scrape_url.url",
                            "folder": "personal-research",
                            "metadata": {
                                "source": "web-research-workflow",
                                "query": "$.input.query",
                                "scraped_at": "$.workflow.timestamp",
                            },
                        },
                    },
                ],
            },
            "description": "Scrape each URL and analyze for gateway pages",
        },
        
        # Step 9: Final synthesis
        {
            "id": "synthesize",
            "type": "agent",
            "agent": "web-search-agent",
            "agent_prompt": """Based on the research conducted, synthesize a comprehensive summary.

Query: $.input.query

Scraped content summaries:
$.scrape_loop.results

Provide:
1. A direct answer to the query
2. Key findings from the sources
3. Source citations with URLs""",
            "description": "Synthesize final research summary",
        },
    ],
    "guardrails": {
        "request_limit": 50,
        "tool_calls_limit": 100,
        "total_tokens_limit": 100000,
        "max_cost_dollars": 2.0,
        "timeout_seconds": 300,
    },
    "trigger": {
        "type": "manual",
        "allowed_types": ["manual", "api", "task"],
    },
}


def create_web_research_workflow(
    deep: bool = False,
    min_results: Optional[int] = None,
    recency: Optional[str] = None,
    scrape_depth: int = 1,
    store_results: bool = True,
    custom_guardrails: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a web research workflow definition with custom configuration.
    
    Args:
        deep: If True, gather more results (15) before summarizing
        min_results: Minimum results before summarizing (default 5 or 15 based on deep)
        recency: Filter results by recency (e.g., "7d", "30d", None for no filter)
        scrape_depth: How many levels of gateway links to follow
        store_results: Whether to store results in user's documents
        custom_guardrails: Override default guardrails
        
    Returns:
        Workflow definition dict ready to be inserted into database
    """
    # Clone the base definition
    import copy
    workflow = copy.deepcopy(WEB_RESEARCH_WORKFLOW_DEFINITION)
    
    # Set default min_results based on deep mode
    if min_results is None:
        min_results = 15 if deep else 5
    
    # Add configuration to workflow metadata
    workflow["config"] = {
        "deep": deep,
        "min_results": min_results,
        "recency": recency,
        "scrape_depth": scrape_depth,
        "store_results": store_results,
    }
    
    # Apply custom guardrails if provided
    if custom_guardrails:
        workflow["guardrails"].update(custom_guardrails)
    
    # Adjust loop iterations based on deep mode
    if deep:
        # Find the scrape_loop step and increase max_iterations
        for step in workflow["steps"]:
            if step["id"] == "scrape_loop" and "loop_config" in step:
                step["loop_config"]["max_iterations"] = 30
    
    return workflow


def get_default_input_data(
    query: str,
    deep: bool = False,
    recency: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get default input_data for executing the web research workflow.
    
    Args:
        query: The search query
        deep: Whether to use deep research mode
        recency: Recency filter (e.g., "7d")
        
    Returns:
        Input data dict for workflow execution
    """
    min_results = 15 if deep else 5
    
    return {
        "query": query,
        "deep": deep,
        "min_results": min_results,
        "recency": recency,
        "scrape_depth": 1,
        "store_results": True,
    }


# Simplified version for quick tasks (no deduplication, no storage)
WEB_RESEARCH_SIMPLE_WORKFLOW: Dict[str, Any] = {
    "name": "web-research-simple",
    "description": "Quick web research without deduplication or storage",
    "steps": [
        # Step 1: Web search using tool directly
        {
            "id": "search",
            "type": "tool",
            "tool": "web_search",
            "tool_args": {
                "query": "$.input.query",
                "max_results": 5,
            },
            "description": "Search the web for relevant content",
        },
        # Step 2: Extract URLs from search results
        {
            "id": "extract_urls",
            "type": "tool",
            "tool": "extract_urls_from_search_results",
            "tool_args": {
                "search_results": "$.search",
            },
            "description": "Extract URLs from search results",
        },
        # Step 3: Scrape top 3 URLs
        {
            "id": "scrape_loop",
            "type": "loop",
            "loop_config": {
                "items_path": "$.extract_urls.urls",
                "item_variable": "current_url",
                "max_iterations": 3,
                "steps": [
                    {
                        "id": "scrape_url",
                        "type": "tool",
                        "tool": "web_scraper",
                        "tool_args": {
                            "url": "$.current_url",
                            "max_content_length": 8000,
                        },
                        "description": "Scrape webpage content",
                    },
                ],
            },
            "description": "Scrape top URLs for content",
        },
        # Step 4: Synthesize results using chat agent
        {
            "id": "synthesize",
            "type": "agent",
            "agent": "chat-agent",
            "agent_prompt": """Synthesize a comprehensive answer based on the search results and scraped content.

Query: $.input.query

Search Results Summary:
- Found $.search.result_count results
- Optimized queries used: $.search.optimized_queries

Scraped Content:
$.scrape_loop

Provide a well-organized answer with:
1. A direct answer to the query
2. Key findings organized by topic
3. Source citations with URLs""",
            "description": "Synthesize research findings",
        },
    ],
    "guardrails": {
        "request_limit": 15,
        "tool_calls_limit": 30,
        "max_cost_dollars": 0.5,
        "timeout_seconds": 120,
    },
}
