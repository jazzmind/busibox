"""Anti-loop and escalation guards (services/routing_guards.py + ChatAgent wiring)."""

import asyncio

import pytest

from app.agents.base_agent import AgentContext
from app.agents.chat_agent import (
    ChatAgent,
    ExecutionPlan,
    FastAckDecision,
    PlanStep,
    _ends_with_yes_no_question,
)
from app.services.routing_guards import (
    affirmation_guard,
    cap_plan_steps,
    clarify_loop_guard,
    factual_guard,
    previous_turn_was_clarify,
)


def _hist(*turns):
    return [{"role": r, "content": c, **extra} for r, c, extra in turns]


# ---------------------------------------------------------------------------
# clarify loop
# ---------------------------------------------------------------------------


def test_previous_clarify_detected_from_annotation_or_heuristic():
    assert previous_turn_was_clarify(_hist(("assistant", "Which project do you mean?", {"action_type": "clarify"})))
    assert previous_turn_was_clarify(_hist(("assistant", "Which project do you mean?", {})))
    assert not previous_turn_was_clarify(_hist(("assistant", "Here is the answer. " * 30 + "Anything else?", {})))
    assert not previous_turn_was_clarify(_hist(("assistant", "The rate is $75 per day.", {})))
    assert not previous_turn_was_clarify([])


def test_second_clarify_in_a_row_is_overridden():
    hist = _hist(("user", "vacation", {}), ("assistant", "Do you mean accrual or carry-over?", {"action_type": "clarify"}), ("user", "the policy", {}))
    out = clarify_loop_guard("clarify", hist)
    assert out.triggered and out.name == "clarify_loop"
    assert out.action_type == "search" and out.needs_tools is True


def test_first_clarify_is_allowed():
    hist = _hist(("assistant", "The rate is $75 per day.", {}))
    assert not clarify_loop_guard("clarify", hist).triggered
    assert not clarify_loop_guard("search", _hist(("assistant", "Which one?", {}))).triggered


# ---------------------------------------------------------------------------
# affirmation
# ---------------------------------------------------------------------------


OFFER = _hist(("assistant", "I found the 2025 handbook. Would you like me to summarize the PTO section?", {}))


@pytest.mark.parametrize("reply", ["yes", "Yes please", "ok", "sure!", "go ahead", "y"])
def test_yes_after_offer_becomes_the_offer(reply):
    out = affirmation_guard(reply, OFFER, _ends_with_yes_no_question)
    assert out.triggered and out.needs_tools is True
    assert "summarize the PTO section" in out.query
    assert out.direct_reply is None


@pytest.mark.parametrize("reply", ["no", "No thanks", "not now"])
def test_no_after_offer_closes_politely(reply):
    out = affirmation_guard(reply, OFFER, _ends_with_yes_no_question)
    assert out.triggered and out.needs_tools is False
    assert out.direct_reply and "leave it there" in out.direct_reply


def test_affirmation_ignored_without_offer_or_for_long_messages():
    plain = _hist(("assistant", "The rate is $75 per day.", {}))
    assert not affirmation_guard("yes", plain, _ends_with_yes_no_question).triggered
    assert not affirmation_guard("yes, and also what about managers in the field office?", OFFER, _ends_with_yes_no_question).triggered
    assert not affirmation_guard("what's the per diem", OFFER, _ends_with_yes_no_question).triggered


# ---------------------------------------------------------------------------
# factual guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", [
    "how many holidays do we get this year",
    "what is our per diem rate for Boston",
    "when is the 401k enrollment deadline",
    "what's the company policy on overtime",
])
def test_direct_answer_to_company_fact_is_forced_to_search(query):
    out = factual_guard(query, "direct", False, "llm")
    assert out.triggered and out.action_type == "search" and out.needs_tools is True


def test_glossary_term_forces_search():
    out = factual_guard("tell me about PREC", "direct", False, "llm", glossary_terms=["PREC"])
    assert out.triggered and "PREC" in out.reason


