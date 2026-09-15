"""Unit tests for completion emails (services/turn_notifications.py).

No network, no database: settings, the email transport and the user
preference lookup are all replaced at the module seams.
"""

from __future__ import annotations

import types
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import pytest

from app.schemas.auth import Principal
from app.services import turn_notifications as tn


@pytest.fixture
def settings(monkeypatch):
    ns = types.SimpleNamespace(
        chat_notify_email_enabled=True,
        chat_notify_min_seconds=120,
        portal_base_url="https://busibox.example.com",
    )
    monkeypatch.setattr(tn, "get_settings", lambda: ns)
    return ns


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,subscribers,elapsed,expected",
    [
        ("completed", 0, 5, True),       # nobody watching → email
        ("completed", 1, 5, False),      # short turn, user attached → no email
        ("completed", 1, 120, True),     # long turn → email even when attached
        ("completed", 2, 119, False),    # just under the threshold, attached
        ("failed", 0, 5, True),          # failures are worth an email too
        ("interrupted", 1, 600, True),
        ("cancelled", 0, 600, False),    # user pressed Stop: they know
    ],
)
def test_should_notify_matrix(settings, status, subscribers, elapsed, expected):
    assert tn.should_notify(status=status, subscribers=subscribers, elapsed_s=elapsed) is expected


def test_should_notify_respects_platform_switch(settings):
    settings.chat_notify_email_enabled = False
    assert tn.should_notify(status="completed", subscribers=0, elapsed_s=999) is False


def test_should_notify_threshold_is_configurable(settings):
    settings.chat_notify_min_seconds = 10
    assert tn.should_notify(status="completed", subscribers=1, elapsed_s=10) is True
    assert tn.should_notify(status="completed", subscribers=1, elapsed_s=9) is False


# ---------------------------------------------------------------------------
# File links and markdown stripping
# ---------------------------------------------------------------------------


def test_file_links_picks_download_links_and_makes_them_absolute():
    answer = (
        "Here you go.\n\n"
        "[Download the spreadsheet: Q3 Budget - Spreadsheet - 2026-09-15.xlsx](/portal/api/media/11111111-1111-1111-1111-111111111111?download=1)\n"
        "![thumb](/portal/api/media/22222222-2222-2222-2222-222222222222)\n"
        "[Docs](https://example.com/docs)\n"
    )
    links = tn.file_links(answer, "https://busibox.example.com/")
    assert links == [
        (
            "Q3 Budget - Spreadsheet - 2026-09-15.xlsx",
            "https://busibox.example.com/portal/api/media/11111111-1111-1111-1111-111111111111?download=1",
        )
    ]


def test_file_links_keeps_absolute_urls_and_strips_other_prefixes():
    answer = "[Download the slides: Deck.pptx](https://host/portal/api/media/33333333-3333-3333-3333-333333333333?download=1)"
    assert tn.file_links(answer, "https://ignored") == [
        ("Deck.pptx", "https://host/portal/api/media/33333333-3333-3333-3333-333333333333?download=1")
    ]
    assert tn.file_links("", "https://x") == []


def test_strip_markdown_flattens_formatting():
    text = "# Title\n\nSome **bold** and `code` and a [link](https://x).\n\n---\n\n> quote\n\n\n\n![img](/a.png)"
    out = tn._strip_markdown(text)
    assert "Title" in out and "**" not in out and "`" not in out and "#" not in out
    assert "link" in out and "https://x" not in out
    assert "![img]" not in out and "---" not in out
    assert "\n\n\n" not in out


# ---------------------------------------------------------------------------
# Email content
# ---------------------------------------------------------------------------


def _build(**overrides) -> tuple:
    kwargs: Dict[str, Any] = dict(
        status="completed",
        conversation_title="Dredging market outlook",
        conversation_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        answer_text="## Summary\n\nDemand is **up**.\n\n[Download the document: Outlook - Research Report - 2026-09-15.docx](/portal/api/media/44444444-4444-4444-4444-444444444444?download=1)",
        error_text=None,
        elapsed_s=494,
        base_url="https://busibox.example.com",
    )
    kwargs.update(overrides)
    return tn.build_email(**kwargs)


