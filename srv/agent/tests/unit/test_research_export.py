"""Research auto-export: the finished report becomes a Word document.

After the lead writes the report, the orchestrator builds a DocumentSpec
from it (H1 → title, H2s → sections, worker sources → Sources) and asks the
data-api to render it. The link is appended under the answer. Every failure
mode is non-fatal: the report on screen is never at risk.
"""

import asyncio
import json

import pytest

from app.agents.base_agent import AgentContext
from app.agents.chat_agent import ChatAgent
from app.config.settings import get_settings
from app.services import research_orchestrator as ro
from app.services.research_orchestrator import (
    ResearchBundle,
    ResearchLeadAgent,
    ResearchOrchestrator,
    ResearchWorkerAgent,
    _split_report,
    build_report_spec,
)
from app.tools.document_tools import DocumentFileOutput
from app.tools.tavily_tools import DeepResearchOutput, ResearchSource
from app.tools.web_search_tool import WebSearchOutput, WebSearchResult


class _Collector:
    def __init__(self):
        self.events = []

    async def __call__(self, ev):
        self.events.append(ev)


def _parent() -> AgentContext:
    return AgentContext(user_id="u1", conversation_history=[{"role": "user", "content": "old"}],
                        recent_messages=[{"role": "user", "content": "old"}])


@pytest.fixture
def stubbed(monkeypatch):
    """Same seams as test_research_orchestrator: breadth call, worker loops, lead loop."""
    calls = {"workers": [], "lead": []}

    async def fake_deep_research(question, **kw):
        return DeepResearchOutput(success=True, status="completed", report="BREADTH",
                                  sources=[ResearchSource(title="B", url="https://breadth")])

    async def fake_structured(**kw):
        return json.dumps({"sub_questions": [{"question": "sub A", "angle": "a"}]})

    async def fake_worker_loop(self, query, stream, cancel, ctx):
        calls["workers"].append((self.config.name, query))
        ctx.record_tool_call("web_search", {"query": query}, WebSearchOutput(
            found=True, query=query, results=[WebSearchResult(
                title="W", url="https://w", snippet="", source="tavily")],
            result_count=1), 5, source="loop")
        ctx.tool_results["llm_response"] = "FINDINGS"

    async def fake_lead_loop(self, query, stream, cancel, ctx):
        calls["lead"].append(query)
        ctx.tool_results["llm_response"] = "# REPORT\n\n![c](/portal/api/media/f1)"
        await stream(ro.content(source="lead", message="# REPORT"))

    import app.tools.tavily_tools as tt
    monkeypatch.setattr(tt, "deep_research", fake_deep_research)
    monkeypatch.setattr(ResearchWorkerAgent, "_execute_llm_driven", fake_worker_loop)
    monkeypatch.setattr(ResearchLeadAgent, "_execute_llm_driven", fake_lead_loop)
    monkeypatch.setattr(ro.model_capabilities, "get", lambda alias: None)
    return calls, fake_structured

CHART = "![Launches](/portal/api/media/11111111-2222-3333-4444-555555555555)"
REPORT = f"""# SpaceX Launch Economics

Intro paragraph.

## Key findings

Costs fell. {CHART}

| Year | Launches |
|---|---:|
| 2024 | 134 |

## Outlook

### Near term

Fine.

```mermaid
## not a heading
```

## Sources

1. [Press](https://spacex.com/press)
"""


def _bundle(**kw):
    base = dict(question="SpaceX launch economics", report="", sources=[
        {"title": "Press kit", "url": "https://spacex.com/press"},
        {"title": "FAA", "url": "https://faa.gov/x"},
        {"title": "FAA dup", "url": "https://faa.gov/x"},
    ])
    base.update(kw)
    return ResearchBundle(**base)


# ---------------------------------------------------------------------------
# Report → spec
# ---------------------------------------------------------------------------


def test_split_report_uses_h1_as_title_and_h2s_as_sections():
    title, sections = _split_report(REPORT)
    assert title == "SpaceX Launch Economics"
    assert [h for h, _ in sections] == [None, "Key findings", "Outlook", "Sources"]
    assert sections[0][1] == "Intro paragraph."
    assert CHART in sections[1][1] and "| 2024 | 134 |" in sections[1][1]
    assert "### Near term" in sections[2][1]
    assert "## not a heading" in sections[2][1]   # fenced code never splits


def test_unstructured_report_stays_one_section():
    title, sections = _split_report("# Only a title\n\nSome text\n\nmore text")
    assert title == "Only a title"
    assert sections == [(None, "Some text\n\nmore text")]
    title, sections = _split_report("plain answer with no headings")
    assert title is None and sections == [(None, "plain answer with no headings")]


def test_build_report_spec_carries_charts_and_dedups_sources():
    spec = build_report_spec("q", REPORT.replace("## Sources\n\n1. [Press](https://spacex.com/press)\n", ""), _bundle())
    assert spec.filename == "SpaceX Launch Economics.docx"
    assert spec.title == "SpaceX Launch Economics" and spec.subtitle == "Deep research report"
    assert spec.toc is True
    assert [s.heading for s in spec.sections] == [None, "Key findings", "Outlook"]
    assert CHART in spec.sections[1].markdown
    assert [s.url for s in spec.sources] == ["https://spacex.com/press", "https://faa.gov/x"]


