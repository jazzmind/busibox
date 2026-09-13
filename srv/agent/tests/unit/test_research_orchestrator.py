"""Deep research as lead + parallel workers.

One Tavily /research call became: decompose → fan out (Tavily breadth worker
plus N isolated search/extract/map loops) → lead writes the report with
render_chart. These tests pin the orchestration contract without any model or
network: workers and the breadth call are stubbed at the seam the orchestrator
calls them through.
"""

import asyncio
import json

import pytest

from app.agents.base_agent import AgentContext
from app.agents.chat_agent import ChatAgent, FastAckDecision
from app.config.settings import get_settings
from app.services import research_orchestrator as ro
from app.services.research_orchestrator import (
    LEAD_TOOLS,
    WORKER_TOOLS,
    ResearchBundle,
    ResearchLeadAgent,
    ResearchOrchestrator,
    ResearchWorkerAgent,
    SubQuestion,
    WorkerFinding,
    _child_context,
    _sources_from_calls,
    resolve_worker_purpose,
)
from app.tools.tavily_tools import DeepResearchOutput, ResearchSource
from app.tools.web_search_tool import WebSearchOutput, WebSearchResult


class _Collector:
    def __init__(self):
        self.events = []

    async def __call__(self, ev):
        self.events.append(ev)

    def phases(self):
        return [getattr(e, "data", None) and e.data.get("phase") for e in self.events]


def _parent() -> AgentContext:
    return AgentContext(user_id="u1", conversation_history=[{"role": "user", "content": "old"}],
                        recent_messages=[{"role": "user", "content": "old"}])


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_child_context_shares_identity_and_nothing_else():
    parent = _parent()
    parent.record_tool_call("web_search", {}, "parent result", 1)
    child = _child_context(parent, loop_mode="research", budget_s=60)

    assert child.user_id == parent.user_id
    assert child.deps is parent.deps and child.principal is parent.principal
    assert child.conversation_history == [] and child.recent_messages == []
    assert child.tool_calls == [] and child.tool_results == {}
    assert child.loop_mode == "research" and child.loop_deadline > 0
    assert child.insights_enabled is False


def test_worker_and_lead_have_the_right_tools_and_models():
    worker = ResearchWorkerAgent("1", "agent")
    lead = ResearchLeadAgent()
    assert worker.config.tools == WORKER_TOOLS == ["web_search", "web_extract", "web_map"]
    assert "deep_research" not in worker.config.tools, "workers never nest the breadth pass"
    assert lead.config.tools == LEAD_TOOLS == ["render_chart"]
    assert lead.config.model == "chat"
    assert lead.config.max_tokens == 32000, "same Anthropic max_tokens reason as ChatAgent"


def test_worker_purpose_falls_back_until_the_alias_exists(monkeypatch):
    monkeypatch.setattr(ro.model_capabilities, "get", lambda alias: None)
    assert resolve_worker_purpose() == get_settings().research_worker_fallback_purpose


def test_worker_purpose_used_when_known(monkeypatch):
    monkeypatch.setattr(ro.model_capabilities, "get", lambda alias: object() if alias == "research_worker" else None)
    assert resolve_worker_purpose() == "research_worker"


# ---------------------------------------------------------------------------
# Decomposition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decompose_parses_structured_output(monkeypatch):
    async def fake_structured(**kw):
        return json.dumps({"sub_questions": [
            {"question": "What is the fleet size?", "angle": "supply"},
            {"question": "What is 2027 demand?", "angle": "demand", "prefer_recent": True},
            {"question": "Who regulates it?", "domains": ["usace.army.mil"]},
        ]})

    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", fake_structured)
    subs = await ResearchOrchestrator(agent).decompose("the dredging market in 2027")
    assert [s.angle for s in subs][:2] == ["supply", "demand"]
    assert subs[1].prefer_recent is True
    assert subs[2].domains == ["usace.army.mil"]


@pytest.mark.asyncio
async def test_decompose_failure_still_yields_one_worker(monkeypatch):
    async def broken(**kw):
        raise RuntimeError("planner down")

    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", broken)
    subs = await ResearchOrchestrator(agent).decompose("narrow question")
    assert len(subs) == 1 and subs[0].question == "narrow question"


