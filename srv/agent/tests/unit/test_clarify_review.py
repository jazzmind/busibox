"""Clarify review by a larger model, and the expanded route table.

Production, 2026-09-10: "who should I ask about IT questions?" was answered
with "What specific IT question are you asking?" — twice. The 0.8B classifier
judges ambiguity from the query text alone, so a well-formed question it
cannot answer itself looks the same to it as a genuinely ambiguous one.
"""

import json
from pathlib import Path

import pytest

from app.agents.base_agent import AgentContext
from app.agents.chat_agent import ChatAgent, FastAckDecision
from app.services.routing_guards import factual_guard
from app.services.semantic_router import SemanticRouter

ROUTES = Path(__file__).resolve().parents[2] / "config" / "routes.yaml"


class _Stream:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)


def _reviewer(monkeypatch, *, ambiguous, question="", reason="r", raw=None, boom=None):
    class Client:
        async def chat_completion(self, **kwargs):
            Client.model = kwargs.get("model")
            if boom:
                raise boom
            body = raw if raw is not None else json.dumps(
                {"ambiguous": ambiguous, "question": question, "reason": reason}
            )
            return {"choices": [{"message": {"content": body}}]}

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: Client())
    return Client


def _clarify(**kw):
    base = dict(action_type="clarify", needs_tools=False, response="Sure.",
                follow_up_question="What specific IT question are you asking?",
                routing_source="llm")
    base.update(kw)
    return FastAckDecision(**base)


# ---------------------------------------------------------------------------
# the review itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_answerable_question_is_overturned_to_search(monkeypatch):
    agent = ChatAgent()
    client = _reviewer(monkeypatch, ambiguous=False, reason="answerable from documents")

    out = await agent._review_clarify("who should I ask about IT questions?", _clarify(), AgentContext())

    assert out.action_type == "search"
    assert out.needs_tools is True
    assert out.follow_up_question is None
    assert out.response in ChatAgent._ACK_RESPONSES
    assert out.routing_source == "llm+clarify_review:overturned"
    assert client.model == "tool_calling"  # local 35B, no marginal cost


@pytest.mark.asyncio
async def test_genuinely_ambiguous_is_kept_with_a_better_question(monkeypatch):
    agent = ChatAgent()
    _reviewer(monkeypatch, ambiguous=True, question="Which project are you asking about?")

    out = await agent._review_clarify("what about that one?", _clarify(), AgentContext())

    assert out.action_type == "clarify"
    assert out.needs_tools is False
    assert out.follow_up_question == "Which project are you asking about?"
    assert out.routing_source == "llm+clarify_review:kept"


@pytest.mark.asyncio
async def test_kept_but_echoing_question_falls_back_to_the_original(monkeypatch):
    agent = ChatAgent()
    _reviewer(monkeypatch, ambiguous=True, question="Set action_type to clarify.")

    out = await agent._review_clarify("help", _clarify(follow_up_question="What can I help with?"), AgentContext())

    assert out.follow_up_question == "What can I help with?"


@pytest.mark.asyncio
async def test_non_clarify_decisions_are_not_reviewed(monkeypatch):
    agent = ChatAgent()

    class Boom:
        async def chat_completion(self, **kwargs):
            raise AssertionError("review must not run")

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: Boom())
    decision = FastAckDecision(action_type="search", needs_tools=True, response="On it.")
    assert (await agent._review_clarify("q", decision, AgentContext())) is decision


@pytest.mark.parametrize("kwargs", [
    {"raw": "not json at all"},
    {"raw": '{"ambiguous":'},
    {"boom": RuntimeError("model down")},
])
@pytest.mark.asyncio
async def test_review_failure_keeps_the_original_decision(monkeypatch, kwargs):
    agent = ChatAgent()
    _reviewer(monkeypatch, ambiguous=False, **kwargs)

    out = await agent._review_clarify("who should I ask about IT?", _clarify(), AgentContext())

    assert out.action_type == "clarify"
    assert out.routing_source == "llm"


