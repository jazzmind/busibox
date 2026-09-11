"""Purpose -> model resolution read from LiteLLM rather than the registry.

Production, 2026-09-11: `/etc/litellm/config.yaml` on the LiteLLM host resolves
`chat` to bedrock/claude-sonnet-4-5 and `research` to bedrock/claude-sonnet-5,
while `model_registry.yml` still maps both to a local Qwen. The admin UI
re-points purposes at runtime (POST /llm/purposes -> /model/new) and never
writes back to git, so the registry is a bootstrap mapping and LiteLLM is the
only source of truth.

The trap this file mostly guards: a local vLLM model is registered as
`openai/<name>`, so the provider prefix alone says nothing — only the
`api_base` distinguishes our own GPUs from OpenAI's.
"""

import pytest

from app.services import model_capabilities as mc


@pytest.fixture(autouse=True)
def _clean_cache():
    mc.reset()
    yield
    mc.reset()


# The real production payload, shaped as /model/info returns it.
PROD = [
    {"model_name": "chat",
     "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                        "aws_region_name": "us-east-1"},
     "model_info": {"max_input_tokens": 200000}},
    {"model_name": "research",
     "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-5"},
     "model_info": {"max_input_tokens": 1000000}},
    {"model_name": "fast",
     "litellm_params": {"model": "openai/qwen3.5-0.8b",
                        "api_base": "http://10.96.200.211:8000/v1"},
     "model_info": {"max_input_tokens": 4096}},
    {"model_name": "tool_calling",
     "litellm_params": {"model": "openai/qwen3.6-35b-a3b-fp8",
                        "api_base": "http://10.96.200.211:8001/v1"},
     "model_info": {"max_input_tokens": 65536}},
]


# ---------------------------------------------------------------------------
# provider detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model,api_base", [
    ("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0", None),
    ("bedrock/us.anthropic.claude-sonnet-5", ""),
    ("anthropic/claude-sonnet-4-5", None),
    ("vertex_ai/gemini-2.0", None),
    ("azure/gpt-4o", None),
    ("openai/gpt-4o", None),              # no api_base -> really OpenAI
    ("openai/gpt-4o", "https://api.openai.com/v1"),
])
def test_cloud_models_are_detected(model, api_base):
    assert mc.is_cloud_model(model, api_base) is True


@pytest.mark.parametrize("api_base", [
    "http://10.96.200.211:8000/v1",       # vLLM on the LLM container
    "http://192.168.1.50:8000/v1",
    "http://172.17.0.3:8000/v1",          # docker bridge
    "http://127.0.0.1:8000/v1",
    "http://localhost:8000/v1",
    "http://vllm:8000/v1",                # compose service name
    "http://litellm.local:4000/v1",
])
def test_local_models_are_not_cloud_however_they_are_prefixed(api_base):
    # This is the whole trap: LiteLLM drives local vLLM/MLX with its
    # OpenAI-compatible client, so everything local is named "openai/...".
    assert mc.is_cloud_model("openai/qwen3.6-35b-a3b-fp8", api_base) is False


def test_a_cloud_prefix_beats_a_private_api_base():
    # A bedrock model proxied through something internal is still an API call
    # and must not receive vLLM-only params.
    assert mc.is_cloud_model("bedrock/us.anthropic.claude-sonnet-5",
                             "http://10.96.200.207:4000") is True


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_production_payload_resolves_correctly():
    caps = mc.parse_model_info(PROD)

    assert caps["chat"].is_cloud is True
    assert caps["research"].is_cloud is True
    assert caps["fast"].is_cloud is False
    assert caps["tool_calling"].is_cloud is False
    assert caps["chat"].context_window == 200000
    assert caps["tool_calling"].context_window == 65536


@pytest.mark.parametrize("entry", [
    {},                                                  # empty
    {"model_name": "x"},                                 # no litellm_params
    {"model_name": "", "litellm_params": {"model": "m"}},  # no alias
    {"litellm_params": {"model": "m"}},                  # no alias key
    "not-a-dict",
])
def test_malformed_entries_are_skipped_not_fatal(entry):
    assert mc.parse_model_info([entry]) == {}


