"""
Tavily web tools beyond plain search: Extract, Map and Research.

- ``web_extract``   — clean markdown for one or more known URLs (POST /extract).
- ``web_map``       — discover the URLs of a site before reading it (POST /map).
- ``deep_research`` — a cited, multi-search research report (POST /research,
                      then poll GET /research/{id}).

All three need a Tavily API key. It is resolved the same way ``web_search``
resolves its providers (user → agent → system tool_configs → settings), so
the key entered in the admin UI is used. Without a key ``web_extract`` falls
back to the built-in stealth scraper and the other two return a clear error.

API reference: https://docs.tavily.com/documentation/api-reference
"""
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Tool

from app.config.settings import get_settings
from app.tools.web_search_tool import (
    TAVILY_API_BASE,
    _tavily_headers,
    get_provider_config_for_context,
)

logger = logging.getLogger(__name__)

EXTRACT_MAX_URLS = 20
EXTRACT_MAX_CHARS_PER_URL = 12000
RESEARCH_POLL_SECONDS = 3.0
RESEARCH_POLL_MAX_SECONDS = 10.0
RESEARCH_MODELS = {"mini", "pro", "auto"}
RESEARCH_LENGTHS = {"short", "standard", "long"}


async def _tavily_api_key() -> str:
    """Tavily key from tool_configs/settings, or "" when not configured."""
    try:
        config = await get_provider_config_for_context()
    except Exception as exc:  # noqa: BLE001 — config lookup must not break a tool
        logger.warning("Tavily config lookup failed: %s", exc)
        config = {}
    tavily = config.get("tavily", {}) if isinstance(config, dict) else {}
    if tavily.get("enabled") and tavily.get("api_key"):
        return str(tavily["api_key"])
    return ""


def _error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
        if isinstance(detail, dict):
            return str(detail.get("error") or detail)
        if detail:
            return str(detail)
    except Exception:  # noqa: BLE001
        pass
    return response.text[:200]


# ---------------------------------------------------------------------------
# web_extract
# ---------------------------------------------------------------------------


class ExtractedPage(BaseModel):
    url: str = Field(description="URL that was extracted")
    content: str = Field(description="Clean page content (markdown)")
    truncated: bool = Field(default=False, description="Whether content was cut to the per-URL limit")


class WebExtractOutput(BaseModel):
    """Output schema for web_extract."""

    success: bool = Field(description="Whether at least one URL was extracted")
    pages: List[ExtractedPage] = Field(default_factory=list, description="Extracted pages")
    failed: List[Dict[str, str]] = Field(default_factory=list, description="URLs that failed, with the error")
    provider: str = Field(default="tavily", description="tavily or web_scraper (fallback)")
    error: Optional[str] = Field(default=None, description="Error message if nothing could be extracted")


async def web_extract(
    urls: List[str],
    query: Optional[str] = None,
    extract_depth: str = "basic",
    max_chars_per_url: int = EXTRACT_MAX_CHARS_PER_URL,
) -> WebExtractOutput:
    """Read the full content of one or more web pages as clean markdown.

    Args:
        urls: One to twenty page URLs, typically taken from web_search results.
        query: Optional focus; when set Tavily returns only the chunks most
            relevant to it instead of the whole page.
        extract_depth: "basic" (default) or "advanced" (tables and embedded
            content, slower, 2x credits).
        max_chars_per_url: Cap on returned content per page.
    """
    urls = [u.strip() for u in (urls or []) if isinstance(u, str) and u.strip()]
    if not urls:
        return WebExtractOutput(success=False, error="No URLs provided.")
    if len(urls) > EXTRACT_MAX_URLS:
        urls = urls[:EXTRACT_MAX_URLS]

    api_key = await _tavily_api_key()
    if not api_key:
        return await _extract_with_scraper(urls[:3], max_chars_per_url)

    payload: Dict[str, Any] = {
        "urls": urls,
        "extract_depth": extract_depth if extract_depth in {"basic", "advanced"} else "basic",
        "format": "markdown",
    }
    if query:
        payload["query"] = query
        payload["chunks_per_source"] = 3

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{TAVILY_API_BASE}/extract", json=payload, headers=_tavily_headers(api_key)
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        detail = _error_detail(exc.response)
        logger.warning("Tavily extract HTTP %s: %s", exc.response.status_code, detail)
        return WebExtractOutput(success=False, error=f"Tavily extract failed ({exc.response.status_code}): {detail}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tavily extract error: %s", exc)
        return WebExtractOutput(success=False, error=f"Tavily extract failed: {exc}")

    pages: List[ExtractedPage] = []
    for item in data.get("results", []):
        content = str(item.get("raw_content") or "")
        truncated = len(content) > max_chars_per_url
        pages.append(ExtractedPage(
            url=str(item.get("url", "")),
            content=content[:max_chars_per_url],
            truncated=truncated,
        ))
    failed = [
        {"url": str(f.get("url", "")), "error": str(f.get("error", ""))}
        for f in data.get("failed_results", [])
    ]
    return WebExtractOutput(
        success=bool(pages),
        pages=pages,
        failed=failed,
        provider="tavily",
        error=None if pages else "No content could be extracted from the given URLs.",
    )