@pytest.mark.asyncio
async def test_decompose_is_capped_by_max_workers(monkeypatch):
    async def many(**kw):
        return json.dumps({"sub_questions": [{"question": f"q{i}"} for i in range(12)]})

    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", many)
    monkeypatch.setattr(get_settings(), "research_max_workers", 3)
    assert len(await ResearchOrchestrator(agent).decompose("q")) == 3


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def test_sources_are_harvested_from_worker_tool_calls():
    ctx = AgentContext()
    ctx.record_tool_call("web_search", {}, WebSearchOutput(
        found=True, query="q", results=[
            WebSearchResult(title="A", url="https://a.example", snippet="", source="tavily"),
            WebSearchResult(title="B", url="https://b.example", snippet="", source="tavily"),
        ], result_count=2,
    ), 1)
    ctx.record_tool_call("web_search", {}, WebSearchOutput(
        found=True, query="q2", results=[
            WebSearchResult(title="A again", url="https://a.example", snippet="", source="tavily"),
        ], result_count=1,
    ), 1)
    ctx.record_tool_call("web_extract", {}, None, 1, ok=False, error="timeout")

    srcs = _sources_from_calls(ctx.tool_calls)
    assert [s["url"] for s in srcs] == ["https://a.example", "https://b.example"], "deduped, ordered"


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


def test_bundle_is_a_research_report_with_failures_marked():
    findings = [
        WorkerFinding("tavily", "Q", "breadth text", sources=[{"title": "T", "url": "https://t"}]),
        WorkerFinding("1", "sub one", "worker one text", sources=[{"title": "T", "url": "https://t"},
                                                                  {"title": "U", "url": "https://u"}]),
        WorkerFinding("2", "sub two", "", ok=False, error="timed out"),
    ]
    b = ResearchOrchestrator.bundle("Q", findings, 1234)
    assert isinstance(b, ResearchBundle)
    assert b.worker_count == 3 and b.failed_workers == 1
    assert [s["url"] for s in b.sources] == ["https://t", "https://u"]
    assert "Breadth report (Tavily research)" in b.report
    assert "worker one text" in b.report
    assert "did not return findings (timed out)" in b.report

    from app.agents.base_agent import _has_research_report
    assert _has_research_report({"deep_research": b}), "the lead/synthesis treats it as research"


# ---------------------------------------------------------------------------
# End to end, with the seams stubbed
# ---------------------------------------------------------------------------


@pytest.fixture
def stubbed(monkeypatch):
    """Stub the breadth call, worker loops and the lead loop."""
    calls = {"workers": [], "lead": []}

    async def fake_deep_research(question, **kw):
        return DeepResearchOutput(success=True, status="completed", report="BREADTH",
                                  sources=[ResearchSource(title="B", url="https://breadth")])

    async def fake_structured(**kw):
        return json.dumps({"sub_questions": [{"question": "sub A", "angle": "a"},
                                             {"question": "sub B", "angle": "b"}]})

    async def fake_worker_loop(self, query, stream, cancel, ctx):
        calls["workers"].append((self.config.name, query))
        ctx.record_tool_call("web_search", {"query": query}, WebSearchOutput(
            found=True, query=query, results=[WebSearchResult(
                title="W", url=f"https://{self.config.name}", snippet="", source="tavily")],
            result_count=1), 5, source="loop")
        ctx.tool_results["llm_response"] = f"FINDINGS for {query.splitlines()[0]}"
        await stream(ro.content(source="x", message="worker text that must not reach the user"))

    async def fake_lead_loop(self, query, stream, cancel, ctx):
        calls["lead"].append(query)
        ctx.record_tool_call("render_chart", {"kind": "bar"}, "![c](/portal/api/media/f1)", 3, source="loop")
        ctx.tool_results["llm_response"] = "# REPORT\n\n![c](/portal/api/media/f1)"
        await stream(ro.content(source="lead", message="# REPORT"))

    import app.tools.tavily_tools as tt
    monkeypatch.setattr(tt, "deep_research", fake_deep_research)
    monkeypatch.setattr(ResearchWorkerAgent, "_execute_llm_driven", fake_worker_loop)
    monkeypatch.setattr(ResearchLeadAgent, "_execute_llm_driven", fake_lead_loop)
    monkeypatch.setattr(ro.model_capabilities, "get", lambda alias: None)
    return calls, fake_structured


