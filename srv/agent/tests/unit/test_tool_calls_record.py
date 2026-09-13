"""An agent loop needs to call the same tool twice and keep both answers.

``AgentContext.tool_results`` is keyed by tool name, so the second call to
``document_search`` silently overwrote the first. That single line is what
stood between the static plan-once pipeline and an iterative loop, and it is
why query fusion had to fan out inside one ``document_search`` call rather than
as separate steps.

``tool_calls`` is the append-only record; ``tool_results`` keeps its
"latest per tool" meaning so the six by-name readers and ten ``.items()``
iterators elsewhere in the codebase are untouched.
"""

import pytest

from app.agents.base_agent import AgentContext, ToolCallRecord
from app.agents.chat_agent import ChatAgent
from app.tools.document_search_tool import DocumentSearchOutput


def _docs(context_text: str, score: float = 0.8) -> DocumentSearchOutput:
    return DocumentSearchOutput(
        found=True, result_count=1, context=context_text, results=[],
    )


# ---------------------------------------------------------------------------
# The record itself
# ---------------------------------------------------------------------------


def test_second_call_to_same_tool_no_longer_overwrites_the_first():
    """The regression test for the overwrite."""
    ctx = AgentContext()
    ctx.record_tool_call("document_search", {"query": "per diem"}, _docs("first"), 12)
    ctx.record_tool_call("document_search", {"query": "travel policy"}, _docs("second"), 15)

    assert len(ctx.tool_calls) == 2
    assert [c.result.context for c in ctx.calls_for("document_search")] == ["first", "second"]


def test_tool_results_still_means_latest_for_legacy_readers():
    """document_agent / weather_agent / image_agent read tool_results[name]."""
    ctx = AgentContext()
    ctx.record_tool_call("document_search", {}, _docs("first"), 1)
    ctx.record_tool_call("document_search", {}, _docs("second"), 1)
    assert ctx.tool_results["document_search"].context == "second"


def test_failed_calls_are_recorded_but_do_not_touch_tool_results():
    """A timeout must not leave a None where a reader expects a result."""
    ctx = AgentContext()
    ctx.record_tool_call("web_search", {"query": "x"}, _docs("good"), 5)
    rec = ctx.record_tool_call(
        "web_search", {"query": "y"}, None, 30000, ok=False, error="timed out after 30s",
    )
    assert rec.ok is False and rec.error.startswith("timed out")
    assert ctx.tool_results["web_search"].context == "good"
    assert len(ctx.tool_calls) == 2
    assert len(ctx.calls_for("web_search")) == 1


def test_record_carries_provenance():
    ctx = AgentContext()
    rec = ctx.record_tool_call(
        "web_search", {"query": "q"}, _docs("r"), 7, step_id="step_2", source="plan",
    )
    assert isinstance(rec, ToolCallRecord)
    assert (rec.step_id, rec.source, rec.elapsed_ms) == ("step_2", "plan", 7)
    assert rec.args == {"query": "q"}


def test_args_are_copied_not_aliased():
    """A caller mutating its own dict afterwards must not rewrite history."""
    ctx = AgentContext()
    args = {"query": "before"}
    ctx.record_tool_call("web_search", args, _docs("r"), 1)
    args["query"] = "after"
    assert ctx.tool_calls[0].args["query"] == "before"


# ---------------------------------------------------------------------------
# Synthesis sees every call
# ---------------------------------------------------------------------------


def test_synthesis_context_renders_both_calls_with_ordinals():
    agent = ChatAgent()
    ctx = AgentContext()
    ctx.record_tool_call("document_search", {"query": "a"}, _docs("FIRST-ANSWER"), 1)
    ctx.record_tool_call("document_search", {"query": "b"}, _docs("SECOND-ANSWER"), 1)

    text = agent._build_synthesis_context("what is the policy", ctx)

    assert "### document_search #1" in text
    assert "### document_search #2" in text
    assert "FIRST-ANSWER" in text
    assert "SECOND-ANSWER" in text


def test_single_call_keeps_the_plain_heading():
    """No ordinal noise when a tool ran once — the common case is unchanged."""
    agent = ChatAgent()
    ctx = AgentContext()
    ctx.record_tool_call("document_search", {"query": "a"}, _docs("ONLY"), 1)
    text = agent._build_synthesis_context("q", ctx)
    assert "### document_search\n" in text
    assert "#1" not in text


def test_failed_calls_are_not_rendered_as_evidence():
    agent = ChatAgent()
    ctx = AgentContext()
    ctx.record_tool_call("web_search", {}, None, 1, ok=False, error="boom")
    ctx.record_tool_call("document_search", {}, _docs("REAL"), 1)
    text = agent._build_synthesis_context("q", ctx)
    assert "REAL" in text
    assert "### web_search" not in text


def test_legacy_direct_writes_to_tool_results_still_render():
    """status_agent and others populate tool_results without record_tool_call."""
    agent = ChatAgent()
    ctx = AgentContext(tool_results={"document_search": _docs("LEGACY")})
    text = agent._build_synthesis_context("q", ctx)
    assert "LEGACY" in text


def test_llm_response_is_never_rendered_as_a_tool_result():
    """It is the loop's own final answer; the chat agent returns it directly
    and never reaches synthesis when it is present. Rendering it as evidence
    would feed the model its own output back as a source."""
    agent = ChatAgent()
    ctx = AgentContext(tool_results={"llm_response": "the model said this"})
    text = agent._build_synthesis_context("q", ctx)
    assert "### llm_response" not in text
