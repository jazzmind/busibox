"""Deep-research intent: router route, regex fallback, confirmation ack, planner."""

import asyncio
from pathlib import Path

import pytest

from app.agents.base_agent import AgentContext
from app.agents.chat_agent import ChatAgent, FastAckDecision, PlanStep
from app.services.routing_guards import research_intent_guard
from app.services.semantic_router import SemanticRouter
from app.tools.tavily_tools import DeepResearchOutput, ResearchSource


class _Stream:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", [
    "do a deep research on the US hopper dredge market",
    "write a comprehensive report on port infrastructure funding",
    "prepare a market analysis of dredging contractors on the east coast",
    "deep dive into the Army Corps dredging budget for next year",
    "research this topic thoroughly and cite your sources",
    "I need a detailed competitive analysis of Great Lakes Dredge and Dock",
])
def test_research_phrasings_are_detected(query):
    out = research_intent_guard(query)
    assert out.triggered and out.name == "research_intent"
    assert out.action_type == "research" and out.needs_tools is True


@pytest.mark.parametrize("query", [
    "what's the weather in boston",
    "how many holidays do we get",
    "latest news on nvidia earnings",
    "summarize this document",
    "deep research",  # too short to be a real request
])
def test_ordinary_queries_are_not_research(query):
    assert not research_intent_guard(query).triggered


def test_routes_yaml_has_deep_research_route_with_preferred_tool():
    router = SemanticRouter(config_path=Path(__file__).resolve().parents[2] / "config" / "routes.yaml",
                            embedding_url="http://embedding-api.test:8005")
    routes = router._parse_config()
    route = routes["deep_research"]
    assert route.preferred_tool == "deep_research"
    assert route.action_type == "research" and route.complexity == "complex"
    assert "few minutes" in route.response
    assert len(route.utterances) >= 8


# ---------------------------------------------------------------------------
# confirmation ack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard_marks_decision_and_ack_promises_minutes(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()

    async def has_key():
        return "tvly-test"

    monkeypatch.setattr("app.tools.tavily_tools._tavily_api_key", has_key)

    decision = FastAckDecision(action_type="search", needs_tools=True, response="On it.", routing_source="llm")
    decision = await agent._apply_routing_guards("write a comprehensive report on port funding", decision, [], stream)
    assert decision.preferred_tool == "deep_research"
    assert decision.routing_source == "llm+research_intent_guard"

    decision = await agent._confirm_deep_research(decision, stream)
    assert decision.preferred_tool == "deep_research"
    assert decision.needs_tools is True and decision.action_type == "research"
    assert "few minutes" in decision.response