def test_build_email_completed_has_subject_link_preview_and_files():
    subject, text, html_body = _build()
    assert subject == "Busibox: your answer is ready — Dredging market outlook"
    assert "https://busibox.example.com/chat?conversation=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in text
    assert "about 8 minutes" in text
    assert "Demand is up." in text and "**" not in text.split("Files:")[0]
    assert "Outlook - Research Report - 2026-09-15.docx: https://busibox.example.com/portal/api/media/44444444-4444-4444-4444-444444444444?download=1" in text
    assert "<a href=\"https://busibox.example.com/portal/api/media/44444444-4444-4444-4444-444444444444?download=1\">" in html_body
    assert "Open the conversation" in html_body
    assert "turn these emails off" in text


def test_build_email_short_turn_omits_duration():
    _subject, text, _html = _build(elapsed_s=42)
    assert "minute" not in text


def test_build_email_failed_and_interrupted_wording():
    subject, text, _ = _build(status="failed", error_text="model timeout", answer_text="")
    assert subject.startswith("Busibox: your answer could not be completed")
    assert "Error: model timeout" in text

    subject, text, _ = _build(status="interrupted", answer_text="partial")
    assert "was interrupted" in subject
    assert "ask again to rerun" in text


def test_build_email_escapes_html_and_truncates_preview():
    long_answer = "<script>alert(1)</script> " + ("word " * 400)
    _s, text, html_body = _build(answer_text=long_answer, conversation_title="")
    assert "&lt;script" in html_body and "<script" not in html_body
    assert "your chat" in text  # empty title falls back
    assert "…" in text  # preview truncated


# ---------------------------------------------------------------------------
# maybe_notify end to end (transport stubbed)
# ---------------------------------------------------------------------------


class _Sent:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.success = True

    async def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(success=self.success, error=None if self.success else "smtp down")


@pytest.fixture
def transport(monkeypatch, settings):
    import app.services.email_service as email_service
    import app.services.chat_turns as chat_turns

    sent = _Sent()
    monkeypatch.setattr(email_service, "send_email", sent.send_email)
    prefs = {"value": True}

    async def _pref(user_id, use_test_db=False):
        return prefs["value"]

    monkeypatch.setattr(chat_turns, "user_notify_preference", _pref)

    # maybe_notify records notified_at through app.db.session.get_session_context; stub it.
    import app.db.session as dbs

    recorded = {}

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _Session:
        async def execute(self, *_a, **_k):
            recorded["queried"] = True
            return _Result()

        async def commit(self):
            pass

    @asynccontextmanager
    async def _ctx(use_test_db=False):
        yield _Session()

    monkeypatch.setattr(dbs, "get_session_context", _ctx)
    sent.prefs = prefs
    sent.recorded = recorded
    return sent


def _principal(email: Optional[str] = "peter@example.com") -> Principal:
    return Principal(sub="user-1", email=email, roles=[], scopes=[])


async def _notify(subscribers: int = 0, status: str = "completed", principal: Optional[Principal] = None) -> bool:
    return await tn.maybe_notify(
        turn_id=uuid.uuid4(),
        status=status,
        principal=principal or _principal(),
        conversation_id=uuid.uuid4(),
        conversation_title="Test",
        answer_text="Done.",
        error_text=None,
        elapsed_s=30,
        subscribers=subscribers,
    )


async def test_maybe_notify_sends_when_nobody_is_attached(transport):
    assert await _notify(subscribers=0) is True
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["to"] == "peter@example.com"
    assert call["subject"].startswith("Busibox: your answer is ready")
    assert "Done." in call["body"] and "<html>" in call["html_body"]
    assert transport.recorded.get("queried") is True


async def test_maybe_notify_skips_when_attached_and_short(transport):
    assert await _notify(subscribers=1) is False
    assert transport.calls == []


async def test_maybe_notify_skips_without_email_claim(transport):
    assert await _notify(subscribers=0, principal=_principal(email=None)) is False
    assert transport.calls == []


async def test_maybe_notify_respects_user_opt_out(transport):
    transport.prefs["value"] = False
    assert await _notify(subscribers=0) is False
    assert transport.calls == []


async def test_maybe_notify_reports_transport_failure(transport):
    transport.success = False
    assert await _notify(subscribers=0) is False
    assert len(transport.calls) == 1
