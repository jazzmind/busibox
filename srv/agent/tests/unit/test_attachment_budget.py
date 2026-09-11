"""The attachment context budget follows the model that writes the answer.

Before this, `AttachmentResolver` assumed a 12,000-token window for every
turn. Synthesis runs on Claude Sonnet via Bedrock (200k), so a document that
would have fitted whole was being cut to RAG chunks — and for a "summarize
this" objective the chunk ranker has nothing meaningful to match on, so the
answer was written from a sixth of the document.
"""

import pytest

from app.config.settings import Settings
from app.services.attachment_resolver import AttachmentResolver


def _settings(**kw) -> Settings:
    base = dict(database_url="postgresql+asyncpg://u:p@localhost/db")
    base.update(kw)
    return Settings(**base)


# What Ansible renders on a production (vLLM) host, from model_registry.yml.
# 'chat' is a local 35B there, not a cloud model — the same alias resolves to
# 16384 on an MLX dev box, which is why nothing is hardcoded in the service.
PROD_MAP = (
    "default:65536,agent:65536,chat:65536,research:65536,tool_calling:65536,"
    "fast:4096,classify:4096,frontier:200000,frontier-fast:200000,fallback:200000"
)


# ---------------------------------------------------------------------------
# alias -> window
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias,expected", [
    ("chat", 65536),
    ("CHAT", 65536),           # case-insensitive
    ("  chat  ", 65536),       # tolerant of padding
    ("frontier", 200000),
    ("fast", 4096),
])
def test_known_aliases_resolve_to_their_window(alias, expected):
    assert _settings(model_context_windows=PROD_MAP).get_model_context_window(alias) == expected


def test_the_shipped_default_claims_nothing():
    """The service ships no map: the binding is per-backend and Ansible owns it.

    A hardcoded default would be wrong on some backend, and wrong *upward*
    means overrunning a local model's max_model_len.
    """
    s = _settings()
    assert s.model_context_windows == ""
    for alias in ("chat", "agent", "frontier", "tool_calling"):
        assert s.get_model_context_window(alias) == 12000


@pytest.mark.parametrize("alias", ["", None, "some-model-nobody-listed"])
def test_unknown_aliases_fall_back_to_the_conservative_default(alias):
    assert _settings(model_context_windows=PROD_MAP).get_model_context_window(alias) == 12000


@pytest.mark.parametrize("mapping", [
    "chat:not-a-number",
    "chat:0",
    "chat:-5",
    "chat",          # no colon at all
])
def test_a_broken_map_can_only_shrink_the_budget(mapping):
    # A misconfiguration must never produce a window larger than the model
    # really has, so every parse failure lands on the default.
    assert _settings(model_context_windows=mapping).get_model_context_window("chat") == 12000


def test_the_default_itself_is_configurable():
    s = _settings(default_context_window_tokens=4096)
    assert s.get_model_context_window("nope") == 4096


# ---------------------------------------------------------------------------
# budget arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_large_window_yields_a_correspondingly_large_budget(monkeypatch):
    """The whole point: 200k in, roughly 200k of budget out."""
    captured = {}

    resolver = AttachmentResolver()

    async def fake_resolve_document(*, available_tokens, **kwargs):
        captured["available_tokens"] = available_tokens
        return {"filename": "x", "source_kind": "full_markdown", "content": "c"}

    monkeypatch.setattr(resolver, "_resolve_document", fake_resolve_document)
    _stub_auth(monkeypatch)

    await resolver.resolve(
        query="summarize this",
        attachment_metadata=[{"id": "a", "file_id": "f", "filename": "x.txt"}],
        principal=_principal(),
        user_id="u1",
        context_window_tokens=200000,
    )

    # window - reserve(2000) - history(0) - query tokens
    assert captured["available_tokens"] > 190000


@pytest.mark.asyncio
async def test_omitting_the_window_preserves_the_old_behaviour(monkeypatch):
    captured = {}
    resolver = AttachmentResolver()

    async def fake_resolve_document(*, available_tokens, **kwargs):
        captured["available_tokens"] = available_tokens
        return {"filename": "x", "source_kind": "full_markdown", "content": "c"}

    monkeypatch.setattr(resolver, "_resolve_document", fake_resolve_document)
    _stub_auth(monkeypatch)

    await resolver.resolve(
        query="summarize this",
        attachment_metadata=[{"id": "a", "file_id": "f", "filename": "x.txt"}],
        principal=_principal(),
        user_id="u1",
    )

    assert 9000 < captured["available_tokens"] <= 12000


@pytest.mark.asyncio
async def test_history_still_eats_into_the_budget(monkeypatch):
    captured = {}
    resolver = AttachmentResolver()

    async def fake_resolve_document(*, available_tokens, **kwargs):
        captured["available_tokens"] = available_tokens
        return {"filename": "x", "source_kind": "full_markdown", "content": "c"}

    monkeypatch.setattr(resolver, "_resolve_document", fake_resolve_document)
    _stub_auth(monkeypatch)

    await resolver.resolve(
        query="q",
        attachment_metadata=[{"id": "a", "file_id": "f", "filename": "x.txt"}],
        principal=_principal(),
        user_id="u1",
        context_token_estimate=50000,
        context_window_tokens=200000,
    )

    assert 145000 < captured["available_tokens"] < 150000


# ---------------------------------------------------------------------------
# the uncapped inline branch
# ---------------------------------------------------------------------------


def test_inline_text_within_budget_is_untouched():
    text = "short enough"
    out, truncated = AttachmentResolver()._cap_inline_text(text, 10000)
    assert out == text and truncated is False


def test_oversized_inline_text_is_cut_to_the_ceiling():
    # 2M characters ~ 500k tokens: this previously went into the prompt whole.
    out, truncated = AttachmentResolver()._cap_inline_text("x" * 2_000_000, 200000)
    assert truncated is True
    assert len(out) < 200_000  # ceiling is 24k tokens, not the 200k budget
    assert out.endswith("...]")


def test_a_small_budget_wins_over_the_ceiling():
    out, truncated = AttachmentResolver()._cap_inline_text("x" * 2_000_000, 2000)
    assert truncated is True
    assert len(out) < 20_000


@pytest.mark.asyncio
async def test_parsed_content_attachments_are_capped_end_to_end(monkeypatch):
    resolver = AttachmentResolver()
    _stub_auth(monkeypatch)

    resolved = await resolver.resolve(
        query="summarize this",
        attachment_metadata=[{
            "id": "a",
            "filename": "Pasted text.txt",
            "parsed_content": "y" * 2_000_000,
        }],
        principal=_principal(),
        user_id="u1",
        context_window_tokens=200000,
    )

    assert len(resolved) == 1
    assert resolved[0]["source_kind"] == "parsed_content"
    assert resolved[0]["truncated"] is True
    assert len(resolved[0]["content"]) < 200_000


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _Principal:
    token = "tok"


def _principal():
    return _Principal()


def _stub_auth(monkeypatch):
    """Short-circuit token exchange and the data-api client."""
    class _Result:
        access_token = "data-token"

    async def fake_exchange(**kwargs):
        return _Result()

    monkeypatch.setattr(
        "app.auth.token_exchange.exchange_token_zero_trust", fake_exchange, raising=False
    )
    monkeypatch.setattr(
        "app.services.attachment_resolver.BusiboxClient", lambda token: object()
    )