@pytest.mark.asyncio
async def test_without_tavily_key_downgrades_to_web_search(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()

    async def no_key():
        return ""

    monkeypatch.setattr("app.tools.tavily_tools._tavily_api_key", no_key)

    decision = FastAckDecision(action_type="research", needs_tools=True, response="x", preferred_tool="deep_research")
    decision = await agent._confirm_deep_research(decision, stream)
    assert decision.preferred_tool is None
    assert decision.needs_tools is True
    assert "few minutes" not in decision.response
    assert any(getattr(e, "data", None) and e.data.get("phase") == "escalation" for e in stream.events)


@pytest.mark.asyncio
async def test_router_hit_carries_preferred_tool_to_ack(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    context = AgentContext()

    class Match:
        route = "deep_research"
        score = 0.91
        action_type = "research"
        needs_tools = True
        response = "router text"
        complexity = "complex"
        matched_utterance = "u"
        elapsed_ms = 1
        preferred_tool = "deep_research"

    class Router:
        async def route(self, q):
            return Match()

    class S:
        semantic_router_enabled = True
        semantic_router_mode = "live"

    monkeypatch.setattr("app.config.settings.get_settings", lambda: S())
    monkeypatch.setattr("app.services.semantic_router.get_semantic_router", lambda: Router())

    decision = await agent._route_intent("do a deep research on hopper dredges", context)
    assert decision.preferred_tool == "deep_research"
    assert decision.routing_source == "semantic_router:deep_research"


# ---------------------------------------------------------------------------
# planner
# ---------------------------------------------------------------------------


def _enable(monkeypatch, names):
    monkeypatch.setattr("app.agents.chat_agent.ToolRegistry.has", lambda name: name in names)
    monkeypatch.setattr(
        "app.agents.chat_agent.ToolRegistry.get",
        lambda name: (lambda query, limit=5: None) if name == "document_search" else (lambda **kw: None),
    )


@pytest.mark.asyncio
async def test_fallback_plan_uses_deep_research_instead_of_web_search(monkeypatch):
    agent = ChatAgent()

    class Failing:
        async def chat_completion(self, **kwargs):
            raise RuntimeError("planner down")

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: Failing())
    _enable(monkeypatch, {"document_search", "web_search", "deep_research"})

    plan = await agent._generate_plan(
        query="write a comprehensive report on port funding",
        context=AgentContext(),
        dispatch=FastAckDecision(action_type="research", needs_tools=True, response="x", preferred_tool="deep_research"),
    )
    tools = [s.tool for s in plan.steps]
    assert "deep_research" in tools and "web_search" not in tools
    step = next(s for s in plan.steps if s.tool == "deep_research")
    assert step.args["question"] == "write a comprehensive report on port funding"


@pytest.mark.asyncio
async def test_llm_plan_that_drops_deep_research_gets_it_back(monkeypatch):
    agent = ChatAgent()

    class Client:
        async def chat_completion(self, **kwargs):
            return {"choices": [{"message": {"content": (
                '{"summary":"s","steps":[{"id":"1","tool":"web_search","objective":"o","run_mode":"parallel","args":{"query":"q"}}],'
                '"parallel_groups":[],"feedback_points":[],"estimated_duration":"quick"}'
            )}}]}

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: Client())
    _enable(monkeypatch, {"document_search", "web_search", "deep_research"})

    plan = await agent._generate_plan(
        query="deep dive into the Army Corps dredging budget",
        context=AgentContext(),
        dispatch=FastAckDecision(action_type="research", needs_tools=True, response="x", preferred_tool="deep_research"),
    )
    tools = [s.tool for s in plan.steps]
    assert tools == ["deep_research"]


@pytest.mark.asyncio
async def test_plain_research_query_still_uses_web_search(monkeypatch):
    agent = ChatAgent()

    class Failing:
        async def chat_completion(self, **kwargs):
            raise RuntimeError("planner down")

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: Failing())
    _enable(monkeypatch, {"document_search", "web_search", "deep_research"})

    plan = await agent._generate_plan(
        query="latest news on nvidia earnings",
        context=AgentContext(),
        dispatch=FastAckDecision(action_type="research", needs_tools=True, response="x"),
    )
    tools = [s.tool for s in plan.steps]
    assert "web_search" in tools and "deep_research" not in tools


# ---------------------------------------------------------------------------
# execution message + synthesis rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_message_before_deep_research_step(monkeypatch):
    from app.agents.chat_agent import ExecutionPlan

    agent = ChatAgent()
    stream = _Stream()
    context = AgentContext()

    async def fake_execute_step(step, stream_, cancel, ctx):
        ctx.tool_results[step.tool] = DeepResearchOutput(success=True, status="completed", report="# R", sources=[])

    monkeypatch.setattr(agent, "_execute_step", fake_execute_step)
    plan = ExecutionPlan(summary="s", steps=[PlanStep(id="s1", tool="deep_research", objective="o", args={"question": "q"})])
    await agent._execute_plan("q", stream, asyncio.Event(), context, plan)

    interim = [e for e in stream.events if getattr(e, "data", None) and e.data.get("tool") == "deep_research" and e.data.get("phase") == "interim"]
    assert interim and "few minutes" in interim[0].message
    assert any(getattr(e, "data", None) and e.data.get("phase") == "deep_research" for e in stream.events)


def test_synthesis_context_renders_report_and_sources():
    agent = ChatAgent()
    context = AgentContext(tool_results={"deep_research": DeepResearchOutput(
        success=True, status="completed", report="# Hopper dredges\nFindings [1]",
        sources=[ResearchSource(title="USACE", url="https://usace.example")],
    )})
    text = agent._build_synthesis_context("write a report on hopper dredges", context)
    assert "cited research report" in text
    assert "Findings [1]" in text
    assert "https://usace.example" in text
    assert "tier: web" in text
