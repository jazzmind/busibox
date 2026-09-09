"""Attachment routing, prompt-echo guard and no-text attachments.

Production, 2026-09-09: a user sent a one-page scanned PDF with no text and
the chat replied "What are the missing profile fields you need me to
gather?" — the fast-ack classifier answered the last line of its own prompt.
The follow-up "what's the attached" arrived with no attachment (files are
per message) and the agent denied a file existed. These tests pin the fixes.
"""

import json
import logging

import pytest

from app.agents.base_agent import AgentContext
from app.agents.chat_agent import (
    ChatAgent,
    FastAckDecision,
    _is_attachment_only_message,
    _looks_like_prompt_echo,
    _mentions_attachment,
)
from app.services.attachment_resolver import AttachmentResolver

PDF = {"id": "att-1", "file_id": "f-1", "filename": "2021 C&D Canal P&S Notes WMH.pdf", "mime_type": "application/pdf"}
CARRIED = {**PDF, "carried_forward": True}


# ---------------------------------------------------------------------------
# Routing: attachments never take the fast path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attachment_rule_bypasses_classifier(monkeypatch):
    agent = ChatAgent()
    context = AgentContext(attachment_metadata=[PDF])

    async def _must_not_run(*args, **kwargs):
        raise AssertionError("classifier must not run for attachment messages")

    monkeypatch.setattr(agent, "_generate_fast_ack", _must_not_run)

    decision = await agent._route_intent("Attached document", context)

    assert decision.needs_tools is True
    assert decision.action_type == "analysis"
    assert decision.routing_source == "attachment_rule"
    assert decision.follow_up_question is None


def test_current_turn_attachments_are_always_in_focus():
    context = AgentContext(attachment_metadata=[PDF])
    assert ChatAgent._attachments_in_focus("what's the weather", context) == [PDF]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("what's the attached", True),
        ("summarize this document", True),
        ("what does it say", True),
        ("what's the weather in boston", False),
        ("how much vacation do I have left", False),
    ],
)
def test_carried_forward_attachment_only_when_referenced(query, expected):
    context = AgentContext(attachment_metadata=[CARRIED])
    in_focus = ChatAgent._attachments_in_focus(query, context)
    assert bool(in_focus) is expected
    assert _mentions_attachment(query) is expected


@pytest.mark.parametrize(
    "query,expected",
    [
        ("", True),
        ("   ", True),
        ("Attached document", True),
        ("attached document.", True),
        ("2021 C&D Canal P&S Notes WMH.pdf", True),
        ("what's in the pdf", False),
        ("compare this with last year's notes", False),
    ],
)
def test_attachment_only_message_detection(query, expected):
    assert _is_attachment_only_message(query, [PDF]) is expected


def test_attachment_only_detection_requires_attachments():
    assert _is_attachment_only_message("", []) is False


def test_default_objective_names_the_file():
    context = AgentContext(attachment_metadata=[PDF])
    objective = ChatAgent._default_attachment_objective(context)
    assert "2021 C&D Canal P&S Notes WMH.pdf" in objective
    assert objective.lower().startswith("summarize the attached document")


# ---------------------------------------------------------------------------
# Fast-ack prompt hygiene and echo guard
# ---------------------------------------------------------------------------


def test_fast_ack_context_ends_with_user_message_and_omits_profile_scaffolding():
    agent = ChatAgent()
    context = AgentContext(
        insights_enabled=True,
        missing_profile_fields=["role", "department"],
        pending_questions=[{"content": "What is your role at the company?"}],
        attachment_metadata=[CARRIED],
    )

    prompt = agent._build_fast_ack_context("what's the attached", context)

    assert "profile" not in prompt.lower()
    assert "role, department" not in prompt
    assert prompt.rstrip().endswith("Current user message: what's the attached")
    assert "sent earlier in this conversation" in prompt


@pytest.mark.parametrize(
    "text,expected",
    [
        ("What are the missing profile fields you need me to gather?", True),
        ("Set action_type to search.", True),
        ("Let me look into that for you.", False),
        ("Which project is this for?", False),
        (None, False),
    ],
)
def test_prompt_echo_detection(text, expected):
    assert _looks_like_prompt_echo(text) is expected


class _EchoingClient:
    """Fake LLM that answers the prompt scaffolding instead of the user."""

    async def chat_completion(self, **kwargs):
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "action_type": "clarify",
                        "needs_tools": False,
                        "response": "I can help with that.",
                        "follow_up_question": "What are the missing profile fields you need me to gather?",
                        "confidence": 0.9,
                        "complexity": "simple",
                    })
                }
            }]
        }


@pytest.mark.asyncio
async def test_fast_ack_discards_prompt_echo(monkeypatch, caplog):
    agent = ChatAgent()
    context = AgentContext(insights_enabled=True, missing_profile_fields=["role"])
    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: _EchoingClient())
    monkeypatch.setattr("app.agents.chat_agent.ToolRegistry.has", lambda name: True)

    with caplog.at_level(logging.WARNING):
        decision = await agent._generate_fast_ack("how do I submit for reimbursement", context)

    assert decision.routing_source == "heuristic_fallback"
    assert "profile fields" not in (decision.follow_up_question or "")
    assert "profile fields" not in decision.response
    assert any("echoed" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# Planner: attachment-only questions need no tool step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_fallback_has_no_steps_for_attachment_only_query(monkeypatch):
    agent = ChatAgent()
    context = AgentContext(attachment_metadata=[PDF])

    class FailingClient:
        async def chat_completion(self, **kwargs):
            raise RuntimeError("planner unavailable")

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: FailingClient())
    monkeypatch.setattr("app.agents.chat_agent.ToolRegistry.has", lambda name: name in {"web_search", "document_search"})
    monkeypatch.setattr(
        "app.agents.chat_agent.ToolRegistry.get",
        lambda name: (lambda query, limit=5: None) if name == "document_search" else (lambda **kw: None),
    )

    plan = await agent._generate_plan(
        query=ChatAgent._default_attachment_objective(context),
        context=context,
        dispatch=FastAckDecision(action_type="analysis", needs_tools=True, response="Reviewing."),
    )

    assert plan.steps == []
    assert "attached" in plan.summary.lower()


# ---------------------------------------------------------------------------
# Resolver: processed document with no text is labelled, not faked
# ---------------------------------------------------------------------------


class _NoTextClient:
    async def request(self, method: str, path: str, **kwargs):
        if "/markdown" in path:
            return {"markdown": "   "}
        return {"status": {"stage": "completed"}}


@pytest.mark.asyncio
async def test_completed_document_without_text_is_marked_no_text():
    resolver = AttachmentResolver()
    events = []

    async def collector(event):
        events.append(event)

    result = await resolver._resolve_document(
        client=_NoTextClient(),
        file_id="f-1",
        filename=PDF["filename"],
        query="summary",
        available_tokens=5000,
        stream=collector,
        attachment=PDF,
    )

    assert result["source_kind"] == "no_text"
    assert result["content"] == ""
    assert result["mime_type"] == "application/pdf"
    assert any((e.data or {}).get("phase") == "attachment_no_text" for e in events)


def test_attachment_section_explains_scanned_pdf():
    agent = ChatAgent()
    context = AgentContext(
        resolved_attachments=[{
            "filename": PDF["filename"],
            "source_kind": "no_text",
            "mime_type": "application/pdf",
            "content": "",
            "carried_forward": True,
        }]
    )

    section = "\n".join(agent._build_attachment_context_section(context))

    assert "scanned" in section
    assert "do not guess" in section
    assert "sent earlier in this conversation" in section
    assert "[Attachment:" not in section
