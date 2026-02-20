"""Reference-query checks for dispatch/action classification."""

import pytest

from app.agents.chat_agent import ChatAgent


@pytest.mark.parametrize(
    "query,expected_action,expected_tools",
    [
        ("hello there", "direct", False),
        ("what's the weather in boston?", "search", True),
        ("search my documents for roadmap", "search", True),
        ("latest news on nvidia earnings", "research", True),
        ("calendar for today", "multi_step", True),
        ("help", "clarify", False),
    ],
)
def test_dispatch_heuristics_reference_queries(query, expected_action, expected_tools):
    """
    Ensure heuristic fallback stays aligned with intended action taxonomy.
    """
    agent = ChatAgent()
    decision = agent._heuristic_fast_ack(query)
    assert decision.action_type == expected_action
    assert decision.needs_tools == expected_tools