async def _extract_with_scraper(urls: List[str], max_chars: int) -> WebExtractOutput:
    """Fallback when Tavily is not configured: the built-in stealth scraper."""
    from app.tools.web_scraper_tool import scrape_webpage

    pages: List[ExtractedPage] = []
    failed: List[Dict[str, str]] = []
    for url in urls:
        try:
            result = await scrape_webpage(url=url, max_content_length=max_chars)
            if getattr(result, "success", False) and getattr(result, "content", ""):
                pages.append(ExtractedPage(url=result.url or url, content=result.content[:max_chars]))
            else:
                failed.append({"url": url, "error": getattr(result, "error", "") or "scrape failed"})
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": url, "error": str(exc)})
    return WebExtractOutput(
        success=bool(pages),
        pages=pages,
        failed=failed,
        provider="web_scraper",
        error=None if pages else "Tavily is not configured and the fallback scraper returned nothing.",
    )


# ---------------------------------------------------------------------------
# web_map
# ---------------------------------------------------------------------------


class WebMapOutput(BaseModel):
    """Output schema for web_map."""

    success: bool = Field(description="Whether the site was mapped")
    base_url: str = Field(default="", description="Root URL that was mapped")
    urls: List[str] = Field(default_factory=list, description="Discovered URLs")
    url_count: int = Field(default=0, description="Number of URLs discovered")
    error: Optional[str] = Field(default=None, description="Error message if mapping failed")


