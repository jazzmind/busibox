"""Deep-research intent: router route, regex fallback, confirmation ack, planner."""

import asyncio
from pathlib import Path

import pytest

from app.agents.base_agent import AgentContext
from app.agents.chat_agent import ChatAgent, FastAckDecision, PlanStep, _ends_with_yes_no_question
from app.services.routing_guards import (
    deep_research_offer_guard,
    pending_research_query,
    research_intent_guard,
)
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


def _with_key(monkeypatch, *, confirm=True):
    async def has_key():
        return "tvly-test"

    monkeypatch.setattr("app.tools.tavily_tools._tavily_api_key", has_key)

    class S:
        deep_research_confirm = confirm
        clarify_review_model = ""
        clarify_review_timeout_seconds = 6.0
        # Explicit, because both readers fail closed on a missing attribute:
        # the consented turn must visibly route to the orchestrator here, not
        # fall back to the one-step plan by accident of an incomplete stub.
        research_orchestrator_enabled = True
        chat_loop_first_tiers = ["complex", "research"]
        chat_loop_budget_seconds = 300

    monkeypatch.setattr("app.config.settings.get_settings", lambda: S())


QUESTION = "write a comprehensive report on port funding"


@pytest.mark.asyncio
async def test_guard_marks_decision_and_turn_stops_at_an_offer(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    _with_key(monkeypatch)

    decision = FastAckDecision(action_type="search", needs_tools=True, response="On it.", routing_source="llm")
    decision = await agent._apply_routing_guards(QUESTION, decision, [], stream)
    assert decision.preferred_tool == "deep_research"
    assert decision.routing_source == "llm+research_intent_guard"

    decision = await agent._confirm_deep_research(decision, stream, query=QUESTION)
    # Offer only: no tools this turn, the question is carried for the next one.
    assert decision.needs_tools is False and decision.action_type == "direct"
    assert decision.preferred_tool == "deep_research"
    assert decision.pending_research == QUESTION
    assert "few minutes" in decision.response
    assert _ends_with_yes_no_question(decision.response)  # → Yes/No buttons
    assert decision.routing_source.endswith("+research_offer")


@pytest.mark.asyncio
async def test_confirmed_offer_runs_with_a_wait_notice(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    _with_key(monkeypatch)

    decision = FastAckDecision(action_type="research", needs_tools=True, response="",
                               preferred_tool="deep_research", routing_source="deep_research_offer_guard")
    decision = await agent._confirm_deep_research(decision, stream, query=QUESTION, confirmed=True)
    assert decision.needs_tools is True and decision.action_type == "research"
    assert decision.preferred_tool == "deep_research"
    assert decision.pending_research is None
    assert "few minutes" in decision.response
    assert not _ends_with_yes_no_question(decision.response)


@pytest.mark.asyncio
async def test_confirmation_can_be_disabled_to_announce_and_run(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    _with_key(monkeypatch, confirm=False)

    decision = FastAckDecision(action_type="research", needs_tools=True, response="x", preferred_tool="deep_research")
    decision = await agent._confirm_deep_research(decision, stream, query=QUESTION)
    assert decision.needs_tools is True and decision.action_type == "research"
    assert decision.pending_research is None
    assert "few minutes" in decision.response and "I'll post it here" in decision.response


# ---------------------------------------------------------------------------
# answering the offer on the next turn
# ---------------------------------------------------------------------------


def _offer_history(*, annotated=True):
    assistant = {"role": "assistant", "content": ChatAgent._RESEARCH_OFFER, "action_type": "direct"}
    if annotated:
        assistant["pending_research"] = QUESTION
    return [{"role": "user", "content": QUESTION}, assistant]


@pytest.mark.parametrize("reply", ["yes", "Yes", "yes please", "go ahead", "ok", "sure!"])
def test_yes_resumes_the_original_question(reply):
    out = deep_research_offer_guard(reply, _offer_history())
    assert out.triggered and out.name == "deep_research_offer"
    assert out.query == QUESTION
    assert out.action_type == "research" and out.needs_tools is True


def test_offer_is_recognised_from_text_when_annotation_is_missing():
    out = deep_research_offer_guard("yes", _offer_history(annotated=False))
    assert out.triggered and out.query == QUESTION


@pytest.mark.parametrize("reply", ["no", "No thanks", "not now"])
def test_no_closes_without_running(reply):
    out = deep_research_offer_guard(reply, _offer_history())
    assert out.triggered and out.direct_reply and out.needs_tools is False
    assert "won't run" in out.direct_reply


def test_other_replies_fall_through_to_normal_routing():
    assert not deep_research_offer_guard("actually just the east coast contractors", _offer_history()).triggered
    assert not deep_research_offer_guard("yes", [{"role": "assistant", "content": "Would you like me to search?"}]).triggered
    assert pending_research_query([]) is None


@pytest.mark.asyncio
async def test_yes_turn_runs_deep_research_on_the_original_question(monkeypatch):
    """End to end through run_with_streaming: the research runs on the
    question, not on 'yes'.

    A consented turn now goes to the research orchestrator rather than a
    one-step plan, so the capture point is ``ResearchOrchestrator.run``. The
    planner must NOT be reached: if it is, the turn has silently fallen back
    to the old path.
    """
    agent = ChatAgent()
    stream = _Stream()
    _with_key(monkeypatch)
    context = AgentContext(recent_messages=_offer_history())

    async def setup(ctx, s, q):
        return context

    captured = {}

    async def stop_at_orchestrator(self, question, parent_ctx, s, cancel):
        captured["query"] = question
        raise RuntimeError("stop here")

    async def planner_must_not_run(query, ctx, decision):
        raise AssertionError("consented research fell back to the planner")

    async def no_attachments(query, s, ctx):
        return None

    from app.services.research_orchestrator import ResearchOrchestrator
    monkeypatch.setattr(ResearchOrchestrator, "run", stop_at_orchestrator)
    monkeypatch.setattr(agent, "_setup_context", setup)
    monkeypatch.setattr(agent, "_resolve_attachments", no_attachments)
    monkeypatch.setattr(agent, "_generate_plan", planner_must_not_run)

    # The orchestrator's RuntimeError is caught by the deep-pass handler and
    # turned into an error reply rather than propagating.
    reply = await agent.run_with_streaming("yes", stream, asyncio.Event(), {})

    assert captured["query"] == QUESTION  # not "yes"
    assert "error" in reply.lower()
    plans = [e for e in stream.events if e.type == "plan"]
    assert plans and "Deep research" in plans[0].message, "the plan event names the orchestrator"
    guards = [e.data.get("guard") for e in stream.events if getattr(e, "data", None)]
    assert "deep_research_offer" in guards
    acks = [e.message for e in stream.events if getattr(e, "data", None) and e.data.get("phase") == "fast_ack"]
    assert acks and "few minutes" in acks[0]


@pytest.mark.asyncio
async def test_no_turn_closes_without_planning(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    _with_key(monkeypatch)
    context = AgentContext(recent_messages=_offer_history())

    async def setup(ctx, s, q):
        return context

    async def must_not_plan(*args, **kwargs):
        raise AssertionError("planner must not run after 'no'")

    monkeypatch.setattr(agent, "_setup_context", setup)
    monkeypatch.setattr(agent, "_generate_plan", must_not_plan)

    reply = await agent.run_with_streaming("no", stream, asyncio.Event(), {})
    assert "won't run" in reply


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
