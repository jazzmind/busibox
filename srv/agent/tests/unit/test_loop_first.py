"""Hard turns run loop-first; simple ones keep the planner.

The chat agent planned once and executed a static list of steps. That is the
right shape for "what's the weather" and the wrong shape for anything where
the second tool call should depend on what the first one returned. The
LLM-driven loop already existed (``_execute_llm_driven``) but was wired as the
fallback — reached only when the planner produced nothing. These tests pin
the inversion: for the configured tiers the planner is skipped and the model
drives, under a wall-clock deadline the tool wrapper enforces.
"""

import time

import pytest

from app.agents.base_agent import (
    LOOP_MODE_DIRECTIVE,
    RESEARCH_LOOP_DIRECTIVE,
    RESEARCH_SYNTHESIS_DIRECTIVE,
    AgentContext,
)
from app.agents.chat_agent import ChatAgent, FastAckDecision
from app.config.settings import get_settings


def _decision(**kw) -> FastAckDecision:
    base = dict(action_type="multi_step", needs_tools=True, complexity="moderate", response="")
    base.update(kw)
    return FastAckDecision(**base)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_default_tiers_are_complex_and_research():
    assert set(get_settings().chat_loop_first_tiers) == {"complex", "research"}


@pytest.mark.parametrize(
    "decision,expected",
    [
        pytest.param(_decision(complexity="complex"), "complex", id="complex-loops"),
        pytest.param(_decision(action_type="research", complexity="moderate"), "research",
                     id="research-loops-regardless-of-complexity"),
        pytest.param(_decision(complexity="simple"), None, id="simple-plans"),
        pytest.param(_decision(complexity="moderate"), None, id="moderate-plans"),
        pytest.param(_decision(action_type="direct", complexity="complex"), "complex",
                     id="complexity-tier-still-applies-to-direct"),
    ],
)
def test_loop_first_tier_by_decision(decision, expected):
    agent = ChatAgent()
    assert agent._loop_first_tier(decision, AgentContext()) == expected


def test_consented_deep_research_is_not_routed_to_the_chat_loop():
    """That turn belongs to the orchestrator. Running Tavily's multi-minute
    /research from inside a chat loop — or worse, nesting it — is exactly what
    the loop directive tells the model not to do."""
    agent = ChatAgent()
    d = _decision(action_type="research", complexity="complex", preferred_tool="deep_research")
    assert agent._loop_first_tier(d, AgentContext()) is None


def test_structured_output_runs_never_loop():
    """/runs/invoke with a response_schema disables tools entirely."""
    agent = ChatAgent()
    ctx = AgentContext(response_schema={"type": "object"})
    assert agent._loop_first_tier(_decision(complexity="complex"), ctx) is None


