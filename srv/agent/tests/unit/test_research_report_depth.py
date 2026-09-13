"""Deep research has to come back deep.

Production, 2026-09-11/12: two consented deep-research passes — one on the
dredging market, one on the Navier-Stokes claims — each ran for roughly four
minutes, succeeded, and returned an answer of two to three thousand characters.
Nothing errored. The user asked, reasonably, why a multi-minute research tool
produces less than a page when ChatGPT's equivalent produces ten.

Three independent ceilings, each sufficient on its own to cause it:

1. ``output_length`` was never set, so Tavily was asked for a "standard"
   report. The planner passed only ``question`` and a hard-coded ``model``.
2. ``_build_synthesis_context`` cut the report at a hard-coded ``[:12000]``
   before the synthesis model read it — most of a long report, discarded
   silently, after the user had already waited for it.
3. ``CHAT_SYSTEM_PROMPT`` rule 8 ("Mobile-Friendly Responses… Avoid long walls
   of text") applies to every turn, including research synthesis. Correct for
   chat; wrong here.

These tests pin each one, and pin that ordinary chat turns keep the concise
mobile behaviour — the fix is meant to be research-only.
"""

import pytest

from app.agents.base_agent import (
    MAX_TOOL_RESULT_CHARS,
    RESEARCH_MERMAID_DIRECTIVE,
    RESEARCH_SYNTHESIS_DIRECTIVE,
    TOOL_CLASSES,
    TOOL_RESULT_CHAR_LIMITS,
    AgentContext,
    _has_research_report,
    _tool_result_char_limit,
)
from app.agents.chat_agent import ChatAgent
from app.config.settings import get_settings
from app.tools.tavily_tools import DeepResearchOutput, ResearchSource