@pytest.mark.asyncio
async def test_full_pass_fans_out_and_writes_the_report(stubbed, monkeypatch):
    calls, fake_structured = stubbed
    parent_agent = ChatAgent()
    monkeypatch.setattr(parent_agent, "_call_structured_output", fake_structured)
    parent = _parent()
    out = _Collector()

    text = await ResearchOrchestrator(parent_agent).run("the question", parent, out, asyncio.Event())

    # Two workers ran, each on its own sub-question, in isolation.
    assert sorted(q.splitlines()[0] for _, q in calls["workers"]) == ["sub A", "sub B"]
    # The lead ran once and was given every worker's findings plus the breadth report.
    assert len(calls["lead"]) == 1
    lead_prompt = calls["lead"][0]
    assert "FINDINGS for sub A" in lead_prompt and "FINDINGS for sub B" in lead_prompt
    assert "BREADTH" in lead_prompt
    assert "https://breadth" in lead_prompt
    # The report is the answer.
    assert text.startswith("# REPORT")
    assert parent.tool_results["llm_response"] == text
    assert isinstance(parent.tool_results["deep_research"], ResearchBundle)


@pytest.mark.asyncio
async def test_worker_text_never_streams_to_the_user_but_lead_text_does(stubbed, monkeypatch):
    calls, fake_structured = stubbed
    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", fake_structured)
    out = _Collector()

    await ResearchOrchestrator(agent).run("q", _parent(), out, asyncio.Event())

    contents = [e.message for e in out.events if e.type == "content"]
    assert "# REPORT" in contents
    assert not any("must not reach the user" in m for m in contents)
    assert {"decompose", "fan_out", "worker_start", "worker_done", "synthesize"} <= set(out.phases())


@pytest.mark.asyncio
async def test_workers_tool_calls_and_lead_chart_calls_land_on_the_parent(stubbed, monkeypatch):
    calls, fake_structured = stubbed
    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", fake_structured)
    parent = _parent()

    await ResearchOrchestrator(agent).run("q", parent, _Collector(), asyncio.Event())

    tools = [c.tool for c in parent.tool_calls]
    assert tools.count("web_search") == 2, "one per worker"
    assert "deep_research" in tools, "the breadth worker is recorded too"
    assert "render_chart" in tools, "the lead's chart call is visible on the turn"


@pytest.mark.asyncio
async def test_one_failed_worker_does_not_sink_the_pass(stubbed, monkeypatch):
    calls, fake_structured = stubbed
    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", fake_structured)

    async def flaky(self, query, stream, cancel, ctx):
        if "sub B" in query:
            raise RuntimeError("worker B exploded")
        ctx.tool_results["llm_response"] = "FINDINGS A"

    monkeypatch.setattr(ResearchWorkerAgent, "_execute_llm_driven", flaky)
    parent = _parent()

    text = await ResearchOrchestrator(agent).run("q", parent, _Collector(), asyncio.Event())

    assert text.startswith("# REPORT")
    bundle = parent.tool_results["deep_research"]
    assert bundle.failed_workers == 1
    assert "worker B exploded" in bundle.report


@pytest.mark.asyncio
async def test_all_workers_failing_gives_an_honest_message_not_a_fabricated_report(stubbed, monkeypatch):
    calls, fake_structured = stubbed
    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", fake_structured)

    async def dead(self, query, stream, cancel, ctx):
        raise RuntimeError("no network")

    async def dead_breadth(question, **kw):
        return DeepResearchOutput(success=False, status="timeout", error="still running after 420s")

    import app.tools.tavily_tools as tt
    monkeypatch.setattr(tt, "deep_research", dead_breadth)
    monkeypatch.setattr(ResearchWorkerAgent, "_execute_llm_driven", dead)

    text = await ResearchOrchestrator(agent).run("q", _parent(), _Collector(), asyncio.Event())

    assert "wasn't able to gather research" in text
    assert calls["lead"] == [], "the lead is not asked to write from nothing"