def test_empty_tier_list_restores_plan_once_everywhere(monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_loop_first_tiers", [])
    agent = ChatAgent()
    assert agent._loop_first_tier(_decision(complexity="complex"), AgentContext()) is None
    assert agent._loop_first_tier(_decision(action_type="research"), AgentContext()) is None


# ---------------------------------------------------------------------------
# What the loop is told
# ---------------------------------------------------------------------------


def test_loop_directive_only_appears_in_loop_mode():
    agent = ChatAgent()
    plain = agent._build_enriched_system_prompt(AgentContext())
    looped = agent._build_enriched_system_prompt(AgentContext(loop_mode="complex"))
    assert LOOP_MODE_DIRECTIVE not in plain
    assert LOOP_MODE_DIRECTIVE in looped
    assert RESEARCH_LOOP_DIRECTIVE not in looped, "complex turns are not told about research tools"


def test_research_loop_gets_tool_guidance_and_report_format():
    """On the loop path the model's text is the answer — no synthesis pass —
    so the report format has to arrive via the system prompt."""
    agent = ChatAgent()
    text = agent._build_enriched_system_prompt(AgentContext(loop_mode="research"))
    assert RESEARCH_LOOP_DIRECTIVE in text
    assert RESEARCH_SYNTHESIS_DIRECTIVE in text


def test_research_guidance_covers_every_tavily_mode():
    """The point of the directive: search for breadth, extract for depth, map
    for navigating a known site, and never nest deep_research."""
    for needle in (
        "`web_search`", "`web_extract`", "`web_map`", "`deep_research`",
        'topic="news"', 'topic="finance"', "time_range", "do NOT call this from inside a loop",
    ):
        assert needle in RESEARCH_LOOP_DIRECTIVE, needle


def test_loop_directive_tells_the_model_how_to_stop():
    """A loop with no stopping rule is a bill, not a feature."""
    for needle in ("Stop when", "time budget is exhausted", "deduplicated"):
        assert needle in LOOP_MODE_DIRECTIVE, needle


# ---------------------------------------------------------------------------
# Wall-clock deadline
# ---------------------------------------------------------------------------


def test_deadline_is_off_by_default():
    assert AgentContext().loop_deadline == 0.0


def test_budget_setting_clears_a_realistic_research_loop():
    """Four advanced searches plus two or three extracts is 60–120s of tool
    time before the model writes anything. 300s leaves room to write."""
    assert get_settings().chat_loop_budget_seconds >= 240


@pytest.mark.asyncio
async def test_tool_wrapper_refuses_calls_past_the_deadline(monkeypatch):
    """The refusal is returned *to the model* as the tool's result, not raised,
    so the loop ends with an answer rather than a cancelled stream."""
    import asyncio
    from app.agents import base_agent as ba

    calls = []

    async def fake_tool(query: str = "") -> str:
        calls.append(query)
        return "real result"

    monkeypatch.setattr(ba.ToolRegistry, "get", lambda name: fake_tool if name == "web_search" else None)

    agent = ChatAgent()
    agent.config.tools = ["web_search"]
    ctx = AgentContext(loop_deadline=time.monotonic() - 1)  # already expired
    events = []

    async def stream(ev):
        events.append(ev)

    # Reach into the wrapper the same way _execute_llm_driven does.
    wrapped = ba._wrap_tool_with_truncation(fake_tool, "web_search")
    cancel = asyncio.Event()

    # Build the monitored tool exactly as the loop builds it, by invoking the
    # code path with a stub Agent that just calls the first tool it was given.
    captured = {}

    class _StubAgent:
        def __init__(self, *a, **kw):
            captured["tools"] = kw.get("tools") or []

    monkeypatch.setattr(ba, "Agent", _StubAgent)

    async def _no_stream(*a, **kw):
        return ""
    monkeypatch.setattr(agent, "_stream_llm_events", _no_stream)

    await agent._execute_llm_driven("q", stream, cancel, ctx)
    tool = captured["tools"][0]

    out = await tool(query="anything")

    assert "TIME BUDGET EXHAUSTED" in out
    assert calls == [], "the real tool must not run past the deadline"
    assert any(getattr(e, "data", {}).get("phase") == "budget" for e in events)


@pytest.mark.asyncio
async def test_tool_wrapper_runs_normally_before_the_deadline(monkeypatch):
    import asyncio
    from app.agents import base_agent as ba

    async def fake_tool(query: str = "") -> str:
        return f"result for {query}"

    monkeypatch.setattr(ba.ToolRegistry, "get", lambda name: fake_tool if name == "web_search" else None)
    agent = ChatAgent()
    agent.config.tools = ["web_search"]
    ctx = AgentContext(loop_deadline=time.monotonic() + 60)
    captured = {}

    class _StubAgent:
        def __init__(self, *a, **kw):
            captured["tools"] = kw.get("tools") or []

    monkeypatch.setattr(ba, "Agent", _StubAgent)

    async def _no_stream(*a, **kw):
        return ""
    monkeypatch.setattr(agent, "_stream_llm_events", _no_stream)

    async def stream(ev):
        pass

    await agent._execute_llm_driven("q", stream, asyncio.Event(), ctx)
    out = await captured["tools"][0](query="dredging")
    assert out == "result for dredging"
    assert ctx.calls_for("web_search")[0].source == "loop"


# ---------------------------------------------------------------------------
# The consent gate is structural, not a prompt instruction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deep_research_is_withheld_from_every_loop(monkeypatch):
    """A loop-first "complex" turn has the full tool list, including
    deep_research — a paid, multi-minute pass behind a Yes/No gate. With it
    reachable, spending it is one model decision away and the consent flow
    and 300s budget are both bypassed. So the loop never gets the tool."""
    import asyncio
    from app.agents import base_agent as ba

    async def fake(**kw):
        return "x"

    monkeypatch.setattr(ba.ToolRegistry, "get", lambda name: fake)
    agent = ChatAgent()
    assert "deep_research" in agent.config.tools
    captured = {}

    class _StubAgent:
        def __init__(self, *a, **kw):
            captured["tools"] = [t.__name__ for t in (kw.get("tools") or [])]

    monkeypatch.setattr(ba, "Agent", _StubAgent)

    async def _no_stream(*a, **kw):
        return ""
    monkeypatch.setattr(agent, "_stream_llm_events", _no_stream)

    async def stream(ev):
        pass

    for mode in ("complex", "research"):
        await agent._execute_llm_driven("q", stream, asyncio.Event(), AgentContext(loop_mode=mode))
        assert "deep_research" not in captured["tools"], mode
        assert "web_search" in captured["tools"]

    # Without loop_mode (planner-escalation path) the list is unchanged.
    await agent._execute_llm_driven("q", stream, asyncio.Event(), AgentContext())
    assert "deep_research" in captured["tools"]


def test_plan_event_does_not_claim_no_tools_before_a_loop_or_research_pass():
    from app.agents.chat_agent import ExecutionPlan
    agent = ChatAgent()
    assert "respond directly" in agent._format_plan_summary(ExecutionPlan(summary="", steps=[]))
    loop = ExecutionPlan(summary="Model-driven: tools chosen from each result in turn.", steps=[], source="loop_first")
    orch = ExecutionPlan(summary="Deep research: parallel workers, then a written report.", steps=[], source="orchestrator")
    assert agent._format_plan_summary(loop) == loop.summary
    assert agent._format_plan_summary(orch) == orch.summary


@pytest.mark.asyncio
async def test_deadline_refusal_is_recorded_as_a_failed_call(monkeypatch):
    import asyncio
    from app.agents import base_agent as ba

    async def fake_tool(query: str = "") -> str:
        return "real"

    monkeypatch.setattr(ba.ToolRegistry, "get", lambda name: fake_tool if name == "web_search" else None)
    agent = ChatAgent()
    agent.config.tools = ["web_search"]
    ctx = AgentContext(loop_deadline=time.monotonic() - 1)
    captured = {}

    class _StubAgent:
        def __init__(self, *a, **kw):
            captured["tools"] = kw.get("tools") or []

    monkeypatch.setattr(ba, "Agent", _StubAgent)

    async def _no_stream(*a, **kw):
        return ""
    monkeypatch.setattr(agent, "_stream_llm_events", _no_stream)

    async def stream(ev):
        pass

    await agent._execute_llm_driven("q", stream, asyncio.Event(), ctx)
    await captured["tools"][0](query="late")

    assert len(ctx.tool_calls) == 1
    rec = ctx.tool_calls[0]
    assert rec.ok is False and "budget exhausted" in rec.error and rec.args == {"query": "late"}
    assert "web_search" not in ctx.tool_results, "a refusal is not a result"
