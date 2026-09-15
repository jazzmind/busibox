"""Document intent: requests for a spreadsheet or Word file take the loop path.

The create_spreadsheet / create_document tools carry a typed spec that only
the loop-first path (full tool schemas) can fill; the static planner sees a
one-line signature. So a file request must reach the "complex" tier, whether
the semantic router caught it or the regex guard did.
"""

from pathlib import Path

import pytest

from app.agents.base_agent import AgentContext
from app.agents.chat_agent import ChatAgent, FastAckDecision
from app.services.routing_guards import document_intent_guard
from app.services.semantic_router import SemanticRouter


class _Stream:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)


@pytest.mark.parametrize("query", [
    "make me a spreadsheet of the crew hours by week",
    "put that in an excel file",
    "export those numbers to excel",
    "write this up as a word document I can send",
    "turn the answer into a docx please",
    "build a budget workbook with totals",
    "I need this as a downloadable document",
    "can you create a word doc summarizing the safety plan",
    "turn that into a short slide deck for the ops meeting",
    "make a powerpoint presentation on the bid results",
    "give me 10 slides summarizing the research",
])
def test_file_requests_are_detected(query):
    out = document_intent_guard(query)
    assert out.triggered and out.name == "document_intent"
    assert out.needs_tools is True and out.action_type == "analysis"


@pytest.mark.parametrize("query", [
    "what does the excel file say about Q3",
    "where is the word document from last week",
    "how many holidays do we get",
    "summarize this document",
    "excel file",  # too short to be a request
    "is the spreadsheet up to date",
    "what did the presentation say about safety",
    "how many slides are in the pptx",
])
def test_questions_about_files_are_not_requests_for_files(query):
    assert not document_intent_guard(query).triggered


def test_routes_yaml_sends_file_requests_to_the_complex_tier():
    router = SemanticRouter(config_path=Path(__file__).resolve().parents[2] / "config" / "routes.yaml",
                            embedding_url="http://embedding-api.test:8005")
    route = router._parse_config()["document_generation"]
    assert route.complexity == "complex" and route.needs_tools is True
    assert route.action_type == "analysis"
    assert len(route.utterances) >= 12
    assert any("slide" in u for u in route.utterances)


async def test_guard_lifts_the_turn_to_the_complex_tier():
    agent = ChatAgent()
    decision = FastAckDecision(action_type="search", needs_tools=True, response="On it.", routing_source="llm", complexity="moderate")
    decision = await agent._apply_routing_guards("make me a spreadsheet of the crew hours by week", decision, [], _Stream())
    assert decision.complexity == "complex"
    assert decision.needs_tools is True and decision.preferred_tool is None
    assert decision.routing_source == "llm+document_intent_guard"
    assert agent._loop_first_tier(decision, AgentContext(user_id="u")) == "complex"


async def test_guard_still_lifts_when_the_factual_guard_fired_first():
    """'holidays' trips the factual guard; the file request must still reach the loop."""
    agent = ChatAgent()
    decision = FastAckDecision(action_type="direct", needs_tools=False, response="…", routing_source="llm", complexity="simple")
    decision = await agent._apply_routing_guards("make me a spreadsheet of the company holidays", decision, [], _Stream())
    assert decision.complexity == "complex" and decision.needs_tools is True
    assert decision.routing_source.endswith("factual_guard")


async def test_report_requests_keep_deep_research_precedence():
    """'write a comprehensive report … as a word document' is research first;
    the Word file arrives through the research auto-export, not the tool."""
    agent = ChatAgent()
    decision = FastAckDecision(action_type="search", needs_tools=True, response="x", routing_source="llm")
    decision = await agent._apply_routing_guards(
        "write a comprehensive report on port funding as a word document", decision, [], _Stream())
    assert decision.preferred_tool == "deep_research"
    assert decision.routing_source == "llm+research_intent_guard"
