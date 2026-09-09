"""Tiered grounding policy (services/grounding.py) and its synthesis wiring."""

from datetime import date

import pytest

from app.agents.base_agent import AgentContext
from app.agents.chat_agent import ChatAgent
from app.services.grounding import (
    STATIC_GROUNDING_RULES,
    TIER_ATTACHMENT,
    TIER_DOCUMENTS,
    TIER_ESTIMATE,
    TIER_KNOWLEDGE,
    TIER_WEB,
    assess_grounding,
    grounding_prompt_section,
)
from app.tools.document_search_tool import DocumentSearchOutput, SearchResultItem
from app.tools.web_search_tool import WebSearchOutput, WebSearchResult


def _docs(*scores, dates=None):
    items = [
        SearchResultItem(file_id=f"f{i}", filename=f"doc{i}.pdf", text="t", score=s, page_number=1)
        for i, s in enumerate(scores)
    ]
    if dates:
        items = [dict(item.model_dump(), document_date=d) for item, d in zip(items, dates)]
    return DocumentSearchOutput(found=bool(items), result_count=len(items), context="ctx", results=items)


def _web(n):
    return WebSearchOutput(
        found=n > 0, result_count=n, query="q",
        results=[WebSearchResult(title="t", url=f"https://x/{i}", snippet="s") for i in range(n)],
    )


# ---------------------------------------------------------------------------
# tier selection
# ---------------------------------------------------------------------------


def test_strong_documents_select_documents_tier():
    a = assess_grounding("what is the per diem rate", {"document_search": _docs(0.81, 0.6)})
    assert a.tier == TIER_DOCUMENTS
    assert a.doc_hits == 2 and a.doc_max_score == 0.81
    assert a.doc_files == ["doc0.pdf", "doc1.pdf"]


def test_weak_documents_with_web_select_web_tier():
    a = assess_grounding("who won the bid", {"document_search": _docs(0.52), "web_search": _web(3)})
    assert a.tier == TIER_WEB
    assert a.web_hits == 3
    assert any("weak document hits" in r for r in a.reasons)


def test_nothing_found_and_figure_question_selects_estimate():
    a = assess_grounding("how many holidays do we get per year", {"document_search": _docs()})
    assert a.tier == TIER_ESTIMATE
    assert a.wants_figure is True


def test_nothing_found_general_question_selects_knowledge():
    a = assess_grounding("explain what a bid bond is", {"document_search": _docs(), "web_search": _web(0)})
    assert a.tier == TIER_KNOWLEDGE


def test_attachment_wins_over_documents():
    a = assess_grounding(
        "summarize this",
        {"document_search": _docs(0.9)},
        resolved_attachments=[{"filename": "a.pdf", "source_kind": "full_markdown", "content": "text"}],
    )
    assert a.tier == TIER_ATTACHMENT


def test_no_text_attachment_still_counts_as_attachment():
    a = assess_grounding("summarize this", {}, resolved_attachments=[{"source_kind": "no_text", "content": ""}])
    assert a.tier == TIER_ATTACHMENT


def test_deep_research_report_counts_as_web():
    class Report:
        success = True
        sources = [{"url": "https://a"}, {"url": "https://b"}]
        error = None
    a = assess_grounding("market report", {"deep_research": Report()})
    assert a.tier == TIER_WEB and a.research_report and a.web_hits == 2


def test_strong_threshold_is_configurable():
    a = assess_grounding("q", {"document_search": _docs(0.7)}, strong_doc_score=0.75)
    assert a.tier == TIER_KNOWLEDGE
    assert assess_grounding("q", {"document_search": _docs(0.7)}, strong_doc_score=0.65).tier == TIER_DOCUMENTS


def test_tool_error_is_recorded():
    failed = WebSearchOutput(found=False, result_count=0, query="q", results=[], error="provider down")
    a = assess_grounding("latest news", {"web_search": failed})
    assert a.tool_errors == ["web_search: provider down"]


def test_document_dates_are_collected():
    a = assess_grounding("q", {"document_search": _docs(0.9, 0.8, dates=["2024-03-01", "2025-07-15T10:00:00Z"])})
    assert a.newest_doc_date == "2025-07-15"
    assert a.oldest_doc_date == "2024-03-01"


# ---------------------------------------------------------------------------
# prompt rendering
# ---------------------------------------------------------------------------


def test_prompt_section_flags_stale_documents():
    a = assess_grounding("q", {"document_search": _docs(0.9, dates=["2025-03-14"])})
    text = grounding_prompt_section(a, today=date(2026, 9, 9), stale_after_months=12)
    assert "tier: documents" in text
    assert "2025-03-14" in text
    assert "about 18 months old" in text
    assert "may be outdated" in text
    assert "Absence rule" in text and "Conflict rule" in text


def test_prompt_section_recent_document_not_flagged():
    a = assess_grounding("q", {"document_search": _docs(0.9, dates=["2026-08-01"])})
    text = grounding_prompt_section(a, today=date(2026, 9, 9))
    assert "older than" not in text
    assert "Mention the document date" in text


def test_prompt_section_mentions_tool_failure():
    failed = WebSearchOutput(found=False, result_count=0, query="q", results=[], error="provider down")
    a = assess_grounding("latest news", {"web_search": failed})
    text = grounding_prompt_section(a, today=date(2026, 9, 9))
    assert "search step failed" in text and "provider down" in text


@pytest.mark.parametrize("tier,phrase", [
    (TIER_DOCUMENTS, "Answer ONLY from the company documents"),
    (TIER_WEB, "didn't find this in the company documents"),
    (TIER_ESTIMATE, "Do NOT refuse"),
    (TIER_KNOWLEDGE, "answer from general knowledge"),
    (TIER_ATTACHMENT, "Answer from the attached document"),
])
def test_each_tier_has_its_rule(tier, phrase):
    a = assess_grounding("q", {})
    a.tier = tier
    assert phrase in grounding_prompt_section(a, today=date(2026, 9, 9))


def test_static_rules_cover_the_same_policies():
    for needle in ("Absence rule", "Recency rule", "Conflict rule", "estimate"):
        assert needle in STATIC_GROUNDING_RULES


# ---------------------------------------------------------------------------
# synthesis wiring
# ---------------------------------------------------------------------------


def test_synthesis_context_includes_tier_and_stores_assessment():
    agent = ChatAgent()
    context = AgentContext(tool_results={"document_search": _docs(0.88)})
    text = agent._build_synthesis_context("what is the per diem rate", context)
    assert "## Grounding Policy (tier: documents)" in text
    assert "following the grounding policy" in text
    assert context.grounding["tier"] == "documents"
    assert context.grounding["doc_max_score"] == 0.88


def test_synthesis_context_estimate_tier_when_nothing_found():
    agent = ChatAgent()
    context = AgentContext(tool_results={"document_search": _docs()})
    text = agent._build_synthesis_context("how many vacation days do new hires get", context)
    assert "tier: estimate" in text
    assert context.grounding["tier"] == "estimate"


def test_llm_driven_system_prompt_carries_static_rules():
    agent = ChatAgent()
    prompt = agent._build_enriched_system_prompt(AgentContext())
    assert "## Grounding Policy" in prompt
    assert "Absence rule" in prompt