@pytest.mark.parametrize("query,action,needs,source", [
    ("hi there, how are you", "direct", False, "llm"),
    ("thanks, that helped", "direct", False, "llm"),
    ("what is the difference between a bid bond and a performance bond", "direct", False, "llm"),
    ("how many holidays do we get", "search", True, "llm"),      # already retrieving
    ("how many pages is the attached", "direct", False, "attachment_rule"),
])
def test_factual_guard_leaves_other_cases_alone(query, action, needs, source):
    assert not factual_guard(query, action, needs, source).triggered


def test_factual_guard_also_covers_clarify():
    """`clarify` used to be exempt. It isn't: asking a question instead of
    looking is the same decision not to retrieve as answering from memory,
    and it is wrong for a question the documents can answer. See
    test_clarify_review.py::test_factual_guard_now_covers_clarify_decisions."""
    out = factual_guard("how many holidays do we get", "clarify", False, "llm")
    assert out.triggered and out.action_type == "search"


# ---------------------------------------------------------------------------
# step cap
# ---------------------------------------------------------------------------


def _steps(*tools):
    return [PlanStep(id=f"s{i}", tool=t, objective=t, args={}) for i, t in enumerate(tools)]


def test_cap_keeps_order_and_protects_deep_research():
    steps = _steps("document_search", "web_search", "get_weather", "memory_search", "deep_research", "web_extract")
    kept = cap_plan_steps(steps, 3)
    assert [s.tool for s in kept] == ["document_search", "web_search", "deep_research"]
    assert cap_plan_steps(steps, 10) == steps
    assert cap_plan_steps(steps, 0) == steps


# ---------------------------------------------------------------------------
# ChatAgent wiring
# ---------------------------------------------------------------------------


class _Stream:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_apply_routing_guards_rewrites_decision_and_streams_thought():
    agent = ChatAgent()
    stream = _Stream()
    decision = FastAckDecision(action_type="direct", needs_tools=False, response="We get 10 holidays.", routing_source="llm")

    out = await agent._apply_routing_guards("how many holidays do we get", decision, [], stream)

    assert out.needs_tools is True and out.action_type == "search"
    assert out.routing_source == "llm+factual_guard"
    assert out.response in ChatAgent._ACK_RESPONSES
    assert any(getattr(e, "data", {}) and e.data.get("phase") == "guard" for e in stream.events)


@pytest.mark.asyncio
async def test_apply_routing_guards_breaks_clarify_loop():
    agent = ChatAgent()
    stream = _Stream()
    history = _hist(("assistant", "Which office?", {"action_type": "clarify"}))
    decision = FastAckDecision(action_type="clarify", needs_tools=False, response="Sure.", follow_up_question="Which office?")

    out = await agent._apply_routing_guards("the per diem for the field crew", decision, history, stream)

    assert out.action_type == "search" and out.needs_tools is True
    assert out.follow_up_question is None
    assert out.routing_source.endswith("+clarify_loop_guard")


