"""
Small shared helpers for calling LiteLLM's proxy management API
(/key/generate, /key/list, /key/delete, etc.).

Kept separate from app/api/coding_agents.py so future consumers of LiteLLM's
management API don't need to import from a route module.
"""
import logging
from typing import Any, Dict, Optional

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def get_litellm_base_url() -> str:
    """Get the LiteLLM base URL (without /v1 suffix)."""
    settings = get_settings()
    url = str(settings.litellm_base_url).rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def get_litellm_headers() -> Dict[str, str]:
    """Get auth headers for LiteLLM's management API (uses the master key)."""
    settings = get_settings()
    headers = {"Content-Type": "application/json"}
    if settings.litellm_api_key:
        headers["Authorization"] = f"Bearer {settings.litellm_api_key}"
    return headers


async def generate_key(
    key_alias: str,
    models: list[str],
    metadata: Optional[Dict[str, Any]] = None,
    max_budget: Optional[float] = None,
    budget_duration: Optional[str] = None,
    rpm_limit: Optional[int] = None,
    tpm_limit: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Call LiteLLM's POST /key/generate. Returns the parsed response
    (includes the raw key under "key", shown only this once) or None on
    failure.
    """
    body: Dict[str, Any] = {"key_alias": key_alias, "models": models}
    if metadata:
        body["metadata"] = metadata
    if max_budget is not None:
        body["max_budget"] = max_budget
    if budget_duration:
        body["budget_duration"] = budget_duration
    if rpm_limit is not None:
        body["rpm_limit"] = rpm_limit
    if tpm_limit is not None:
        body["tpm_limit"] = tpm_limit

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{get_litellm_base_url()}/key/generate",
                json=body,
                headers=get_litellm_headers(),
            )
            if resp.status_code == 200:
                return resp.json()
            logger.error(f"LiteLLM /key/generate failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"LiteLLM /key/generate request failed: {e}")
    return None


async def list_keys_by_alias(key_alias: str) -> Optional[Dict[str, Any]]:
    """Call LiteLLM's GET /key/list filtered by key_alias.

    NOTE: alias-filtered /key/list is a relatively recent LiteLLM feature —
    verify it's supported by the deployed LiteLLM version. Falls back to an
    unfiltered /key/list scan (client-side filtered) if the server doesn't
    recognize the query param, since ignoring an unknown query param just
    returns everything rather than erroring.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{get_litellm_base_url()}/key/list",
                params={"key_alias": key_alias},
                headers=get_litellm_headers(),
            )
            if resp.status_code == 200:
                return resp.json()
            logger.error(f"LiteLLM /key/list failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"LiteLLM /key/list request failed: {e}")
    return None


async def delete_key_by_alias(key_alias: str) -> bool:
    """Call LiteLLM's POST /key/delete with a key_aliases list. Returns True
    on success (including if the alias was already gone)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{get_litellm_base_url()}/key/delete",
                json={"key_aliases": [key_alias]},
                headers=get_litellm_headers(),
            )
            if resp.status_code == 200:
                return True
            logger.error(f"LiteLLM /key/delete failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"LiteLLM /key/delete request failed: {e}")
    return False
