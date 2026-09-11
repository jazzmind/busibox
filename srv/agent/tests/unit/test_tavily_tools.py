"""Tavily search depth, extract, map and deep_research tools."""

import asyncio
import json
from typing import Any, Dict, List, Optional

import pytest

from app.agents.base_agent import TOOL_CLASSES, ToolRegistry
from app.agents.chat_agent import ChatAgent
from app.tools import tavily_tools
from app.tools import web_search_tool


# ---------------------------------------------------------------------------
# httpx stand-in
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int, body: Any = None, text: str = ""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text or json.dumps(self._body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            request = httpx.Request("POST", "https://api.tavily.com/x")
            raise httpx.HTTPStatusError("error", request=request, response=httpx.Response(self.status_code, request=request, json=self._body))


class _FakeClient:
    """Replaces httpx.AsyncClient; records calls and replays queued responses."""

    calls: List[Dict[str, Any]] = []
    queue: List[_Resp] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None, **kwargs):
        _FakeClient.calls.append({"method": "POST", "url": url, "json": json, "headers": headers})
        return _FakeClient.queue.pop(0)

    async def get(self, url, headers=None, **kwargs):
        _FakeClient.calls.append({"method": "GET", "url": url, "headers": headers})
        return _FakeClient.queue.pop(0)