def test_a_missing_window_is_none_rather_than_a_guess():
    caps = mc.parse_model_info([
        {"model_name": "mystery", "litellm_params": {"model": "bedrock/something-new"}},
    ])
    assert caps["mystery"].context_window is None
    assert caps["mystery"].is_cloud is True


def test_aliases_are_matched_case_insensitively():
    mc._cache = mc.parse_model_info(PROD)
    assert mc.routes_to_cloud("CHAT") is True
    assert mc.routes_to_cloud("  chat ") is True


# ---------------------------------------------------------------------------
# what callers see
# ---------------------------------------------------------------------------


def test_an_unknown_alias_reads_as_none_so_callers_fall_back():
    mc._cache = mc.parse_model_info(PROD)
    assert mc.routes_to_cloud("frontier") is None
    assert mc.context_window("frontier") is None


def test_a_cold_cache_reads_as_none():
    assert mc.routes_to_cloud("chat") is None
    assert mc.context_window("chat") is None


def test_local_windows_are_not_capped(monkeypatch):
    _cap(monkeypatch, 800000)
    mc._cache = mc.parse_model_info(PROD)
    assert mc.context_window("tool_calling") == 65536


def test_cloud_windows_are_capped(monkeypatch):
    _cap(monkeypatch, 800000)
    mc._cache = mc.parse_model_info(PROD)
    assert mc.context_window("research") == 800000   # 1M clamped
    assert mc.context_window("chat") == 200000       # already under, untouched


def test_the_cap_can_be_disabled(monkeypatch):
    _cap(monkeypatch, 0)
    mc._cache = mc.parse_model_info(PROD)
    assert mc.context_window("research") == 1000000


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_populates_the_cache(monkeypatch):
    async def fake_fetch():
        return PROD

    monkeypatch.setattr(mc, "_fetch_model_info", fake_fetch)
    _ttl(monkeypatch, 600)

    assert await mc.refresh(force=True) == 4
    assert mc.routes_to_cloud("chat") is True


@pytest.mark.asyncio
async def test_an_unreachable_litellm_leaves_the_previous_cache(monkeypatch):
    """A proxy blip must not silently downgrade every model to 'local'."""
    async def ok():
        return PROD

    async def boom():
        raise RuntimeError("connection refused")

    _ttl(monkeypatch, 600)
    monkeypatch.setattr(mc, "_fetch_model_info", ok)
    await mc.refresh(force=True)

    monkeypatch.setattr(mc, "_fetch_model_info", boom)
    assert await mc.refresh(force=True) == 4
    assert mc.routes_to_cloud("chat") is True


@pytest.mark.asyncio
async def test_an_empty_response_does_not_wipe_a_good_cache(monkeypatch):
    async def ok():
        return PROD

    async def empty():
        return []

    _ttl(monkeypatch, 600)
    monkeypatch.setattr(mc, "_fetch_model_info", ok)
    await mc.refresh(force=True)
    monkeypatch.setattr(mc, "_fetch_model_info", empty)

    assert await mc.refresh(force=True) == 4
    assert mc.routes_to_cloud("chat") is True


@pytest.mark.asyncio
async def test_the_ttl_suppresses_a_re_fetch(monkeypatch):
    calls = {"n": 0}

    async def counting():
        calls["n"] += 1
        return PROD

    monkeypatch.setattr(mc, "_fetch_model_info", counting)
    _ttl(monkeypatch, 600)

    await mc.refresh(force=True)
    await mc.refresh()
    await mc.refresh()
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _settings_stub(**kw):
    class S:
        model_capabilities_ttl_seconds = 600
        cloud_context_window_cap = 800000

    for key, value in kw.items():
        setattr(S, key, value)
    return lambda: S()


def _cap(monkeypatch, value):
    monkeypatch.setattr("app.config.settings.get_settings",
                        _settings_stub(cloud_context_window_cap=value))


def _ttl(monkeypatch, value):
    monkeypatch.setattr("app.config.settings.get_settings",
                        _settings_stub(model_capabilities_ttl_seconds=value))
