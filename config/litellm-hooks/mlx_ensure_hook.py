"""
LiteLLM proxy hook to ensure host MLX server is available.

Runs as an async_pre_call_hook before model requests and triggers the
deploy-api MLX ensure endpoint when MLX is not healthy.
"""

import os
import threading
import time
from typing import Dict, Set

import httpx
from litellm.integrations.custom_logger import CustomLogger

_ensure_lock = threading.Lock()
_last_check_at = 0.0
_last_status = None
_TTL_OK_SECONDS = 120.0
_TTL_FAIL_SECONDS = 15.0
_CONFIG_PATH = os.environ.get("LITELLM_CONFIG_PATH", "/app/config.yaml")
_mlx_models_lock = threading.Lock()
_mlx_models_cache: Dict[str, object] = {"mtime": None, "models": set()}


def _load_mlx_chat_models_from_config() -> Set[str]:
    """
    Load model aliases that route to the host MLX text server (port 8080).

    We only include chat/completion MLX routes so cloud/vLLM models and other
    MLX-adjacent services (e.g. whisper/tts on different ports) do not trigger
    MLX LM auto-start.
    """
    try:
        import yaml  # type: ignore
    except Exception:
        return set()

    if not os.path.exists(_CONFIG_PATH):
        return set()

    try:
        mtime = os.path.getmtime(_CONFIG_PATH)
    except Exception:
        return set()

    with _mlx_models_lock:
        cached_mtime = _mlx_models_cache.get("mtime")
        if cached_mtime == mtime:
            return set(_mlx_models_cache.get("models", set()))

        models: Set[str] = set()
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

            for entry in config.get("model_list", []) or []:
                model_name = entry.get("model_name")
                params = entry.get("litellm_params", {}) or {}
                api_base = str(params.get("api_base", "")).lower()
                routed_model = str(params.get("model", "")).lower()

                # Restrict to the host MLX LM server on 8080.
                if (
                    model_name
                    and "host.docker.internal" in api_base
                    and ":8080" in api_base
                    and "mlx-community" in routed_model
                ):
                    models.add(str(model_name))
        except Exception:
            models = set()

        _mlx_models_cache["mtime"] = mtime
        _mlx_models_cache["models"] = models
        return set(models)


def _should_ensure_mlx(data: dict, call_type: str) -> bool:
    """Decide if this request is targeting an MLX text model."""
    if os.environ.get("LLM_BACKEND", "").lower() != "mlx":
        return False

    # Restrict to text completion pathways.
    if call_type not in {"completion", "text_completion"}:
        return False

    requested_model = str(data.get("model", "")).strip()
    if not requested_model:
        return False

    api_base = str(data.get("api_base", "")).lower()
    if "host.docker.internal" in api_base and ":8080" in api_base:
        return True

    mlx_chat_aliases = _load_mlx_chat_models_from_config()
    if requested_model in mlx_chat_aliases:
        return True

    requested_lower = requested_model.lower()
    if requested_lower.startswith("openai/mlx-community/"):
        # Exclude non-LLM endpoints served by different MLX stacks/ports.
        if "whisper" in requested_lower or "kokoro" in requested_lower:
            return False
        return True

    return False


def _mlx_healthcheck(mlx_port: str) -> bool:
    try:
        response = httpx.get(
            f"http://host.docker.internal:{mlx_port}/v1/models",
            timeout=3.0,
        )
        return response.status_code == 200
    except Exception:
        return False


def _check_and_start_mlx() -> None:
    global _last_check_at, _last_status

    if not _ensure_lock.acquire(blocking=False):
        return

    try:
        mlx_port = os.environ.get("MLX_PORT", "8080")

        if _mlx_healthcheck(mlx_port):
            _last_check_at = time.monotonic()
            _last_status = "ok"
            return

        deploy_api_url = os.environ.get("DEPLOY_API_URL", "").rstrip("/")
        if not deploy_api_url:
            _last_check_at = time.monotonic()
            _last_status = "no_deploy_api"
            return

        token = os.environ.get("LITELLM_API_KEY") or os.environ.get("LITELLM_MASTER_KEY", "")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = httpx.post(
            f"{deploy_api_url}/api/v1/services/mlx/ensure/quick",
            headers=headers,
            timeout=15.0,
        )

        _last_check_at = time.monotonic()
        if response.status_code == 200:
            try:
                _last_status = response.json().get("status", "unknown")
            except Exception:
                _last_status = "unknown"
        else:
            _last_status = "failed"
    except Exception:
        _last_check_at = time.monotonic()
        _last_status = "error"
    finally:
        _ensure_lock.release()


class MLXEnsureHook(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        global _last_check_at, _last_status

        if not _should_ensure_mlx(data=data, call_type=call_type):
            return data

        now = time.monotonic()
        ttl_seconds = _TTL_OK_SECONDS if _last_status == "ok" else _TTL_FAIL_SECONDS
        if now - _last_check_at < ttl_seconds:
            return data

        threading.Thread(target=_check_and_start_mlx, daemon=True).start()
        return data


mlx_ensure_hook_instance = MLXEnsureHook()