def _report(chars: int = 30000, status: str = "completed") -> DeepResearchOutput:
    """A research result the size Tavily actually returns for output_length=long."""
    body = "## Finding\n" + ("Substantive researched prose with a citation [1]. " * (chars // 50))
    return DeepResearchOutput(
        success=status == "completed",
        status=status,
        report=body[:chars],
        sources=[ResearchSource(title="Source", url="https://example.com/a")],
        request_id="req-test",
        model="pro",
        elapsed_seconds=236.6,
    )


# ---------------------------------------------------------------------------
# Ceiling 1: the length Tavily is asked for
# ---------------------------------------------------------------------------


def test_long_is_the_configured_default():
    """Deep research is opt-in and slow; someone who waits for it wants depth."""
    assert get_settings().tavily_research_output_length == "long"


def test_planner_leaves_output_length_unset_so_the_setting_applies():
    """The planner must not pin a length — that is how "standard" got locked in.

    Backfilling "standard" here, or hard-coding it in the tool signature, both
    produce the original bug. Absent means "resolve from settings".
    """
    agent = ChatAgent()
    args = agent._normalize_planned_step_args("deep_research", {}, "the dredging market in 2027")
    assert args["question"] == "the dredging market in 2027"
    assert "output_length" not in args


def test_planner_keeps_a_valid_explicit_length():
    agent = ChatAgent()
    args = agent._normalize_planned_step_args(
        "deep_research", {"output_length": "short"}, "a narrow question"
    )
    assert args["output_length"] == "short"


def test_planner_discards_a_bogus_length():
    agent = ChatAgent()
    args = agent._normalize_planned_step_args(
        "deep_research", {"output_length": "enormous"}, "a question"
    )
    assert "output_length" not in args


# ---------------------------------------------------------------------------
# Ceiling 2: how much of the report reaches the model
# ---------------------------------------------------------------------------


def test_a_long_report_survives_into_the_synthesis_prompt():
    """The regression test for report[:12000]."""
    agent = ChatAgent()
    result = _report(30000)
    context = AgentContext(tool_results={"deep_research": result})

    text = agent._build_synthesis_context("what is the state of the dredging market", context)

    # The old cap would have kept 12000 characters of a 30000-character report.
    assert result.report[:29000] in text
    assert "cited research report" in text
    assert "https://example.com/a" in text


def test_the_report_budget_is_configurable_and_generous():
    budget = get_settings().research_report_context_chars
    assert budget >= 30000, "a 'long' Tavily report routinely exceeds 30k characters"


def test_deep_research_is_exempt_from_the_generic_tool_result_cap():
    """The 12k default protects against runaway query results.

    Applied to a tool whose entire output *is* the deliverable, it deletes the
    deliverable. deep_research needs its own budget; nothing else changes.
    """
    assert _tool_result_char_limit("deep_research") > MAX_TOOL_RESULT_CHARS
    assert _tool_result_char_limit("query_data") == MAX_TOOL_RESULT_CHARS
    assert _tool_result_char_limit("document_search") == MAX_TOOL_RESULT_CHARS
    assert set(TOOL_RESULT_CHAR_LIMITS) == {"deep_research"}


# ---------------------------------------------------------------------------
# Ceiling 3: what the model is told to do with it
# ---------------------------------------------------------------------------


def test_research_directive_appears_when_a_report_is_present():
    agent = ChatAgent()
    context = AgentContext(tool_results={"deep_research": _report()})
    text = agent._build_synthesis_context("what is the state of the dredging market", context)
    assert RESEARCH_SYNTHESIS_DIRECTIVE in text


def test_research_directive_overrides_the_mobile_brevity_rule_by_name():
    """A directive that only asks for "more detail" loses to a specific
    formatting rule already sitting in the system prompt. It has to say which
    rule it is displacing."""
    assert "concise and mobile-friendly" in RESEARCH_SYNTHESIS_DIRECTIVE
    assert "does NOT apply" in RESEARCH_SYNTHESIS_DIRECTIVE


def test_research_directive_asks_for_tables():
    """Tables render today: the chat renderer loads remark-gfm."""
    assert "markdown table" in RESEARCH_SYNTHESIS_DIRECTIVE


def test_mermaid_is_off_by_default_because_chat_does_not_render_it():
    """Verified 2026-09-12 against busibox-frontend.

    apps/chat/src/components/chat/themes/marine/Messages.tsx mounts
    ReactMarkdown with remark-gfm and only an `a` component override. With no
    `code` override, a ```mermaid block is shown as raw source inside a <pre>.
    Asking the model for one would put diagram syntax in front of the user.
    The setting exists so it can be turned on the moment the UI wires in the
    MermaidDiagram component that already exists in the portal docs viewer.
    """
    assert get_settings().research_mermaid_enabled is False
    assert "```mermaid" not in RESEARCH_SYNTHESIS_DIRECTIVE
    agent = ChatAgent()
    context = AgentContext(tool_results={"deep_research": _report()})
    text = agent._build_synthesis_context("the dredging market in 2027", context)
    assert "```mermaid" not in text


def test_mermaid_directive_appears_when_enabled(monkeypatch):
    """get_settings() is an lru_cache'd singleton and Settings is not frozen,
    so flipping the attribute on the live instance is the whole setup."""
    monkeypatch.setattr(get_settings(), "research_mermaid_enabled", True)

    agent = ChatAgent()
    context = AgentContext(tool_results={"deep_research": _report()})
    text = agent._build_synthesis_context("the dredging market in 2027", context)
    assert RESEARCH_MERMAID_DIRECTIVE in text


def test_mermaid_directive_constrains_output_to_avoid_broken_diagrams():
    """Models reliably emit invalid Mermaid when left unconstrained, and the
    portal's MermaidDiagram component shows a red error box plus the raw
    source when parsing fails — worse than no diagram."""
    for needle in ("timeline", "flowchart TD", "double quotes", "Skip the diagram"):
        assert needle in RESEARCH_MERMAID_DIRECTIVE, needle


def _timed_out() -> DeepResearchOutput:
    """What a 240s timeout actually returns: no report, only an explanation."""
    return DeepResearchOutput(
        success=False, status="timeout", report="", request_id="req-test",
        error="Research is still running after 240s (task req-test).",
    )


@pytest.mark.parametrize(
    "make_results",
    [
        pytest.param(lambda: {}, id="no-tools"),
        pytest.param(lambda: {"web_search": object()}, id="plain-web-search"),
        pytest.param(lambda: {"deep_research": _timed_out()}, id="research-timed-out"),
    ],
)
def test_ordinary_turns_stay_concise(make_results):
    """The whole point of scoping this to research: a weather question must not
    come back as a ten-page report, and neither must a research pass that
    produced nothing to report on."""
    agent = ChatAgent()
    context = AgentContext(tool_results=make_results())
    text = agent._build_synthesis_context("what's the weather in Boston", context)
    assert RESEARCH_SYNTHESIS_DIRECTIVE not in text


def test_report_detection_is_attribute_based_not_tool_name_based():
    """So a future research tool inherits the behaviour without editing a list."""
    assert _has_research_report({"some_future_research_tool": _report()}) is True
    assert _has_research_report({"deep_research": _timed_out()}) is False
    assert _has_research_report({"deep_research": DeepResearchOutput(success=True, report="   ")}) is False
    assert _has_research_report({}) is False
    assert _has_research_report(None) is False


# ---------------------------------------------------------------------------
# Timeouts
#
# Asking for longer reports makes them slower. A successful production run
# finished at 236.6s against a 240s deadline — three seconds of margin — so
# raising output_length without raising the deadline would have turned working
# research into timeouts.
# ---------------------------------------------------------------------------


def test_the_research_deadline_clears_a_known_real_run():
    """236.6s is measured, not hypothetical, and that was a "standard" report."""
    observed_standard_run_seconds = 236.6
    assert get_settings().tavily_research_timeout_seconds > observed_standard_run_seconds * 1.5


def test_the_outer_tool_timeout_sits_above_the_research_deadline():
    """Two timeouts, nested, that must stay ordered.

    asyncio.wait_for(TOOL_CLASSES[...]["timeout"]) wraps the whole call. If it
    fires first it cancels the task outright, so the tool's own timeout branch
    — which returns "answer from web_search results instead" and logs the
    Tavily request_id — never runs. The user gets a bare tool error instead of
    a usable partial result, and the request_id needed to chase it server-side
    is lost. Ordering these two is not a style preference.
    """
    inner = get_settings().tavily_research_timeout_seconds
    outer = TOOL_CLASSES["deep_research"]["timeout"]
    assert outer > inner, f"outer tool timeout {outer}s must exceed research deadline {inner}s"
    assert outer - inner >= 30, (
        f"only {outer - inner}s of headroom; the create POST and final poll need room "
        "or the outer timeout wins a race it should never enter"
    )


# ---------------------------------------------------------------------------
# The prerequisite
# ---------------------------------------------------------------------------


def test_the_output_ceiling_is_large_enough_to_write_a_report():
    """A ten-page report is roughly 10k tokens. None of the above matters if the
    model is cut off at the ceiling fixed the day before (see
    test_chat_max_tokens.py) — worth an explicit link between the two."""
    import inspect

    source = inspect.getsource(ChatAgent.__init__).replace(" ", "").replace("\n", "")
    assert "max_tokens=32000" in source