@pytest.mark.asyncio
async def test_lead_failure_falls_back_to_synthesis_on_the_bundle(stubbed, monkeypatch):
    calls, fake_structured = stubbed
    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", fake_structured)

    async def silent_lead(self, query, stream, cancel, ctx):
        return  # no llm_response

    monkeypatch.setattr(ResearchLeadAgent, "_execute_llm_driven", silent_lead)
    parent = _parent()

    text = await ResearchOrchestrator(agent).run("q", parent, _Collector(), asyncio.Event())

    assert text == ""
    assert "llm_response" not in parent.tool_results, "chat agent must fall through to _synthesize"
    assert isinstance(parent.tool_results["deep_research"], ResearchBundle)


# ---------------------------------------------------------------------------
# Routing from the chat agent
# ---------------------------------------------------------------------------


def _d(**kw):
    base = dict(action_type="research", needs_tools=True, complexity="complex", response="",
                preferred_tool="deep_research")
    base.update(kw)
    return FastAckDecision(**base)


def test_consented_research_turn_routes_to_the_orchestrator():
    agent = ChatAgent()
    assert agent._use_research_orchestrator(_d()) is True
    assert agent._loop_first_tier(_d(), AgentContext()) is None, "and not also to the chat loop"


def test_the_offer_turn_does_not_route_to_the_orchestrator():
    """_confirm_deep_research sets needs_tools=False for the Yes/No offer."""
    agent = ChatAgent()
    assert agent._use_research_orchestrator(_d(needs_tools=False)) is False


def test_orchestrator_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "research_orchestrator_enabled", False)
    agent = ChatAgent()
    assert agent._use_research_orchestrator(_d()) is False


def test_no_tavily_key_path_degrades_to_the_chat_loop():
    """_confirm_deep_research clears preferred_tool when Tavily is missing;
    the turn is still action_type=research, so it runs loop-first with
    web_search rather than a static plan."""
    agent = ChatAgent()
    d = _d(preferred_tool=None)
    assert agent._use_research_orchestrator(d) is False
    assert agent._loop_first_tier(d, AgentContext()) == "research"


# ---------------------------------------------------------------------------
# Review-driven: directives per role, bundle budget, fallback hygiene
# ---------------------------------------------------------------------------


def test_worker_is_told_how_to_search_but_not_to_write_a_long_report():
    """Workers return compact findings to the lead. Giving them the report
    directive ("go long", "use ## sections", "call render_chart") contradicts
    their own instructions and names a tool they do not have."""
    from app.agents.base_agent import (
        LOOP_MODE_DIRECTIVE, RESEARCH_CHART_DIRECTIVE, RESEARCH_LOOP_DIRECTIVE, RESEARCH_SYNTHESIS_DIRECTIVE,
    )
    worker = ResearchWorkerAgent("1", "agent")
    text = worker._build_enriched_system_prompt(AgentContext(loop_mode="research_worker"))
    assert LOOP_MODE_DIRECTIVE in text
    assert RESEARCH_LOOP_DIRECTIVE in text
    assert RESEARCH_SYNTHESIS_DIRECTIVE not in text
    assert RESEARCH_CHART_DIRECTIVE not in text


def test_lead_is_told_to_write_and_chart_but_not_to_search():
    """The lead 'has not seen the web itself' and has render_chart alone;
    telling it to run 2–4 searches would contradict that."""
    from app.agents.base_agent import RESEARCH_CHART_DIRECTIVE, RESEARCH_LOOP_DIRECTIVE, RESEARCH_SYNTHESIS_DIRECTIVE
    lead = ResearchLeadAgent()
    text = lead._build_enriched_system_prompt(AgentContext(loop_mode="research"))
    assert RESEARCH_SYNTHESIS_DIRECTIVE in text
    assert RESEARCH_CHART_DIRECTIVE in text
    assert RESEARCH_LOOP_DIRECTIVE not in text


def test_bundle_has_success_so_grounding_sees_retrieved_evidence():
    b = ResearchBundle(question="q", report="r")
    assert b.success is True