@pytest.mark.asyncio
async def test_review_can_be_disabled(monkeypatch):
    agent = ChatAgent()

    class S:
        clarify_review_model = ""
        clarify_review_timeout_seconds = 6.0

    monkeypatch.setattr("app.config.settings.get_settings", lambda: S())

    class Boom:
        async def chat_completion(self, **kwargs):
            raise AssertionError("review must not run when disabled")

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: Boom())
    out = await agent._review_clarify("q", _clarify(), AgentContext())
    assert out.action_type == "clarify" and out.routing_source == "llm"


# ---------------------------------------------------------------------------
# wiring into the guard chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guards_run_the_review_and_report_it(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    _reviewer(monkeypatch, ambiguous=False, reason="answerable")

    out = await agent._apply_routing_guards(
        "who should I ask about IT questions?", _clarify(), [], stream, AgentContext()
    )

    assert out.action_type == "search" and out.needs_tools is True
    guards = [e.data.get("guard") for e in stream.events if getattr(e, "data", None)]
    assert "clarify_review" in guards


@pytest.mark.asyncio
async def test_upheld_clarify_is_not_overridden_by_the_factual_guard(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    _reviewer(monkeypatch, ambiguous=True, question="Which office's payroll?")

    out = await agent._apply_routing_guards(
        "what about payroll", _clarify(), [], stream, AgentContext()
    )

    assert out.action_type == "clarify"
    assert out.follow_up_question == "Which office's payroll?"
    assert "factual_guard" not in out.routing_source


@pytest.mark.asyncio
async def test_factual_guard_still_backstops_when_the_review_is_unavailable(monkeypatch):
    agent = ChatAgent()
    stream = _Stream()
    _reviewer(monkeypatch, ambiguous=False, boom=RuntimeError("model down"))

    out = await agent._apply_routing_guards(
        "what is our per diem policy", _clarify(), [], stream, AgentContext()
    )

    assert out.action_type == "search" and out.needs_tools is True
    assert out.routing_source.endswith("+factual_guard")


def test_factual_guard_now_covers_clarify_decisions():
    kept = factual_guard("what is our per diem rate", "clarify", False, "llm")
    assert kept.triggered and kept.action_type == "search"
    assert "clarified instead of searched" in kept.reason

    direct = factual_guard("what is our per diem rate", "direct", False, "llm")
    assert direct.triggered and "answered from memory" in direct.reason

    # A clarify on something with no company-fact signal is still allowed.
    assert not factual_guard("what about that one", "clarify", False, "llm").triggered


# ---------------------------------------------------------------------------
# routes.yaml
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def routes():
    return SemanticRouter(config_path=ROUTES, embedding_url="http://embedding-api.test:8005")._parse_config()


def test_new_routes_exist_and_retrieve(routes):
    for name in ("who_to_contact", "company_news", "industry_research"):
        assert name in routes, name
        assert routes[name].needs_tools is True
        assert len(routes[name].utterances) >= 6


def test_who_to_contact_covers_the_production_failure(routes):
    assert any("who should I ask about IT questions" in u for u in routes["who_to_contact"].utterances)
    assert routes["who_to_contact"].action_type == "search"


def test_only_deep_research_forces_a_tool(routes):
    forced = {n: r.preferred_tool for n, r in routes.items() if r.preferred_tool}
    assert forced == {"deep_research": "deep_research"}


def test_research_routes_are_separated_by_threshold(routes):
    # industry_research must not inherit deep_research's forced tool, and
    # deep_research keeps the stricter threshold so it can't capture a
    # plain market question.
    assert routes["industry_research"].preferred_tool is None
    assert routes["deep_research"].threshold == pytest.approx(0.84)


def test_every_route_has_a_valid_action_type(routes):
    valid = {"direct", "research", "search", "analysis", "clarify", "multi_step"}
    for name, route in routes.items():
        assert route.action_type in valid, f"{name}: {route.action_type}"
        assert route.response.strip(), name


def test_no_duplicate_utterances_across_routes(routes):
    seen = {}
    for name, route in routes.items():
        for utterance in route.utterances:
            key = utterance.strip().lower()
            assert key not in seen, f"{utterance!r} in both {seen.get(key)} and {name}"
            seen[key] = name
