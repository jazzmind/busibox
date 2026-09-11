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


def test_litellm_s_own_provider_label_is_trusted_first():
    # No prefix, no api_base — only LiteLLM's label says what this is.
    assert mc.is_cloud_model("us.anthropic.claude-sonnet-5", None, "bedrock") is True
    assert mc.is_cloud_model("qwen3.6-35b", "http://10.96.200.211:8001/v1", "openai") is False


# ---------------------------------------------------------------------------
# the admin UI is the source of truth
# ---------------------------------------------------------------------------


def test_a_purpose_repointed_from_the_ui_beats_the_deployed_config():
    """The UI writes to LiteLLM's DB; config.yaml keeps its stale entry.

    Production showed both: config.yaml said chat -> sonnet-4-5 while the UI
    showed chat -> sonnet-5. The DB entry is what LiteLLM serves.
    """
    caps = mc.parse_model_info([
        {"model_name": "chat",
         "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"},
         "model_info": {"max_input_tokens": 200000}},                      # config file
        {"model_name": "chat",
         "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-5"},
         "model_info": {"db_model": True, "max_input_tokens": 1000000}},   # admin UI
    ])
    assert caps["chat"].model == "bedrock/us.anthropic.claude-sonnet-5"
    assert caps["chat"].context_window == 1000000


def test_a_load_balanced_purpose_budgets_for_the_smaller_arm():
    """Production had `chat` live twice: sonnet-4-5 (200k) and sonnet-5 (1M).

    LiteLLM's router balances across both, so a turn budgeted at the 1M arm's
    window would be rejected whenever it landed on the 200k one. Until the
    duplicate is deduped, the safe budget is the smaller.
    """
    caps = mc.parse_model_info([
        {"model_name": "chat",
         "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"},
         "model_info": {"max_input_tokens": 200000}},
        {"model_name": "chat",
         "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-5"},
         "model_info": {"db_model": True, "max_input_tokens": 1000000}},
    ])
    assert caps["chat"].context_window == 200000          # not 1_000_000
    assert caps["chat"].model == "bedrock/us.anthropic.claude-sonnet-5"   # UI's intent


def test_a_purpose_split_across_local_and_cloud_is_treated_as_cloud():
    """Production had `cleanup` live as both a local Qwen and Bedrock Sonnet 5.

    Whichever way it is called, one arm rejects the other's parameters. Cloud
    is the safer read: sending vLLM-only params to Bedrock is the documented
    400 this whole setting exists to prevent.
    """
    caps = mc.parse_model_info([
        {"model_name": "cleanup",
         "litellm_params": {"model": "openai/qwen3.6-35b-a3b-fp8",
                            "api_base": "http://10.96.200.211:8001/v1"}},
        {"model_name": "cleanup",
         "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-5"},
         "model_info": {"db_model": True, "max_input_tokens": 1000000}},
    ])
    assert caps["cleanup"].is_cloud is True


def test_identical_duplicates_are_not_warned_about(caplog):
    """LiteLLM re-adds the config entry on every deploy; when it matches the
    DB entry that is noise, not a problem."""
    import logging
    with caplog.at_level(logging.WARNING):
        mc.parse_model_info([
            {"model_name": "vision",
             "litellm_params": {"model": "openai/qwen3.6-35b-a3b-fp8",
                                "api_base": "http://10.96.200.211:8001/v1"}},
            {"model_name": "vision",
             "litellm_params": {"model": "openai/qwen3.6-35b-a3b-fp8",
                                "api_base": "http://10.96.200.211:8001/v1"},
             "model_info": {"db_model": True}},
        ])
    assert "vision" not in caplog.text


def test_differing_duplicates_are_warned_about(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        mc.parse_model_info([
            {"model_name": "fast", "litellm_params": {"model": "openai/qwen3.5-0.8b",
                                                      "api_base": "http://10.96.200.211:8000/v1"}},
            {"model_name": "fast", "litellm_params": {"model": "openai/qwen3.6-35b-a3b-fp8",
                                                      "api_base": "http://10.96.200.211:8001/v1"},
             "model_info": {"db_model": True}},
        ])
    assert "fast" in caplog.text and "dedup" in caplog.text


def test_order_within_the_payload_does_not_decide_it():
    entries = [
        {"model_name": "chat", "litellm_params": {"model": "bedrock/new"},
         "model_info": {"db_model": True}},
        {"model_name": "chat", "litellm_params": {"model": "bedrock/stale"}},
    ]
    assert mc.parse_model_info(entries)["chat"].model == "bedrock/new"
    assert mc.parse_model_info(list(reversed(entries)))["chat"].model == "bedrock/new"


def test_a_purpose_pointing_at_another_purpose_is_followed():
    """'cleanup -> chat' in the UI means cleanup runs on whatever chat runs on.

    This matters beyond display: cleanup is not in the CLOUD_ROUTED_ALIASES
    default, so without following the chain it would be treated as local and
    sent vLLM-only params to Bedrock.
    """
    caps = mc.parse_model_info([
        {"model_name": "chat",
         "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-5"},
         "model_info": {"max_input_tokens": 200000}},
        {"model_name": "cleanup", "litellm_params": {"model": "chat"}},
    ])
    assert caps["cleanup"].is_cloud is True
    assert caps["cleanup"].model == "bedrock/us.anthropic.claude-sonnet-5"
    assert caps["cleanup"].context_window == 200000
    assert caps["cleanup"].alias == "cleanup"   # keeps its own name