@pytest.mark.asyncio
async def test_no_after_offer_returns_direct_reply_without_routing(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    context = AgentContext(recent_messages=OFFER)

    async def fake_setup(*args, **kwargs):
        return context

    async def must_not_route(*args, **kwargs):
        raise AssertionError("router must not run")

    monkeypatch.setattr(agent, "_setup_context", fake_setup)
    monkeypatch.setattr(agent, "_route_intent", must_not_route)

    result = await agent.run_with_streaming("no thanks", stream, asyncio.Event(), context={})

    assert "leave it there" in result
    assert any(e.data and e.data.get("phase") == "direct" for e in stream.events if getattr(e, "data", None))


@pytest.mark.asyncio
async def test_yes_after_offer_runs_tools_with_the_offer(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    context = AgentContext(recent_messages=OFFER)
    seen = {}

    async def fake_setup(*args, **kwargs):
        return context

    async def must_not_route(*args, **kwargs):
        raise AssertionError("router must not run")

    async def fake_resolve(*args, **kwargs):
        return None

    async def fake_plan(query, ctx, decision):
        seen["query"] = query
        seen["decision"] = decision
        return ExecutionPlan(summary="s", steps=[], source="llm")

    async def fake_llm_driven(query, stream, cancel, ctx):
        ctx.tool_results["llm_response"] = "PTO summary"

    monkeypatch.setattr(agent, "_setup_context", fake_setup)
    monkeypatch.setattr(agent, "_route_intent", must_not_route)
    monkeypatch.setattr(agent, "_resolve_attachments", fake_resolve)
    monkeypatch.setattr(agent, "_generate_plan", fake_plan)
    monkeypatch.setattr(agent, "_execute_llm_driven", fake_llm_driven)

    result = await agent.run_with_streaming("yes", stream, asyncio.Event(), context={})

    assert "summarize the PTO section" in seen["query"]
    assert seen["decision"].routing_source == "affirmation_guard"
    assert result == "PTO summary"


@pytest.mark.asyncio
async def test_plan_fallback_on_complex_request_escalates_to_llm_driven(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    context = AgentContext()
    calls = {"plan_executed": False, "llm_driven": False}

    async def fake_setup(*args, **kwargs):
        return context

    async def fake_route(query, ctx):
        return FastAckDecision(action_type="research", needs_tools=True, response="On it.", complexity="complex")

    async def fake_resolve(*args, **kwargs):
        return None

    async def fake_plan(query, ctx, decision):
        return ExecutionPlan(summary="fallback", steps=[PlanStep(id="s1", tool="document_search", objective="o", args={"query": query})], source="fallback")

    async def fake_execute_plan(*args, **kwargs):
        calls["plan_executed"] = True

    async def fake_llm_driven(query, stream, cancel, ctx):
        calls["llm_driven"] = True
        ctx.tool_results["llm_response"] = "answer"

    monkeypatch.setattr(agent, "_setup_context", fake_setup)
    monkeypatch.setattr(agent, "_route_intent", fake_route)
    monkeypatch.setattr(agent, "_resolve_attachments", fake_resolve)
    monkeypatch.setattr(agent, "_generate_plan", fake_plan)
    monkeypatch.setattr(agent, "_execute_plan", fake_execute_plan)
    monkeypatch.setattr(agent, "_execute_llm_driven", fake_llm_driven)

    await agent.run_with_streaming("write a comprehensive comparison of hopper dredge suppliers", stream, asyncio.Event(), context={})

    assert calls == {"plan_executed": False, "llm_driven": True}
    assert any(getattr(e, "data", None) and e.data.get("phase") == "escalation" for e in stream.events)


@pytest.mark.asyncio
async def test_failed_search_step_is_retried_once(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    context = AgentContext()
    attempts = []

    class Failed:
        error = "search-api 500"
        found = False
        results = []

    class Ok:
        error = None
        found = True
        results = [1]

    async def fake_execute_step(step, stream_, cancel, ctx):
        attempts.append(step.tool)
        ctx.tool_results[step.tool] = Failed() if len(attempts) == 1 else Ok()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(agent, "_execute_step", fake_execute_step)
    monkeypatch.setattr("app.agents.chat_agent.asyncio.sleep", no_sleep)

    plan = ExecutionPlan(summary="s", steps=[PlanStep(id="s1", tool="document_search", objective="o", args={"query": "q"})])
    await agent._execute_plan("q", stream, asyncio.Event(), context, plan)

    assert attempts == ["document_search", "document_search"]
    assert any(getattr(e, "data", None) and e.data.get("phase") == "retry" for e in stream.events)
    assert context.tool_results["document_search"].found is True


@pytest.mark.asyncio
async def test_slow_steps_skipped_when_turn_budget_exhausted(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    context = AgentContext(turn_started=1.0)
    ran = []

    async def fake_execute_step(step, stream_, cancel, ctx):
        ran.append(step.tool)
        ctx.tool_results[step.tool] = type("R", (), {"error": None, "found": True, "results": [1]})()

    monkeypatch.setattr(agent, "_execute_step", fake_execute_step)
    monkeypatch.setattr("app.agents.chat_agent.time.monotonic", lambda: 10_000.0)

    plan = ExecutionPlan(summary="s", steps=[
        PlanStep(id="s1", tool="web_search", objective="o", args={"query": "q"}),
        PlanStep(id="s2", tool="deep_research", objective="o", args={"question": "q"}),
    ])
    await agent._execute_plan("q", stream, asyncio.Event(), context, plan)

    assert ran == ["deep_research"]
    assert any(getattr(e, "data", None) and e.data.get("skipped_tool") == "web_search" for e in stream.events)