async def web_map(
    url: str,
    instructions: Optional[str] = None,
    max_depth: int = 1,
    limit: int = 50,
    select_paths: Optional[List[str]] = None,
) -> WebMapOutput:
    """List the pages of a website so the right ones can be read with web_extract.

    Args:
        url: Root URL of the site (e.g. "https://www.nae.usace.army.mil").
        instructions: Optional natural-language focus (e.g. "pages about
            dredging solicitations"); costs extra credits.
        max_depth: Link hops from the root, 1–5 (default 1).
        limit: Maximum URLs to return (default 50).
        select_paths: Optional regex path filters (e.g. ["/Missions/.*"]).
    """
    if not url or not isinstance(url, str):
        return WebMapOutput(success=False, error="A root URL is required.")
    api_key = await _tavily_api_key()
    if not api_key:
        return WebMapOutput(success=False, error="Site mapping requires Tavily; no Tavily API key is configured.")

    payload: Dict[str, Any] = {
        "url": url.strip(),
        "max_depth": max(1, min(int(max_depth), 5)),
        "limit": max(1, min(int(limit), 500)),
    }
    if instructions:
        payload["instructions"] = instructions
    if select_paths:
        payload["select_paths"] = [p for p in select_paths if p]

    try:
        async with httpx.AsyncClient(timeout=150.0) as client:
            response = await client.post(
                f"{TAVILY_API_BASE}/map", json=payload, headers=_tavily_headers(api_key)
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        detail = _error_detail(exc.response)
        logger.warning("Tavily map HTTP %s: %s", exc.response.status_code, detail)
        return WebMapOutput(success=False, error=f"Tavily map failed ({exc.response.status_code}): {detail}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tavily map error: %s", exc)
        return WebMapOutput(success=False, error=f"Tavily map failed: {exc}")

    urls = [str(u) for u in data.get("results", []) if u]
    return WebMapOutput(
        success=bool(urls),
        base_url=str(data.get("base_url") or url),
        urls=urls,
        url_count=len(urls),
        error=None if urls else "No URLs were discovered.",
    )


# ---------------------------------------------------------------------------
# deep_research
# ---------------------------------------------------------------------------


class ResearchSource(BaseModel):
    title: str = Field(default="", description="Source title")
    url: str = Field(default="", description="Source URL")


class DeepResearchOutput(BaseModel):
    """Output schema for deep_research."""

    success: bool = Field(description="Whether a completed report was returned")
    status: str = Field(default="unknown", description="completed, failed, timeout or error")
    report: str = Field(default="", description="Research report in markdown with numbered citations")
    sources: List[ResearchSource] = Field(default_factory=list, description="Sources cited in the report")
    request_id: Optional[str] = Field(default=None, description="Tavily research task id")
    model: str = Field(default="auto", description="Research agent model used")
    elapsed_seconds: float = Field(default=0.0, description="Wall time spent")
    error: Optional[str] = Field(default=None, description="Error message if the research did not complete")


async def deep_research(
    question: str,
    model: Optional[str] = None,
    output_length: str = "standard",
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
) -> DeepResearchOutput:
    """Run a multi-source web research task and return a cited report.

    Use for requests that need a comprehensive, decision-ready answer
    ("write a report on…", "compare…", "what is the market for…"), not for
    single facts — those are cheaper and faster with web_search. Typical
    runtime is one to four minutes.

    Args:
        question: The research task, with the context and output format wanted.
        model: "mini" for narrow questions, "pro" for multi-topic research,
            "auto" (default) to let Tavily choose.
        output_length: "short", "standard" (default) or "long".
        include_domains: Preferred source domains (soft preference, max 20).
        exclude_domains: Domains to block (max 20).
    """
    question = (question or "").strip()
    if not question:
        return DeepResearchOutput(success=False, status="error", error="A research question is required.")

    api_key = await _tavily_api_key()
    if not api_key:
        return DeepResearchOutput(
            success=False, status="error",
            error="Deep research requires Tavily; no Tavily API key is configured. Use web_search instead.",
        )

    settings = get_settings()
    chosen_model = model if model in RESEARCH_MODELS else settings.tavily_research_default_model
    if chosen_model not in RESEARCH_MODELS:
        chosen_model = "auto"
    payload: Dict[str, Any] = {
        "input": question,
        "model": chosen_model,
        "output_length": output_length if output_length in RESEARCH_LENGTHS else "standard",
        "citation_format": "numbered",
    }
    if include_domains:
        payload["include_domains"] = [d for d in include_domains if d][:20]
    if exclude_domains:
        payload["exclude_domains"] = [d for d in exclude_domains if d][:20]

    started = time.monotonic()
    headers = _tavily_headers(api_key)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{TAVILY_API_BASE}/research", json=payload, headers=headers)
            response.raise_for_status()
            created = response.json()
            request_id = str(created.get("request_id") or "")
            if not request_id:
                return DeepResearchOutput(success=False, status="error", model=chosen_model,
                                          error="Tavily did not return a research request id.")

            deadline = started + max(30, int(settings.tavily_research_timeout_seconds))
            delay = RESEARCH_POLL_SECONDS
            while True:
                await asyncio.sleep(delay)
                poll = await client.get(f"{TAVILY_API_BASE}/research/{request_id}", headers=headers)
                if poll.status_code == 202:
                    status = "in_progress"
                else:
                    poll.raise_for_status()
                    body = poll.json()
                    status = str(body.get("status") or "")
                    if status == "completed":
                        content = body.get("content")
                        if not isinstance(content, str):
                            content = json.dumps(content, indent=2)
                        sources = [
                            ResearchSource(title=str(s.get("title", "")), url=str(s.get("url", "")))
                            for s in body.get("sources", []) if isinstance(s, dict)
                        ]
                        return DeepResearchOutput(
                            success=True, status="completed", report=content, sources=sources,
                            request_id=request_id, model=chosen_model,
                            elapsed_seconds=round(time.monotonic() - started, 1),
                        )
                    if status == "failed":
                        return DeepResearchOutput(
                            success=False, status="failed", request_id=request_id, model=chosen_model,
                            elapsed_seconds=round(time.monotonic() - started, 1),
                            error="Tavily reported the research task as failed.",
                        )
                if time.monotonic() >= deadline:
                    return DeepResearchOutput(
                        success=False, status="timeout", request_id=request_id, model=chosen_model,
                        elapsed_seconds=round(time.monotonic() - started, 1),
                        error=(
                            f"Research is still running after {int(settings.tavily_research_timeout_seconds)}s "
                            f"(task {request_id}). Answer from web_search results instead."
                        ),
                    )
                delay = min(delay * 1.5, RESEARCH_POLL_MAX_SECONDS)
    except httpx.HTTPStatusError as exc:
        detail = _error_detail(exc.response)
        logger.warning("Tavily research HTTP %s: %s", exc.response.status_code, detail)
        return DeepResearchOutput(success=False, status="error", model=chosen_model,
                                  elapsed_seconds=round(time.monotonic() - started, 1),
                                  error=f"Tavily research failed ({exc.response.status_code}): {detail}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tavily research error: %s", exc)
        return DeepResearchOutput(success=False, status="error", model=chosen_model,
                                  elapsed_seconds=round(time.monotonic() - started, 1),
                                  error=f"Tavily research failed: {exc}")


# ---------------------------------------------------------------------------
# PydanticAI tool objects
# ---------------------------------------------------------------------------

web_extract_tool = Tool(
    web_extract,
    takes_ctx=False,
    name="web_extract",
    description=(
        "Read the full content of specific web pages as clean markdown. Use after "
        "web_search when a result looks relevant and the snippet is not enough, or "
        "when the user gives a URL. Pass a query to get only the relevant parts."
    ),
)

web_map_tool = Tool(
    web_map,
    takes_ctx=False,
    name="web_map",
    description=(
        "Discover the pages of a website (a site map) so the right ones can be read "
        "with web_extract. Use when the user asks about a specific site's content or "
        "structure, e.g. 'what solicitations are listed on the district's site'."
    ),
)

deep_research_tool = Tool(
    deep_research,
    takes_ctx=False,
    name="deep_research",
    description=(
        "Run comprehensive multi-source web research and return a cited report. Use "
        "only when the user asks for a report, deep dive, comparison or market/company "
        "analysis that needs many sources; takes one to four minutes and costs more "
        "than web_search. For single facts or news use web_search."
    ),
)
