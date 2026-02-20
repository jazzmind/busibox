"""
LiteLLM proxy hook to ensure host MLX server is available.

Runs as an async_pre_call_hook before model requests and triggers the
deploy-api MLX ensure endpoint when MLX is not healthy.
"""

import os
import threading
import time
from typing import Dict, Set

# LiteLLM has moved internal logger modules across versions.
# Keep this hook import-compatible so proxy startup never fails on upgrade.
try:
    from litellm.integrations.custom_logger import CustomLogger
except Exception:
    class CustomLogger:  # type: ignore[override]
        pass

_ensure_lock = threading.Lock()
_last_check_at = {"primary": 0.0, "fast": 0.0}
_last_status = {"primary": None, "fast": None}
_TTL_OK_SECONDS = 120.0
_TTL_FAIL_SECONDS = 15.0
_MLX_PRIMARY_PORT = int(os.environ.get("MLX_PORT", "8080"))
_MLX_FAST_PORT = int(os.environ.get("MLX_FAST_PORT", "18081"))
_CONFIG_PATH = os.environ.get("LITELLM_CONFIG_PATH", "/app/config.yaml")
_mlx_models_lock = threading.Lock()
_mlx_models_cache: Dict[str, object] = {"mtime": None, "models": set()}

_FAST_ALIAS_ENV = os.environ.get("MLX_FAST_MODEL_ALIASES", "fast,classify,test")
_FAST_MODEL_ALIASES = {
    alias.strip() for alias in _FAST_ALIAS_ENV.split(",") if alias.strip()
}


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


def _get_mlx_target(data: dict, call_type: str) -> str | None:
    """Decide if this request targets MLX text models, and which server target."""
    if os.environ.get("LLM_BACKEND", "").lower() != "mlx":
        return None

    # Restrict to text completion pathways.
    if call_type not in {"completion", "text_completion"}:
        return None

    requested_model = str(data.get("model", "")).strip()
    if not requested_model:
        return None

    api_base = str(data.get("api_base", "")).lower()
    if "host.docker.internal" in api_base and f":{_MLX_FAST_PORT}" in api_base:
        return "fast"
    if "host.docker.internal" in api_base and f":{_MLX_PRIMARY_PORT}" in api_base:
        return "primary"

    mlx_chat_aliases = _load_mlx_chat_models_from_config()
    if requested_model in mlx_chat_aliases:
        if requested_model in _FAST_MODEL_ALIASES:
            return "fast"
        return "primary"

    requested_lower = requested_model.lower()
    if requested_lower.startswith("openai/mlx-community/"):
        # Exclude non-LLM endpoints served by different MLX stacks/ports.
        if "whisper" in requested_lower or "kokoro" in requested_lower:
            return None
        # If the alias explicitly maps to a fast class, keep it on fast server.
        if requested_model in _FAST_MODEL_ALIASES:
            return "fast"
        return "primary"

    if requested_model in _FAST_MODEL_ALIASES:
        return "fast"
    return None


def _mlx_healthcheck(mlx_port: int) -> bool:
    # Import lazily so missing optional deps never break module import/startup.
    try:
        import httpx  # type: ignore
    except Exception:
        httpx = None

    try:
        if httpx is not None:
            response = httpx.get(
                f"http://host.docker.internal:{mlx_port}/v1/models",
                timeout=3.0,
            )
            return response.status_code == 200
        # Fallback to stdlib if httpx is unavailable.
        import urllib.request
        with urllib.request.urlopen(
            f"http://host.docker.internal:{mlx_port}/v1/models", timeout=3.0
        ) as response:
            return response.status == 200
    except Exception:
        return False


def _check_and_start_mlx(target: str) -> None:
    global _last_check_at, _last_status
    # Import lazily so missing optional deps never break module import/startup.
    try:
        import httpx  # type: ignore
    except Exception:
        httpx = None

    if not _ensure_lock.acquire(blocking=False):
        return

    try:
        mlx_port = int(
            os.environ.get("MLX_FAST_PORT", "18081")
            if target == "fast"
            else os.environ.get("MLX_PORT", "8080")
        )

        if _mlx_healthcheck(mlx_port):
            _last_check_at[target] = time.monotonic()
            _last_status[target] = "ok"
            return

        deploy_api_url = os.environ.get("DEPLOY_API_URL", "").rstrip("/")
        if not deploy_api_url:
            _last_check_at[target] = time.monotonic()
            _last_status[target] = "no_deploy_api"
            return

        token = os.environ.get("LITELLM_API_KEY") or os.environ.get("LITELLM_MASTER_KEY", "")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        if httpx is not None:
            response = httpx.post(
                f"{deploy_api_url}/api/v1/services/mlx/ensure/quick",
                headers=headers,
                json={"target": target},
                timeout=15.0,
            )
            status_code = response.status_code
            json_payload = None
            try:
                json_payload = response.json()
            except Exception:
                json_payload = None
        else:
            import json
            import urllib.request
            req = urllib.request.Request(
                f"{deploy_api_url}/api/v1/services/mlx/ensure/quick",
                data=b"{}",
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15.0) as response:
                status_code = response.status
                raw = response.read().decode("utf-8", errors="replace")
            try:
                json_payload = json.loads(raw) if raw else None
            except Exception:
                json_payload = None

        _last_check_at[target] = time.monotonic()
        if status_code == 200:
            if isinstance(json_payload, dict):
                _last_status[target] = str(json_payload.get("status", "unknown"))
            else:
                _last_status[target] = "unknown"
        else:
            _last_status[target] = "failed"
    except Exception:
        _last_check_at[target] = time.monotonic()
        _last_status[target] = "error"
    finally:
        _ensure_lock.release()


class MLXEnsureHook(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        global _last_check_at, _last_status

        target = _get_mlx_target(data=data, call_type=call_type)
        if target is None:
            return data

        now = time.monotonic()
        status = _last_status.get(target)
        last_check_at = _last_check_at.get(target, 0.0)
        ttl_seconds = _TTL_OK_SECONDS if status == "ok" else _TTL_FAIL_SECONDS
        if now - last_check_at < ttl_seconds:
            return data

        threading.Thread(target=_check_and_start_mlx, args=(target,), daemon=True).start()
        return data


mlx_ensure_hook_instance = MLXEnsureHook()
