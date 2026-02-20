"""Unit tests for proactive insight gathering helpers."""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models.domain import Conversation, Message
from app.services.insights_generator import get_profile_completeness, identify_knowledge_gaps


def _conversation() -> Conversation:
    return Conversation(
        id=uuid.uuid4(),
        title="Profile test",
        user_id="user-123",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def _messages() -> list[Message]:
    return [
        Message(
            role="user",
            content="Can you help me stay on top of tasks?",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ),
        Message(
            role="assistant",
            content="Yes. We can set up a lightweight workflow.",
            conversation_id="conv-123",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ),
    ]


def test_profile_completeness_score_improves_with_more_context():
    empty = get_profile_completeness([])
    enriched = get_profile_completeness(
        [
            {"content": "I live in Austin.", "category": "fact"},
            {"content": "I work as an engineering manager.", "category": "fact"},
            {"content": "Please keep replies concise.", "category": "preference"},
        ]
    )
    assert enriched["score"] > empty["score"]
    assert enriched["required_score"] > empty["required_score"]


@pytest.mark.asyncio
@patch("busibox_common.llm.get_client")
async def test_pending_question_lifecycle(mock_get_client):
    """A pending question is created once and not duplicated while unresolved."""
    mock_get_client.side_effect = RuntimeError("llm unavailable")

    conv = _conversation()
    msgs = _messages()

    first = await identify_knowledge_gaps(
        conversation=conv,
        messages=msgs,
        user_id="user-123",
        existing_insights=[],
    )
    assert first is not None
    assert first.category == "pending_question"

    # Simulate unresolved question persisted in insights.
    second = await identify_knowledge_gaps(
        conversation=conv,
        messages=msgs,
        user_id="user-123",
        existing_insights=[{"content": first.content, "category": "pending_question"}],
    )
    assert second is None