def test_report_with_its_own_sources_section_gets_no_second_one():
    spec = build_report_spec("q", REPORT, _bundle())
    assert spec.sources == []
    assert spec.sections[-1].heading == "Sources"


def test_title_falls_back_to_the_question():
    spec = build_report_spec("what are the economics of reusable launch vehicles in 2026?", "no headings here", _bundle())
    assert spec.title == "What are the economics of reusable launch vehicles in 2026"
    assert spec.filename.endswith(".docx") and "?" not in spec.filename
    long_q = " ".join(f"w{i}" for i in range(30))
    assert build_report_spec(long_q, "x", _bundle()).title.endswith("…")


# ---------------------------------------------------------------------------
# Export step
# ---------------------------------------------------------------------------


def _ctx_with_deps():
    ctx = _parent()
    ctx.deps = object()  # anything non-None; export_document is stubbed
    return ctx


async def test_export_appends_link_and_records_the_call(monkeypatch):
    import app.tools.document_tools as dt

    seen = {}

    async def fake_export(deps, spec, thumbnail=True):
        seen["spec"] = spec
        return DocumentFileOutput(success=True, file_id="d1", filename=spec.filename, pages=4,
                                  download_url="/portal/api/media/d1?download=1", thumbnail_url="/portal/api/media/t1",
                                  markdown="[Download the document: r.docx](/portal/api/media/d1?download=1)\n\n![First page of r.docx](/portal/api/media/t1)",
                                  summary="r.docx: 3 section(s), 1 table(s), 1 figure(s), ~40 words, 4 page(s).")

    monkeypatch.setattr(dt, "export_document", fake_export)
    ctx = _ctx_with_deps()
    out = _Collector()
    md = await ResearchOrchestrator(ChatAgent())._export_report("q", REPORT, _bundle(), ctx, out)

    assert md.startswith("[Download the document")
    assert seen["spec"].title == "SpaceX Launch Economics"
    calls = [c for c in ctx.tool_calls if c.tool == "create_document"]
    assert len(calls) == 1 and calls[0].source == "lead"
    phases = [e.data.get("phase") for e in out.events if e.data]
    assert phases.count("export") == 2  # started + done
    assert any(e.data.get("ok") is True and e.data.get("pages") == 4 for e in out.events if e.data)


async def test_export_failure_is_a_thought_not_an_error(monkeypatch):
    import app.tools.document_tools as dt

    async def failing(deps, spec, thumbnail=True):
        return DocumentFileOutput(success=False, error="pandoc is not installed on this server", issues=["error at document: x"])

    monkeypatch.setattr(dt, "export_document", failing)
    out = _Collector()
    md = await ResearchOrchestrator(ChatAgent())._export_report("q", REPORT, _bundle(), _ctx_with_deps(), out)
    assert md == ""
    assert not any(e.type == "error" for e in out.events)
    assert any("Word export skipped" in e.message for e in out.events)


async def test_export_exception_never_propagates(monkeypatch):
    import app.tools.document_tools as dt

    async def boom(deps, spec, thumbnail=True):
        raise RuntimeError("network down")

    monkeypatch.setattr(dt, "export_document", boom)
    md = await ResearchOrchestrator(ChatAgent())._export_report("q", REPORT, _bundle(), _ctx_with_deps(), _Collector())
    assert md == ""


async def test_export_respects_the_setting_and_needs_deps(monkeypatch):
    import app.tools.document_tools as dt

    called = []

    async def spy(deps, spec, thumbnail=True):
        called.append(1)
        return DocumentFileOutput(success=True, markdown="[x](y)")

    monkeypatch.setattr(dt, "export_document", spy)
    monkeypatch.setattr(get_settings(), "research_export_docx", False)
    assert await ResearchOrchestrator(ChatAgent())._export_report("q", REPORT, _bundle(), _ctx_with_deps(), _Collector()) == ""
    monkeypatch.setattr(get_settings(), "research_export_docx", True)
    assert await ResearchOrchestrator(ChatAgent())._export_report("q", REPORT, _bundle(), _parent(), _Collector()) == ""
    assert called == []


# ---------------------------------------------------------------------------
# Whole pass
# ---------------------------------------------------------------------------


async def test_full_pass_streams_the_link_after_the_report(stubbed, monkeypatch):
    import app.tools.document_tools as dt

    calls, fake_structured = stubbed
    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", fake_structured)

    async def fake_export(deps, spec, thumbnail=True):
        return DocumentFileOutput(success=True, markdown="[Download the document: REPORT.docx](/portal/api/media/d1?download=1)", summary="ok")

    monkeypatch.setattr(dt, "export_document", fake_export)
    ctx = _ctx_with_deps()
    out = _Collector()

    text = await ResearchOrchestrator(agent).run("the question", ctx, out, asyncio.Event())

    assert text.startswith("# REPORT")
    assert text.endswith("---\n\n[Download the document: REPORT.docx](/portal/api/media/d1?download=1)")
    assert ctx.tool_results["llm_response"] == text
    contents = [e.message for e in out.events if e.type == "content"]
    assert contents[-1].strip().startswith("---") and "Download the document" in contents[-1]
    assert contents.index("# REPORT") < len(contents) - 1


async def test_full_pass_without_deps_is_unchanged(stubbed, monkeypatch):
    calls, fake_structured = stubbed
    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", fake_structured)
    text = await ResearchOrchestrator(agent).run("q", _parent(), _Collector(), asyncio.Event())
    assert "Download" not in text
