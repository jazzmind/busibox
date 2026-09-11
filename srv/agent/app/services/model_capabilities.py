"""
What a model purpose actually resolves to, asked of LiteLLM.

`model_registry.yml` is only the bootstrap mapping. Purposes are re-pointed
from the admin UI (`POST /llm/purposes` → LiteLLM `/model/new`), which writes
to LiteLLM's database and never back to git. Production proves the drift: the
registry says `chat` is a local Qwen 35B, while LiteLLM serves it from
`bedrock/us.anthropic.claude-sonnet-4-5`. Anything derived from the registry
at deploy time is therefore stale the moment an admin changes a mapping.

LiteLLM is the one place that knows the truth, and it knows both facts the
agent needs:

- ``litellm_params.model`` — the provider. Note that a local vLLM/MLX model is
  registered as ``openai/<name>`` because LiteLLM drives it with the
  OpenAI-compatible client, so the prefix alone is not enough; a private
  ``api_base`` is what distinguishes local from real OpenAI.
- ``model_info.max_input_tokens`` — the context window.

Reads are synchronous against a cache because ``_routes_to_cloud`` runs during
agent construction. The cache is filled by a startup refresh and re-filled on
a TTL. Cold or unreachable resolves to ``None`` and every caller falls back to
its settings value, which is why those defaults must stay conservative — this
service can improve an answer, never break one.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Providers that are unambiguously an API call, used only to override the
# api_base test below (a Bedrock model reached through an internal gateway is
# still an API call). `openai` is deliberately absent: it is also how every
# local vLLM and MLX model is registered.
#
# This is the one hardcoded list in the file. It is additive — an unlisted
# provider is not assumed local, it just falls through to the api_base test,
# which is the more reliable signal anyway.
_CLOUD_PROVIDERS = (
    "bedrock", "anthropic", "vertex_ai", "azure", "azure_ai",
    "gemini", "cohere", "mistral", "groq", "together_ai",
)
_CLOUD_PREFIXES = tuple(f"{p}/" for p in _CLOUD_PROVIDERS)

# How many purpose -> purpose hops to follow (cleanup -> chat -> the model).
_MAX_ALIAS_HOPS = 4

# RFC1918, loopback, and bare/internal hostnames — an api_base pointing at one
# of these is our own hardware however the model is prefixed.
_PRIVATE_HOST_RE = re.compile(
    r"^(?:"
    r"10\.\d+\.\d+\.\d+"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+"
    r"|192\.168\.\d+\.\d+"
    r"|127\.\d+\.\d+\.\d+"
    r"|localhost"
    r"|[a-z0-9][a-z0-9-]*(?:\.(?:local|internal|lan))?"   # bare container names
    r")$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ModelCapability:
    """What LiteLLM reports for one purpose alias."""

    alias: str
    model: str
    is_cloud: bool
    context_window: Optional[int] = None


_cache: Dict[str, ModelCapability] = {}
_fetched_at: float = 0.0
_failed_at: float = 0.0

# After a failed fetch, don't try again for this long. Without it, a LiteLLM
# outage would put a 10s timeout on the front of every chat turn that asks
# for a refresh.
_RETRY_COOLDOWN_SECONDS = 60.0


def _host_of(api_base: str) -> str:
    text = (api_base or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[a-z0-9+.-]+://", "", text, flags=re.IGNORECASE)
    return text.split("/", 1)[0].split(":", 1)[0]


def is_cloud_model(
    model: str,
    api_base: Optional[str] = None,
    provider: Optional[str] = None,
) -> bool:
    """Whether this model is answered by an external API rather than our hardware.

    Order of evidence, most trustworthy first:

    1. ``litellm_provider`` when LiteLLM reports one — its own answer, no
       guessing on our side.
    2. A known cloud provider prefix on the model string.
    3. ``api_base``: a private, loopback or bare host is our own network,
       whatever the model is called. This is what separates local vLLM and MLX
       (registered as ``openai/...`` because LiteLLM drives them with the
       OpenAI-compatible client) from real OpenAI.
    """
    label = (provider or "").strip().lower()
    name = (model or "").strip().lower()
    if label in _CLOUD_PROVIDERS or name.startswith(_CLOUD_PREFIXES):
        return True
    host = _host_of(api_base or "")
    if host:
        return not bool(_PRIVATE_HOST_RE.match(host))
    # No api_base and no cloud marker: real OpenAI if it says openai.
    return label == "openai" or name.startswith("openai/")


def _window_of(entry: Dict[str, Any]) -> Optional[int]:
    info = entry.get("model_info")
    if not isinstance(info, dict):
        return None
    for key in ("max_input_tokens", "max_model_len", "context_window", "max_tokens"):
        value = info.get(key)
        try:
            window = int(value)
        except (TypeError, ValueError):
            continue
        if window > 0:
            return window
    return None


def _is_runtime_entry(entry: Dict[str, Any]) -> bool:
    """True for a mapping written by the admin UI rather than the config file."""
    info = entry.get("model_info")
    return bool(isinstance(info, dict) and info.get("db_model"))


def _strip_provider(model: str) -> str:
    return (model or "").split("/", 1)[-1].strip().lower()


def parse_model_info(entries: List[Dict[str, Any]]) -> Dict[str, ModelCapability]:
    """Turn a LiteLLM ``/model/info`` payload into alias → capability.

    Two details matter, and both mirror how the admin UI reads the same data
    (``_merge_model_entries`` in ``app/api/llm.py``):

    - A purpose re-pointed from the UI is persisted to LiteLLM's database and
      can coexist with a stale entry of the same name from the deployed
      config file. Runtime entries (``model_info.db_model``) win, so the agent
      and the UI always agree on what a purpose means.
    - A purpose may resolve to another purpose rather than to a model —
      ``cleanup`` pointing at ``chat`` is how the UI shows it — so the chain is
      followed to the model that actually answers.
    """
    ordered = [e for e in (entries or []) if isinstance(e, dict)]
    ordered.sort(key=_is_runtime_entry)  # stable: config first, runtime last

    raw: Dict[str, ModelCapability] = {}
    for entry in ordered:
        alias = str(entry.get("model_name") or "").strip()
        if not alias:
            continue
        params = entry.get("litellm_params")
        params = params if isinstance(params, dict) else {}
        model = str(params.get("model") or "")
        if not model:
            continue
        info = entry.get("model_info") if isinstance(entry.get("model_info"), dict) else {}
        raw[alias.lower()] = ModelCapability(
            alias=alias,
            model=model,
            is_cloud=is_cloud_model(model, params.get("api_base"), info.get("litellm_provider")),
            context_window=_window_of(entry),
        )

    return {alias: _follow_aliases(alias, raw) for alias in raw}


def _follow_aliases(alias: str, raw: Dict[str, ModelCapability]) -> ModelCapability:
    """Resolve a purpose that points at another purpose to the real model."""
    capability = raw[alias]
    seen = {alias}
    for _ in range(_MAX_ALIAS_HOPS):
        target = _strip_provider(capability.model)
        if target not in raw or target in seen:
            break
        seen.add(target)
        nested = raw[target]
        # Keep this purpose's own name, take the target's actual capabilities.
        capability = ModelCapability(
            alias=capability.alias,
            model=nested.model,
            is_cloud=nested.is_cloud,
            context_window=capability.context_window or nested.context_window,
        )
    return capability


async def _fetch_model_info() -> List[Dict[str, Any]]:
    """GET LiteLLM /model/info. Kept local to avoid importing app.api.llm."""
    from app.config.settings import get_settings
    settings = get_settings()

    base = str(settings.litellm_base_url).rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    headers = {"Content-Type": "application/json"}
    if settings.litellm_api_key:
        headers["Authorization"] = f"Bearer {settings.litellm_api_key}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{base}/model/info", headers=headers)
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else payload
    return data if isinstance(data, list) else []


async def refresh(force: bool = False) -> int:
    """Re-read the purpose mapping from LiteLLM. Returns how many were learned.

    Never raises: a failure leaves the previous cache (or an empty one) in
    place and callers fall back to settings.
    """
    global _cache, _fetched_at, _failed_at

    from app.config.settings import get_settings
    now = time.monotonic()
    ttl = get_settings().model_capabilities_ttl_seconds
    if not force:
        if _cache and (now - _fetched_at) < ttl:
            return len(_cache)
        # Back off after a failure so an outage can't put a network timeout
        # on the front of every turn.
        if _failed_at and (now - _failed_at) < _RETRY_COOLDOWN_SECONDS:
            return len(_cache)

    try:
        entries = await _fetch_model_info()
    except Exception as exc:  # noqa: BLE001 — never break a turn over this
        _failed_at = time.monotonic()
        logger.warning("model_capabilities: LiteLLM /model/info unavailable: %s", exc)
        return len(_cache)

    parsed = parse_model_info(entries)
    if not parsed:
        _failed_at = time.monotonic()
        logger.warning("model_capabilities: /model/info returned nothing usable")
        return len(_cache)

    _cache = parsed
    _fetched_at = time.monotonic()
    _failed_at = 0.0
    cloud = sorted(c.alias for c in parsed.values() if c.is_cloud)
    logger.info(
        "model_capabilities: %d purposes resolved from LiteLLM (cloud: %s)",
        len(parsed), ",".join(cloud) or "none",
    )
    return len(parsed)


def get(alias: Optional[str]) -> Optional[ModelCapability]:
    """Cached capability for *alias*, or None when unknown or not yet warm."""
    return _cache.get((alias or "").strip().lower()) or None


def routes_to_cloud(alias: Optional[str]) -> Optional[bool]:
    """True/False when LiteLLM knows this alias, None when it doesn't."""
    capability = get(alias)
    return None if capability is None else capability.is_cloud


def context_window(alias: Optional[str]) -> Optional[int]:
    """Context window LiteLLM reports for *alias*, capped for cloud models.

    Sonnet accepts up to a million tokens; a million-token prompt is a bill
    and a slow first token, so cloud windows are clamped while local models
    use what they actually serve.
    """
    capability = get(alias)
    if capability is None or not capability.context_window:
        return None
    if not capability.is_cloud:
        return capability.context_window

    from app.config.settings import get_settings
    cap = get_settings().cloud_context_window_cap
    return min(capability.context_window, cap) if cap > 0 else capability.context_window


def snapshot() -> Dict[str, ModelCapability]:
    """Current cache — for diagnostics and tests."""
    return dict(_cache)


def reset() -> None:
    """Drop the cache (tests)."""
    global _cache, _fetched_at, _failed_at
    _cache = {}
    _fetched_at = 0.0
    _failed_at = 0.0