@pytest.fixture
def fake_http(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.queue = []
    monkeypatch.setattr(web_search_tool.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(tavily_tools.httpx, "AsyncClient", _FakeClient)

    async def _key():
        return "tvly-test"

    monkeypatch.setattr(tavily_tools, "_tavily_api_key", _key)
    return _FakeClient


# ---------------------------------------------------------------------------
# search: advanced depth by default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_tavily_uses_advanced_depth_and_bearer_auth(fake_http):
    fake_http.queue.append(_Resp(200, {"results": [
        {"title": "USACE dredging", "url": "https://a.example/1", "content": "x" * 2000, "score": 0.9, "published_date": "2026-09-01"},
    ]}))

    results = await web_search_tool.search_tavily(
        "dredging news", 5, "tvly-test", topic="news", time_range="week", include_domains=["usace.army.mil"],
    )

    call = fake_http.calls[0]
    assert call["url"].endswith("/search")
    assert call["headers"]["Authorization"] == "Bearer tvly-test"
    assert "api_key" not in call["json"]
    assert call["json"]["search_depth"] == "advanced"
    assert call["json"]["chunks_per_source"] == 3
    assert call["json"]["topic"] == "news"
    assert call["json"]["time_range"] == "week"
    assert call["json"]["include_domains"] == ["usace.army.mil"]
    assert len(results) == 1
    assert results[0].snippet.startswith("[published 2026-09-01] ")
    assert len(results[0].snippet) <= web_search_tool.TAVILY_SNIPPET_CHARS + 30


@pytest.mark.asyncio
async def test_search_tavily_rejects_unknown_topic_and_range(fake_http):
    fake_http.queue.append(_Resp(200, {"results": []}))
    await web_search_tool.search_tavily("q", 3, "tvly-test", topic="sports", time_range="decade")
    payload = fake_http.calls[0]["json"]
    assert payload["topic"] == "general"
    assert "time_range" not in payload


@pytest.mark.asyncio
async def test_search_tavily_http_error_returns_empty(fake_http):
    fake_http.queue.append(_Resp(432, {"detail": {"error": "plan limit"}}))
    assert await web_search_tool.search_tavily("q", 3, "tvly-test") == []


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_extract_parses_results_and_failures(fake_http):
    fake_http.queue.append(_Resp(200, {
        "results": [{"url": "https://a.example/p", "raw_content": "# Page\n" + "body " * 5000}],
        "failed_results": [{"url": "https://b.example", "error": "timeout"}],
    }))

    out = await tavily_tools.web_extract(["https://a.example/p", "https://b.example"], query="dredge")

    payload = fake_http.calls[0]["json"]
    assert payload["urls"] == ["https://a.example/p", "https://b.example"]
    assert payload["query"] == "dredge"
    assert payload["format"] == "markdown"
    assert out.success is True
    assert out.provider == "tavily"
    assert out.pages[0].truncated is True
    assert len(out.pages[0].content) == tavily_tools.EXTRACT_MAX_CHARS_PER_URL
    assert out.failed == [{"url": "https://b.example", "error": "timeout"}]


@pytest.mark.asyncio
async def test_web_extract_requires_urls():
    out = await tavily_tools.web_extract([])
    assert out.success is False
    assert "No URLs" in out.error


@pytest.mark.asyncio
async def test_web_extract_falls_back_to_scraper_without_key(monkeypatch):
    async def _no_key():
        return ""

    class _Scraped:
        success = True
        url = "https://a.example"
        content = "scraped text"

    async def _scrape(url, max_content_length):
        return _Scraped()

    monkeypatch.setattr(tavily_tools, "_tavily_api_key", _no_key)
    import app.tools.web_scraper_tool as scraper
    monkeypatch.setattr(scraper, "scrape_webpage", _scrape)

    out = await tavily_tools.web_extract(["https://a.example"])
    assert out.success is True
    assert out.provider == "web_scraper"
    assert out.pages[0].content == "scraped text"


# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_map_returns_urls(fake_http):
    fake_http.queue.append(_Resp(200, {"base_url": "https://site.example", "results": ["https://site.example/a", "https://site.example/b"]}))
    out = await tavily_tools.web_map("https://site.example", instructions="solicitations", max_depth=9, limit=10)
    payload = fake_http.calls[0]["json"]
    assert payload["max_depth"] == 5  # clamped
    assert payload["instructions"] == "solicitations"
    assert out.success and out.url_count == 2


@pytest.mark.asyncio
async def test_web_map_without_key_explains(monkeypatch):
    async def _no_key():
        return ""
    monkeypatch.setattr(tavily_tools, "_tavily_api_key", _no_key)
    out = await tavily_tools.web_map("https://site.example")
    assert out.success is False
    assert "Tavily" in out.error


# ---------------------------------------------------------------------------
# deep research: create + poll
# ---------------------------------------------------------------------------


@pytest.fixture
def no_sleep(monkeypatch):
    async def _instant(_seconds):
        return None
    monkeypatch.setattr(tavily_tools.asyncio, "sleep", _instant)


@pytest.mark.asyncio
async def test_deep_research_polls_until_completed(fake_http, no_sleep):
    fake_http.queue.extend([
        _Resp(201, {"request_id": "req-1", "status": "pending"}),
        _Resp(202, {"request_id": "req-1", "status": "in_progress"}),
        _Resp(200, {"request_id": "req-1", "status": "completed", "content": "# Report\nFindings [1]",
                    "sources": [{"title": "Src", "url": "https://s.example", "favicon": ""}]}),
    ])

    out = await tavily_tools.deep_research("Market for hopper dredges in the US", model="pro", output_length="long")

    create = fake_http.calls[0]
    assert create["url"].endswith("/research")
    assert create["json"]["model"] == "pro"
    assert create["json"]["output_length"] == "long"
    assert create["json"]["citation_format"] == "numbered"
    assert fake_http.calls[1]["method"] == "GET" and fake_http.calls[1]["url"].endswith("/research/req-1")
    assert out.success is True
    assert out.status == "completed"
    assert out.report.startswith("# Report")
    assert out.sources[0].url == "https://s.example"
    assert out.request_id == "req-1"


@pytest.mark.asyncio
async def test_deep_research_structured_content_is_serialised(fake_http, no_sleep):
    fake_http.queue.extend([
        _Resp(201, {"request_id": "req-2"}),
        _Resp(200, {"status": "completed", "content": {"company": "Cashman"}, "sources": []}),
    ])
    out = await tavily_tools.deep_research("q")
    assert out.success and json.loads(out.report) == {"company": "Cashman"}


@pytest.mark.asyncio
async def test_deep_research_failed_status(fake_http, no_sleep):
    fake_http.queue.extend([
        _Resp(201, {"request_id": "req-3"}),
        _Resp(200, {"status": "failed"}),
    ])
    out = await tavily_tools.deep_research("q")
    assert out.success is False and out.status == "failed"


@pytest.mark.asyncio
async def test_deep_research_times_out(fake_http, no_sleep, monkeypatch):
    class _S:
        tavily_research_timeout_seconds = 30
        tavily_research_default_model = "auto"
    monkeypatch.setattr(tavily_tools, "get_settings", lambda: _S())
    clock = iter([0.0, 100.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(tavily_tools.time, "monotonic", lambda: next(clock))
    fake_http.queue.extend([
        _Resp(201, {"request_id": "req-4"}),
        _Resp(202, {"status": "in_progress"}),
    ])
    out = await tavily_tools.deep_research("q")
    assert out.status == "timeout"
    assert "req-4" in out.error


@pytest.mark.asyncio
async def test_deep_research_without_key_points_to_web_search(monkeypatch):
    async def _no_key():
        return ""
    monkeypatch.setattr(tavily_tools, "_tavily_api_key", _no_key)
    out = await tavily_tools.deep_research("q")
    assert out.success is False and "web_search" in out.error


# ---------------------------------------------------------------------------
# registration and planner arg backfill
# ---------------------------------------------------------------------------


def test_tools_are_registered_and_classified():
    for name in ("web_extract", "web_map", "deep_research"):
        assert ToolRegistry.has(name), name
        assert TOOL_CLASSES[name]["class"] == "slow"
    assert TOOL_CLASSES["deep_research"]["timeout"] >= 240
    assert {"web_extract", "web_map", "deep_research"} <= set(ChatAgent().config.tools)


def test_planner_backfills_tavily_args():
    agent = ChatAgent()
    q = "read https://www.nae.usace.army.mil/Missions/ and tell me about dredging"
    assert agent._normalize_planned_step_args("deep_research", {}, "market study")["question"] == "market study"
    assert agent._normalize_planned_step_args("web_extract", {}, q)["urls"] == ["https://www.nae.usace.army.mil/Missions/"]
    assert agent._normalize_planned_step_args("web_extract", {"urls": "https://x.example"}, q)["urls"] == ["https://x.example"]
    assert agent._normalize_planned_step_args("web_map", {}, q)["url"] == "https://www.nae.usace.army.mil/Missions/"
    assert agent._resolve_planned_tool("deep_research") == "deep_research"
    assert agent._resolve_planned_tool("research") is None  # never upgrade a plain search by alias
