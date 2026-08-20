"""
Platform Configuration — reads global feature flags from config-api.

Uses the public config endpoint (no auth required) to fetch platform-wide
settings like insights_enabled. Values are cached in memory and refreshed
on startup and periodically.
"""

import asyncio
import logging
from typing import Literal, Optional

import httpx

logger = logging.getLogger(__name__)

_config_api_url: Optional[str] = None
_cached_insights_enabled: bool = True
_cached_chat_model_routing_mode: "ChatModelRoutingMode" = "local"
_refresh_task: Optional[asyncio.Task] = None
_REFRESH_INTERVAL_SECONDS = 60

ChatModelRoutingMode = Literal["local", "auto", "frontier"]
_VALID_CHAT_MODEL_ROUTING_MODES = {"local", "auto", "frontier"}


def _parse_bool(value: str) -> bool:
    return value.lower() in ("true", "1", "yes")


async def _fetch_public_config() -> dict:
    """Fetch all public-tier config entries from config-api."""
    if not _config_api_url:
        return {}
    url = f"{_config_api_url.rstrip('/')}/config/public"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data.get("config", {})
    except Exception as e:
        logger.warning(f"Failed to fetch platform config from {url}: {e}")
        return {}


async def refresh_platform_config() -> None:
    """Refresh cached platform config from config-api."""
    global _cached_insights_enabled, _cached_chat_model_routing_mode
    config = await _fetch_public_config()
    if "insights_enabled" in config:
        _cached_insights_enabled = _parse_bool(config["insights_enabled"])
        logger.debug(f"Platform config refreshed: insights_enabled={_cached_insights_enabled}")
    routing_mode = str(config.get("chat_model_routing_mode", "")).lower()
    if routing_mode in _VALID_CHAT_MODEL_ROUTING_MODES:
        _cached_chat_model_routing_mode = routing_mode  # type: ignore[assignment]
        logger.debug("Platform config refreshed: chat_model_routing_mode=%s", routing_mode)
    elif routing_mode:
        logger.warning(
            "Ignoring invalid chat_model_routing_mode=%r; keeping %s",
            routing_mode,
            _cached_chat_model_routing_mode,
        )


async def _periodic_refresh() -> None:
    """Background loop that refreshes config every N seconds."""
    while True:
        await asyncio.sleep(_REFRESH_INTERVAL_SECONDS)
        try:
            await refresh_platform_config()
        except Exception as e:
            logger.warning(f"Periodic platform config refresh failed: {e}")


async def init_platform_config(config_api_url: Optional[str]) -> None:
    """
    Initialize platform config. Call during app startup (lifespan).

    Fetches initial values and starts a background refresh loop.
    """
    global _config_api_url, _refresh_task
    _config_api_url = config_api_url
    if not _config_api_url:
        logger.info("No CONFIG_API_URL configured; platform config defaults apply")
        return

    await refresh_platform_config()
    _refresh_task = asyncio.create_task(_periodic_refresh())
    logger.info(f"Platform config initialized from {_config_api_url}")


async def shutdown_platform_config() -> None:
    """Cancel background refresh. Call during app shutdown."""
    global _refresh_task
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
        _refresh_task = None


def get_platform_insights_enabled() -> bool:
    """Return the cached insights_enabled platform flag."""
    return _cached_insights_enabled


def get_chat_model_routing_mode() -> ChatModelRoutingMode:
    """Return the cached, administrator-controlled chat routing policy."""
    return _cached_chat_model_routing_mode