def test_an_alias_cycle_terminates():
    caps = mc.parse_model_info([
        {"model_name": "a", "litellm_params": {"model": "b"}},
        {"model_name": "b", "litellm_params": {"model": "a"}},
    ])
    assert set(caps) == {"a", "b"}   # resolved without hanging


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
async def test_a_failure_backs_off_instead_of_retrying_every_turn(monkeypatch):
    """Without a cooldown, an outage puts a network timeout on every turn.

    The refresh is called once per chat turn, so a down LiteLLM must be tried
    occasionally, not on each message.
    """
    calls = {"n": 0}

    async def always_fails():
        calls["n"] += 1
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mc, "_fetch_model_info", always_fails)
    _ttl(monkeypatch, 600)

    await mc.refresh(force=True)      # the startup attempt
    for _ in range(5):                # five chat turns
        await mc.refresh()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_a_later_success_clears_the_backoff(monkeypatch):
    async def fails():
        raise RuntimeError("down")

    async def works():
        return PROD

    _ttl(monkeypatch, 600)
    monkeypatch.setattr(mc, "_fetch_model_info", fails)
    await mc.refresh(force=True)
    assert mc.routes_to_cloud("chat") is None       # still on fallbacks

    monkeypatch.setattr(mc, "_fetch_model_info", works)
    await mc.refresh(force=True)                     # LiteLLM comes back
    assert mc.routes_to_cloud("chat") is True
    assert mc._failed_at == 0.0


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
# endpoint compatibility
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status=200, payload=None, text_only=False):
        self.status_code = status
        self._payload = payload
        self._text_only = text_only

    def json(self):
        if self._text_only:
            raise ValueError("not JSON")
        return self._payload


class _FakeClient:
    """Records every (method, path) tried and replies from a routing table."""

    def __init__(self, table):
        self.table = table
        self.tried = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kwargs):
        path = url.split("4000", 1)[-1] if "4000" in url else url
        self.tried.append((method, path))
        handler = self.table.get(path)
        if handler is None:
            return _FakeResponse(status=404)
        if isinstance(handler, Exception):
            raise handler
        return handler


def _client(monkeypatch, table):
    holder = {}

    def factory(*args, **kwargs):
        holder["client"] = _FakeClient(table)
        return holder["client"]

    monkeypatch.setattr(mc.httpx, "AsyncClient", factory)
    monkeypatch.setattr("app.config.settings.get_settings", _settings_stub())
    return holder


@pytest.mark.asyncio
async def test_a_422_on_bodyless_get_falls_through_to_the_next_shape(monkeypatch):
    """Some LiteLLM builds reject GET with no body. Production hit exactly this."""
    calls = {"n": 0}

    class Endpoint(_FakeResponse):
        def __init__(self):
            super().__init__()

        @property
        def status_code(self):
            calls["n"] += 1
            return 422 if calls["n"] == 1 else 200

        @status_code.setter
        def status_code(self, v):
            pass

        def json(self):
            return {"data": PROD}

    holder = _client(monkeypatch, {"/model/info": Endpoint()})
    entries = await mc._fetch_model_info()

    assert len(entries) == 4
    assert holder["client"].tried[0] == ("GET", "/model/info")


@pytest.mark.asyncio
async def test_config_yaml_is_used_when_model_info_is_unavailable(monkeypatch):
    config_shaped = [{"model_name": "chat",
                      "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-5"}}]
    holder = _client(monkeypatch, {
        "/config/yaml": _FakeResponse(payload={"model_list": config_shaped}),
    })

    entries = await mc._fetch_model_info()

    assert entries == config_shaped
    tried = [p for _, p in holder["client"].tried]
    assert "/model/info" in tried and "/config/yaml" in tried   # in that order
    # Routing still works from the thinner payload; the window falls back.
    caps = mc.parse_model_info(entries)
    assert caps["chat"].is_cloud is True
    assert caps["chat"].context_window is None


@pytest.mark.asyncio
async def test_every_endpoint_failing_reports_all_of_them(monkeypatch):
    """The raised message is the only evidence in the log — it must name each
    endpoint, not just whichever happened to be tried last."""
    _client(monkeypatch, {})
    with pytest.raises(RuntimeError) as err:
        await mc._fetch_model_info()

    message = str(err.value)
    for path, _ in mc._ENDPOINTS:
        assert path in message, message
    assert "404" in message


@pytest.mark.asyncio
async def test_a_non_json_body_is_reported_alongside_the_rest(monkeypatch):
    _client(monkeypatch, {"/model/info": _FakeResponse(text_only=True)})
    with pytest.raises(RuntimeError) as err:
        await mc._fetch_model_info()

    message = str(err.value)
    assert "not JSON" in message        # the real cause, previously hidden
    assert "/config/yaml" in message    # and what was tried after it


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