def test_bundle_render_keeps_whole_sections_under_its_own_budget(monkeypatch):
    """research_report_context_chars is sized for one Tavily report; the
    bundle is that plus every worker. It gets its own budget, and when that
    is hit whole trailing sections go, not a mid-sentence cut."""
    monkeypatch.setattr(get_settings(), "research_bundle_context_chars", 400)
    findings = [
        WorkerFinding("tavily", "Q", "B" * 150),
        WorkerFinding("1", "s1", "W1" * 100),
        WorkerFinding("2", "s2", "W2" * 100),
        WorkerFinding("3", "s3", "W3" * 100),
    ]
    b = ResearchOrchestrator.bundle("Q", findings, 0)
    text = ResearchOrchestrator(ChatAgent())._render_bundle(b)
    assert "section(s) omitted" in text
    assert "Say so in the report" in text
    # Whatever survived ends at a section boundary, not inside a worker's text.
    body = text.split("_[")[0]
    assert body.rstrip().endswith(("B" * 150, "W1" * 100, "W2" * 100)) or body.rstrip().endswith("\n")


def test_bundle_render_untouched_when_it_fits():
    findings = [WorkerFinding("tavily", "Q", "short breadth"), WorkerFinding("1", "s1", "short worker")]
    b = ResearchOrchestrator.bundle("Q", findings, 0)
    text = ResearchOrchestrator(ChatAgent())._render_bundle(b)
    assert "omitted" not in text and "short worker" in text


@pytest.mark.asyncio
async def test_lead_failure_leaves_synthesis_exactly_one_research_record(stubbed, monkeypatch):
    """Synthesis renders from tool_calls. After a lead failure the parent
    must hold the bundle once — not the raw Tavily output plus every
    worker's search results plus a bundle that already contains all of it."""
    calls, fake_structured = stubbed
    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", fake_structured)

    async def silent_lead(self, query, stream, cancel, ctx):
        return

    monkeypatch.setattr(ResearchLeadAgent, "_execute_llm_driven", silent_lead)
    parent = _parent()

    await ResearchOrchestrator(agent).run("q", parent, _Collector(), asyncio.Event())

    research_records = [c for c in parent.tool_calls if c.tool == "deep_research"]
    assert len(research_records) == 1 and isinstance(research_records[0].result, ResearchBundle)
    assert not any(c.tool == "web_search" for c in parent.tool_calls), "raw worker calls pruned"
    rendered = agent._build_synthesis_context("q", parent)
    assert rendered.count("cited research report") == 1


@pytest.mark.asyncio
async def test_a_raising_lead_does_not_lose_the_findings(stubbed, monkeypatch):
    calls, fake_structured = stubbed
    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", fake_structured)

    async def exploding_lead(self, query, stream, cancel, ctx):
        raise RuntimeError("Agent construction failed")

    monkeypatch.setattr(ResearchLeadAgent, "_execute_llm_driven", exploding_lead)
    parent = _parent()

    text = await ResearchOrchestrator(agent).run("q", parent, _Collector(), asyncio.Event())

    assert text == "", "falls through to synthesis instead of raising"
    assert isinstance(parent.tool_results["deep_research"], ResearchBundle)


@pytest.mark.asyncio
async def test_worker_records_are_tagged_with_their_worker(stubbed, monkeypatch):
    calls, fake_structured = stubbed
    agent = ChatAgent()
    monkeypatch.setattr(agent, "_call_structured_output", fake_structured)
    parent = _parent()

    await ResearchOrchestrator(agent).run("q", parent, _Collector(), asyncio.Event())

    sources = {c.source for c in parent.tool_calls if c.tool == "web_search"}
    assert sources == {"worker 1", "worker 2"}


def test_failed_workers_sources_are_not_offered_to_the_lead():
    findings = [
        WorkerFinding("1", "s1", "ok text", sources=[{"title": "G", "url": "https://good"}]),
        WorkerFinding("2", "s2", "", sources=[{"title": "B", "url": "https://from-failed"}], ok=False, error="x"),
    ]
    b = ResearchOrchestrator.bundle("Q", findings, 0)
    assert [s["url"] for s in b.sources] == ["https://good"]
