"""
LLM API endpoints for direct model queries and configuration.

Provides:
- Direct chat completions via LiteLLM (supports local MLX and cloud models)
- Model listing from LiteLLM with proper provider labels
- Health checks for LLM backends
- API key management for cloud providers (admin only)
- Cloud model discovery from OpenAI/Anthropic APIs
- Purpose-to-model mapping (read/update)
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_principal
from app.config.settings import get_settings
from app.schemas.auth import Principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])

settings = get_settings()


# =============================================================================
# Request/Response Models
# =============================================================================

class ChatMessage(BaseModel):
    """A single chat message."""
    role: str = Field(..., description="Message role: system, user, or assistant")
    content: str = Field(..., description="Message content")


class CompletionRequest(BaseModel):
    """Request for a chat completion."""
    model: str = Field("agent", description="Model name or purpose")
    messages: List[ChatMessage] = Field(..., description="Chat messages")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, gt=0)
    stream: bool = Field(False, description="Stream the response via SSE")


class CompletionResponse(BaseModel):
    """Response from a chat completion."""
    model: str
    content: str
    usage: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class ModelInfo(BaseModel):
    """Information about an available model."""
    id: str
    provider: Optional[str] = None
    description: Optional[str] = None
    purpose: Optional[str] = None  # If this model is mapped to a purpose
    db_model: bool = False  # True if stored in DB (can be deleted)
    model_id: Optional[str] = None  # LiteLLM model UUID (for deletion)
    actual_model: Optional[str] = None  # The underlying model path


class ModelsResponse(BaseModel):
    """Response listing available models."""
    models: List[ModelInfo]
    purposes: Dict[str, str] = Field(default_factory=dict)  # purpose -> model_name


class HealthResponse(BaseModel):
    """Health status for LLM backends."""
    litellm: bool
    litellm_url: str
    models_available: int = 0


class ProviderKeyRequest(BaseModel):
    """Request to save a cloud provider API key."""
    provider: str = Field(..., description="Provider name: openai, anthropic, bedrock")
    api_key: str = Field(default="", description="API key for the provider (access_key:secret_key or Bedrock API Key for bedrock)")
    # Bedrock-specific fields (alternative to api_key)
    aws_access_key_id: Optional[str] = Field(default=None, description="AWS Access Key ID (Bedrock)")
    aws_secret_access_key: Optional[str] = Field(default=None, description="AWS Secret Access Key (Bedrock)")
    aws_region: Optional[str] = Field(default=None, description="AWS region (Bedrock, defaults to us-east-1)")


class ProviderKeyInfo(BaseModel):
    """Info about a configured provider key (key is masked)."""
    provider: str
    configured: bool
    masked_key: Optional[str] = None


class KeysResponse(BaseModel):
    """Response listing configured provider keys."""
    providers: List[ProviderKeyInfo]


class CloudModel(BaseModel):
    """A model available from a cloud provider."""
    id: str
    name: str
    provider: str
    description: Optional[str] = None
    context_window: Optional[int] = None
    registered: bool = False  # Whether it's already in LiteLLM config


class CloudModelsResponse(BaseModel):
    """Response listing available cloud models."""
    provider: str
    models: List[CloudModel]
    api_key_configured: bool
    needs_key_resave: bool = False  # True when key is in LiteLLM DB but not accessible for API calls
    provider_error: Optional[str] = None  # Error from provider API (key is accessible but API call failed)


class RegisterModelsRequest(BaseModel):
    """Request to register cloud models in LiteLLM."""
    provider: str = Field(..., description="Provider: openai, anthropic, bedrock")
    model_ids: List[str] = Field(..., description="Model IDs to register")


class PurposeMappingUpdate(BaseModel):
    """Request to update a purpose-to-model mapping."""
    purpose: str = Field(..., description="Purpose name (e.g. agent, fast, frontier)")
    model_name: str = Field(..., description="LiteLLM model name to assign")


# =============================================================================
# Cloud Provider Configuration
# =============================================================================

# Provider API base URLs for fetching live model lists
CLOUD_PROVIDER_CONFIG: Dict[str, Dict[str, Any]] = {
    "openai": {
        "models_url": "https://api.openai.com/v1/models",
        "env_var": "OPENAI_API_KEY",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    },
    "anthropic": {
        "models_url": "https://api.anthropic.com/v1/models",
        "env_var": "ANTHROPIC_API_KEY",
        "auth_header": "x-api-key",
        "auth_prefix": "",
    },
    "bedrock": {
        # Bedrock uses AWS credentials (IAM) or a Bedrock API Key (bearer token)
        "env_vars": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION_NAME", "AWS_BEARER_TOKEN_BEDROCK"],
        "env_var": "AWS_ACCESS_KEY_ID",  # Primary env var for IAM detection
    },
}

# Models to exclude from the live lists (internal, deprecated, or not useful)
OPENAI_MODEL_EXCLUDES = {
    "babbage", "davinci", "dall-e", "tts", "whisper", "canary", "audio",
    "embedding", "embed", "moderation", "omni-moderation", "realtime",
    "text-embedding", "codex", "search",
}

ANTHROPIC_MODEL_EXCLUDES = set()  # Anthropic list is already clean

# AWS region to inference profile prefix mapping
# Bedrock requires cross-region inference profiles with a geographic prefix
# See: https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html
BEDROCK_REGION_PREFIX_MAP = {
    # US regions
    "us-east-1": "us", "us-east-2": "us", "us-west-1": "us", "us-west-2": "us",
    # EU regions
    "eu-west-1": "eu", "eu-west-2": "eu", "eu-west-3": "eu",
    "eu-central-1": "eu", "eu-central-2": "eu",
    "eu-north-1": "eu", "eu-south-1": "eu", "eu-south-2": "eu",
    # Asia Pacific regions
    "ap-southeast-1": "ap", "ap-southeast-2": "ap", "ap-southeast-3": "ap",
    "ap-northeast-1": "ap", "ap-northeast-2": "ap", "ap-northeast-3": "ap",
    "ap-south-1": "ap", "ap-south-2": "ap", "ap-east-1": "ap",
    # Canada
    "ca-central-1": "us",
    # South America
    "sa-east-1": "us",
}


def _get_bedrock_region_prefix(region: str = "") -> str:
    """Get the geographic inference profile prefix for a Bedrock region.
    
    Bedrock models require a region prefix (e.g. 'us.', 'eu.', 'ap.')
    for cross-region inference profiles. Falls back to 'us' if unknown.
    """
    if not region:
        import os
        region = os.environ.get("AWS_REGION_NAME", "us-east-1")
    return BEDROCK_REGION_PREFIX_MAP.get(region, "us")


# Curated list of popular Bedrock models (since we don't have boto3 for ListFoundationModels)
# Format: (base_model_id, display_name, description)
# Note: base_model_id does NOT include the region prefix; it's added at runtime
BEDROCK_CURATED_MODELS = [
    # Anthropic Claude on Bedrock
    ("anthropic.claude-sonnet-4-20250514-v1:0", "Claude Sonnet 4", "Anthropic Claude Sonnet 4 (latest)"),
    ("anthropic.claude-3-7-sonnet-20250219-v1:0", "Claude 3.7 Sonnet", "Anthropic Claude 3.7 Sonnet"),
    ("anthropic.claude-3-5-sonnet-20241022-v2:0", "Claude 3.5 Sonnet v2", "Anthropic Claude 3.5 Sonnet v2"),
    ("anthropic.claude-3-5-haiku-20241022-v1:0", "Claude 3.5 Haiku", "Anthropic Claude 3.5 Haiku (fast)"),
    ("anthropic.claude-3-opus-20240229-v1:0", "Claude 3 Opus", "Anthropic Claude 3 Opus"),
    ("anthropic.claude-3-haiku-20240307-v1:0", "Claude 3 Haiku", "Anthropic Claude 3 Haiku"),
    # Amazon Nova
    ("amazon.nova-pro-v1:0", "Nova Pro", "Amazon Nova Pro"),
    ("amazon.nova-lite-v1:0", "Nova Lite", "Amazon Nova Lite"),
    ("amazon.nova-micro-v1:0", "Nova Micro", "Amazon Nova Micro (fast)"),
    ("amazon.nova-2-lite-v1:0", "Nova 2 Lite", "Amazon Nova 2 Lite"),
    ("amazon.nova-2-sonic-v1:0", "Nova 2 Sonic", "Amazon Nova 2 Sonic (speech)"),
    # Meta Llama
    ("meta.llama3-3-70b-instruct-v1:0", "Llama 3.3 70B", "Meta Llama 3.3 70B Instruct"),
    ("meta.llama3-1-405b-instruct-v1:0", "Llama 3.1 405B", "Meta Llama 3.1 405B Instruct"),
    ("meta.llama3-1-70b-instruct-v1:0", "Llama 3.1 70B", "Meta Llama 3.1 70B Instruct"),
    ("meta.llama3-1-8b-instruct-v1:0", "Llama 3.1 8B", "Meta Llama 3.1 8B Instruct"),
    # Mistral
    ("mistral.mistral-large-2407-v1:0", "Mistral Large", "Mistral Large (2407)"),
    ("mistral.mistral-small-2402-v1:0", "Mistral Small", "Mistral Small (2402)"),
    # DeepSeek
    ("deepseek.deepseek-r1-v1:0", "DeepSeek R1", "DeepSeek R1 reasoning model"),
    # Cohere
    ("cohere.command-r-plus-v1:0", "Command R+", "Cohere Command R+"),
    ("cohere.command-r-v1:0", "Command R", "Cohere Command R"),
    # AI21 Labs
    ("ai21.jamba-1-5-large-v1:0", "Jamba 1.5 Large", "AI21 Jamba 1.5 Large"),
    ("ai21.jamba-1-5-mini-v1:0", "Jamba 1.5 Mini", "AI21 Jamba 1.5 Mini"),
]

# Purposes that can be overridden via the UI
CONFIGURABLE_PURPOSES = [
    "fast", "agent", "chat", "frontier", "tool_calling", "test", "default",
    "cleanup", "parsing", "classify",
    "video", "image", "transcribe", "voice",
]


# =============================================================================
# Helper Functions
# =============================================================================

def _get_litellm_base_url() -> str:
    """Get the LiteLLM base URL (without /v1 suffix)."""
    url = str(settings.litellm_base_url).rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def _get_litellm_headers() -> Dict[str, str]:
    """Get auth headers for LiteLLM."""
    headers = {"Content-Type": "application/json"}
    if settings.litellm_api_key:
        headers["Authorization"] = f"Bearer {settings.litellm_api_key}"
    return headers


async def _ensure_media_server(server_name: str, principal: Principal, timeout: float = 120.0) -> None:
    """Ensure an on-demand media server is running before proxying to LiteLLM.

    Uses zero-trust token exchange to obtain a deploy-api scoped token from
    the caller's JWT, then calls deploy-api POST /media/ensure and polls
    GET /media/status until the server is healthy or the timeout expires.
    """
    from app.auth.token_exchange import exchange_token_zero_trust

    deploy_url = (settings.deploy_api_url or "").rstrip("/")
    if not deploy_url:
        logger.debug("No deploy_api_url configured; skipping media ensure for %s", server_name)
        return

    if not principal.token:
        logger.warning("No user token available for media ensure; skipping for %s", server_name)
        return

    exchange_result = await exchange_token_zero_trust(
        subject_token=principal.token,
        target_audience="deploy-api",
        user_id=principal.sub,
        scopes="services:manage",
    )
    if not exchange_result:
        logger.warning("Token exchange for deploy-api failed; skipping media ensure for %s", server_name)
        return

    deploy_token = exchange_result.access_token if hasattr(exchange_result, "access_token") else str(exchange_result)

    headers = {
        "Authorization": f"Bearer {deploy_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{deploy_url}/api/v1/services/media/ensure",
                headers=headers,
                json={"server": server_name},
            )
            if resp.is_success:
                body = resp.json()
                if body.get("running") and not body.get("started"):
                    return
        except Exception as exc:
            logger.warning("media/ensure call failed for %s: %s", server_name, exc)
            return

    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=10.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2.0)
            try:
                resp = await client.get(
                    f"{deploy_url}/api/v1/services/media/status",
                    headers=headers,
                )
                if resp.is_success:
                    data = resp.json()
                    server = (data.get("servers") or {}).get(server_name, {})
                    if server.get("running") and server.get("healthy"):
                        logger.info("Media server %s is healthy", server_name)
                        return
            except Exception:
                pass

    logger.warning("Timed out waiting for media server %s to become healthy", server_name)


async def _litellm_generate_image(
    model: str,
    prompt: str,
    size: str = "1024x1024",
    n: int = 1,
    response_format: Optional[str] = None,
) -> Dict[str, Any]:
    """Proxy an image generation request to LiteLLM /v1/images/generations.
    
    Args:
        model: LiteLLM model name/purpose (e.g. "image")
        prompt: Text prompt for image generation
        size: Output image dimensions (e.g. "1024x1024")
        n: Number of images to generate
        response_format: Optional format hint ("url" or "b64_json").
            Not all models support this (gpt-image-1 ignores it).
            If None, the parameter is omitted and the model returns
            its default format.
    """
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    body: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": n,
    }
    
    # Only include response_format if explicitly requested.
    # gpt-image-1 doesn't support it and LiteLLM may return empty data if passed.
    if response_format is not None:
        body["response_format"] = response_format

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{base_url}/v1/images/generations",
            headers=headers,
            json=body,
        )
        if not resp.is_success:
            error_text = resp.text[:1000]
            logger.error(f"LiteLLM /images/generations failed {resp.status_code}: {error_text}")
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Image generation failed: {error_text}",
            )
        return resp.json()


def _is_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _convert_to_wav(file_bytes: bytes) -> bytes:
    """Convert any audio format to 16-bit PCM WAV using ffmpeg."""
    import subprocess
    import tempfile
    import os

    in_path = out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as inf:
            inf.write(file_bytes)
            in_path = inf.name
        out_path = in_path + ".wav"

        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", in_path,
                "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                out_path,
            ],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")[:500]
            raise RuntimeError(f"ffmpeg failed (rc={result.returncode}): {stderr}")

        with open(out_path, "rb") as f:
            return f.read()
    finally:
        for p in (in_path, out_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


async def _litellm_transcribe_audio(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    model: str = "transcribe",
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """Proxy an audio transcription request to LiteLLM /v1/audio/transcriptions."""
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    mp_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}

    if not _is_wav(file_bytes):
        logger.info(
            "Transcribe: input is not WAV (size=%d, header=%s), converting via ffmpeg",
            len(file_bytes), file_bytes[:4].hex(),
        )
        try:
            file_bytes = _convert_to_wav(file_bytes)
            filename = "recording.wav"
            content_type = "audio/wav"
            logger.info("Transcribe: ffmpeg conversion succeeded, new size=%d", len(file_bytes))
        except Exception as e:
            logger.error("Transcribe: ffmpeg conversion failed: %s", e)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported audio format and conversion failed: {e}",
            )

    logger.info(
        "Transcribe request: filename=%s content_type=%s model=%s size=%d",
        filename, content_type, model, len(file_bytes),
    )

    files = {
        "file": (filename, file_bytes, content_type or "application/octet-stream"),
    }
    data: Dict[str, Any] = {"model": model}
    if language:
        data["language"] = language

    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{base_url}/v1/audio/transcriptions",
            headers=mp_headers,
            data=data,
            files=files,
        )
        if not resp.is_success:
            error_text = resp.text[:1000]
            logger.error(f"LiteLLM /audio/transcriptions failed {resp.status_code}: {error_text}")
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Audio transcription failed: {error_text}",
            )
        return resp.json()


_OPENAI_TO_KOKORO_VOICE: dict[str, str] = {
    "alloy": "af_alloy",
    "echo": "am_echo",
    "fable": "bm_fable",
    "onyx": "am_onyx",
    "nova": "af_nova",
    "shimmer": "af_sky",
}


async def _litellm_text_to_speech(
    model: str,
    input_text: str,
    voice: str = "alloy",
    response_format: str = "mp3",
    speed: float = 1.0,
) -> Tuple[bytes, str]:
    """Proxy a TTS request to LiteLLM /v1/audio/speech and return bytes + MIME."""
    mapped_voice = _OPENAI_TO_KOKORO_VOICE.get(voice, voice)
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    body = {
        "model": model,
        "input": input_text,
        "voice": mapped_voice,
        "response_format": response_format,
        "speed": speed,
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{base_url}/v1/audio/speech",
            headers=headers,
            json=body,
        )
        if not resp.is_success:
            error_text = resp.text[:1000]
            logger.error(f"LiteLLM /audio/speech failed {resp.status_code}: {error_text}")
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Text-to-speech failed: {error_text}",
            )
        if len(resp.content) == 0:
            logger.error("LiteLLM /audio/speech returned empty audio (0 bytes)")
            raise HTTPException(
                status_code=502,
                detail="TTS returned empty audio — the voice name may be unsupported by the backend model",
            )
        content_type = resp.headers.get("content-type", "audio/mpeg")
        return resp.content, content_type


def _require_admin(principal: Principal) -> None:
    """Verify that the principal has admin role."""
    role_names_lower = [r.lower() for r in principal.roles]
    if "admin" not in role_names_lower:
        logger.warning(
            f"Admin check failed for user {principal.sub}. "
            f"Roles: {principal.roles}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required"
        )


def _mask_key(key: str) -> str:
    """Mask an API key for display, showing first 4 and last 4 chars."""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


async def _get_configured_env_vars() -> Dict[str, str]:
    """
    Get environment variables configured in LiteLLM.
    
    Tries multiple strategies:
    1. os.environ (fastest - set when keys are saved via UI in current process)
    2. /config/yaml (LiteLLM config endpoint)
    3. LiteLLM database: LiteLLM_Config.environment_variables
    4. LiteLLM database: LiteLLM_CredentialsTable (credentials stored by /credentials API)
    
    Note: Credentials in the DB are encrypted with LITELLM_SALT_KEY. We can't decrypt
    them from agent-api, but we can detect their presence to report "configured" status.
    """
    # Strategy 1: os.environ (set during key save in current process lifetime)
    import os
    result = {}
    # The agent-api container has ANTHROPIC_API_KEY set to the LiteLLM master key
    # for Claude Agent SDK proxy routing. Exclude values that match the LiteLLM
    # master/API key so they don't falsely report "configured".
    litellm_keys = set()
    if settings.litellm_api_key:
        litellm_keys.add(str(settings.litellm_api_key))
    for var in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY", "AWS_REGION_NAME", "AWS_BEARER_TOKEN_BEDROCK"]:
        val = os.environ.get(var, "")
        if val and val not in litellm_keys:
            result[var] = val
    if result:
        return result
    
    # Strategy 2: /config/yaml
    config = await _get_litellm_config()
    if config and "environment_variables" in config:
        env_from_config = config.get("environment_variables", {})
        if env_from_config:
            return env_from_config
    
    # Strategy 3: Query LiteLLM database for environment_variables config
    if hasattr(settings, "litellm_database_url") and settings.litellm_database_url:
        try:
            import asyncpg
            conn = await asyncpg.connect(str(settings.litellm_database_url))
            try:
                row = await conn.fetchrow(
                    'SELECT param_value FROM "LiteLLM_Config" '
                    "WHERE param_name = 'environment_variables'"
                )
                if row and row["param_value"]:
                    import json
                    stored = row["param_value"]
                    if isinstance(stored, str):
                        stored = json.loads(stored)
                    if isinstance(stored, dict):
                        return stored
            finally:
                await conn.close()
        except ImportError:
            logger.debug("asyncpg not available for direct DB query")
        except Exception as e:
            logger.warning(f"Failed to query LiteLLM DB for env vars: {e}")
    
    # Strategy 4: Check LiteLLM_CredentialsTable for stored credentials.
    # We can't decrypt the values, but we can detect which providers have
    # credentials stored. Return placeholder values so _is_key_configured works.
    if hasattr(settings, "litellm_database_url") and settings.litellm_database_url:
        try:
            import asyncpg
            conn = await asyncpg.connect(str(settings.litellm_database_url))
            try:
                rows = await conn.fetch(
                    'SELECT credential_name FROM "LiteLLM_CredentialsTable"'
                )
                cred_names = {row["credential_name"] for row in rows}
                if "openai_credentials" in cred_names:
                    result["OPENAI_API_KEY"] = "****stored-in-credentials****"
                if "anthropic_credentials" in cred_names:
                    result["ANTHROPIC_API_KEY"] = "****stored-in-credentials****"
                if "bedrock_credentials" in cred_names:
                    result["AWS_ACCESS_KEY_ID"] = "****stored-in-credentials****"
                    result["AWS_SECRET_ACCESS_KEY"] = "****stored-in-credentials****"
            finally:
                await conn.close()
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Failed to check credentials table: {e}")
    
    return result


def _is_key_configured(env_vars: Dict[str, str], env_var_name: str) -> bool:
    """Check if a specific API key is configured (non-empty, non-None)."""
    key_val = env_vars.get(env_var_name, "")
    return bool(key_val and key_val != "" and key_val != "None")


def _looks_like_real_key(value: str) -> bool:
    """Check if a value looks like a real API key vs an encrypted/placeholder value."""
    if not value:
        return False
    if value.startswith("os.environ/"):
        return False
    if value.startswith("encrypted:"):
        return False
    if value.startswith("****stored"):
        return False
    if len(value) < 8:
        return False
    return True


async def _clean_stale_config_env_vars(
    client: "httpx.AsyncClient",
    base_url: str,
    headers: Dict[str, str],
) -> None:
    """Remove environment_variables from LiteLLM_Config to prevent stale encrypted
    values from poisoning LiteLLM's env on its periodic DB reload.

    LiteLLM encrypts env vars with LITELLM_SALT_KEY. If the salt key changes
    (e.g. container recreated with a new master key), old encrypted blobs become
    undecryptable and LiteLLM uses the raw ciphertext as the env var value,
    causing auth failures and DNS errors.

    We use a direct DB query because LiteLLM has no API to delete config entries.
    """
    if not (hasattr(settings, "litellm_database_url") and settings.litellm_database_url):
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(str(settings.litellm_database_url))
        try:
            result = await conn.execute(
                'DELETE FROM "LiteLLM_Config" '
                "WHERE param_name = 'environment_variables'"
            )
            if result and "DELETE" in result:
                logger.info(f"Cleaned stale LiteLLM config environment_variables: {result}")
        finally:
            await conn.close()
    except ImportError:
        logger.debug("asyncpg not available, skipping stale config cleanup")
    except Exception as e:
        logger.warning(f"Failed to clean stale config env vars from DB: {e}")


async def _clean_stale_litellm_db(config_model_names: Optional[List[str]] = None) -> None:
    """Remove stale encrypted data from LiteLLM's database tables.

    LiteLLM encrypts model params (model, api_key, api_base) and credentials
    with LITELLM_SALT_KEY. When the salt key changes (container recreate, key
    rotation), these encrypted blobs become undecryptable. LiteLLM then passes
    raw ciphertext as model identifiers, causing "LLM Provider NOT provided"
    errors and decryption warnings.

    This function cleans:
    1. LiteLLM_ProxyModelTable - model deployments matching config-file names
    2. LiteLLM_Config - stale environment_variables
    3. LiteLLM_CredentialsTable - all stored credentials (re-saved on next key entry)

    Args:
        config_model_names: If provided, only delete model entries whose
            model_name matches these names. If None, skip model table cleanup.
    """
    if not (hasattr(settings, "litellm_database_url") and settings.litellm_database_url):
        return

    try:
        import asyncpg
    except ImportError:
        logger.debug("asyncpg not available, skipping stale LiteLLM DB cleanup")
        return

    try:
        conn = await asyncpg.connect(str(settings.litellm_database_url))
    except Exception as e:
        logger.warning(f"Cannot connect to LiteLLM DB for cleanup: {e}")
        return

    try:
        # 1. Clean config-file model entries from LiteLLM_ProxyModelTable.
        # model_name is the display name (e.g. "default", "agent", "fast").
        # model_id is an auto-generated UUID primary key.
        # These entries will be re-registered fresh with the current salt key.
        if config_model_names:
            try:
                result = await conn.execute(
                    'DELETE FROM "LiteLLM_ProxyModelTable" WHERE model_name = ANY($1::text[])',
                    config_model_names,
                )
                if result and "DELETE" in result and result != "DELETE 0":
                    logger.info(f"Cleaned config-file models from LiteLLM DB: {result}")
            except Exception as e:
                logger.warning(f"Failed to clean LiteLLM_ProxyModelTable: {e}")

        # 2. Clean stale environment_variables from LiteLLM_Config
        try:
            result = await conn.execute(
                'DELETE FROM "LiteLLM_Config" '
                "WHERE param_name = 'environment_variables'"
            )
            if result and "DELETE" in result and result != "DELETE 0":
                logger.info(f"Cleaned stale LiteLLM config env vars: {result}")
        except Exception as e:
            logger.warning(f"Failed to clean LiteLLM_Config: {e}")

        # 3. Clean all credentials — they'll be re-saved when the admin
        # enters keys via the UI. This prevents undecryptable credential
        # blobs from breaking model routing.
        try:
            result = await conn.execute(
                'DELETE FROM "LiteLLM_CredentialsTable"'
            )
            if result and "DELETE" in result and result != "DELETE 0":
                logger.info(f"Cleaned stale LiteLLM credentials: {result}")
        except Exception as e:
            logger.warning(f"Failed to clean LiteLLM_CredentialsTable: {e}")
    finally:
        await conn.close()


async def _get_api_key_for_provider(provider: str) -> Optional[str]:
    """
    Get the actual (decrypted) API key for a cloud provider.
    
    LiteLLM stores keys encrypted in DB via /config/update. But when models
    are called, LiteLLM resolves them from its own env. So we also need the
    raw key for direct provider API calls (listing models).
    
    Strategy:
    1. Check os.environ (fastest, set when keys are saved via UI)
    2. Query LiteLLM config/DB for stored env vars (survives agent-api restarts)
    3. If found in LiteLLM, also populate os.environ for future calls
    
    For Bedrock: we don't need an API key for listing models (we use a curated list),
    but we return a truthy value if credentials are configured so the endpoint
    knows that models can be shown.
    """
    import os
    
    if provider == "bedrock":
        # Check os.environ first
        if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
            return "iam-configured"
        if os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
            return "bearer-configured"
        # Try LiteLLM config/DB
        env_vars = await _get_configured_env_vars()
        if (env_vars.get("AWS_ACCESS_KEY_ID") and _looks_like_real_key(env_vars.get("AWS_ACCESS_KEY_ID", "")) and
            env_vars.get("AWS_SECRET_ACCESS_KEY") and _looks_like_real_key(env_vars.get("AWS_SECRET_ACCESS_KEY", ""))):
            # Restore to os.environ for future calls
            os.environ["AWS_ACCESS_KEY_ID"] = env_vars["AWS_ACCESS_KEY_ID"]
            os.environ["AWS_SECRET_ACCESS_KEY"] = env_vars["AWS_SECRET_ACCESS_KEY"]
            if env_vars.get("AWS_REGION_NAME"):
                os.environ["AWS_REGION_NAME"] = env_vars["AWS_REGION_NAME"]
            logger.info("Restored Bedrock IAM credentials from LiteLLM config to os.environ")
            return "iam-configured"
        if env_vars.get("AWS_BEARER_TOKEN_BEDROCK") and _looks_like_real_key(env_vars.get("AWS_BEARER_TOKEN_BEDROCK", "")):
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = env_vars["AWS_BEARER_TOKEN_BEDROCK"]
            if env_vars.get("AWS_REGION_NAME"):
                os.environ["AWS_REGION_NAME"] = env_vars["AWS_REGION_NAME"]
            logger.info("Restored Bedrock bearer token from LiteLLM config to os.environ")
            return "bearer-configured"
        return None
    
    env_var = CLOUD_PROVIDER_CONFIG.get(provider, {}).get("env_var", "")
    if not env_var:
        return None
    
    # Strategy 1: Check os.environ (set during key save or container startup)
    # Exclude values that match the LiteLLM master/API key (agent-api sets
    # ANTHROPIC_API_KEY to the LiteLLM key for Claude SDK proxy routing).
    val = os.environ.get(env_var, "")
    litellm_key = str(settings.litellm_api_key) if settings.litellm_api_key else ""
    if val and val != litellm_key:
        return val
    
    # Strategy 2: Query LiteLLM config/DB for stored env vars
    # This handles the case where keys were saved via UI but agent-api restarted
    env_vars = await _get_configured_env_vars()
    val = env_vars.get(env_var, "")
    if val and _looks_like_real_key(val):
        # Restore to os.environ so future calls don't need to re-query
        os.environ[env_var] = val
        logger.info(f"Restored {provider} API key from LiteLLM config to os.environ")
        return val
    
    # If not in os.environ and not readable from LiteLLM, the key may be
    # encrypted in LiteLLM's DB. We can't decrypt it.
    # Return None to signal we can't make direct provider API calls.
    return None


async def _fetch_live_openai_models(api_key: str) -> List[CloudModel]:
    """Fetch live model list from OpenAI API."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code != 200:
                logger.warning(
                    f"OpenAI /v1/models returned {resp.status_code}: "
                    f"{resp.text[:300]}"
                )
                return []
            data = resp.json()
            models = []
            for m in data.get("data", []):
                mid = m.get("id", "")
                # Filter out non-chat models (embeddings, tts, dall-e, etc.)
                skip = False
                for excl in OPENAI_MODEL_EXCLUDES:
                    if excl in mid.lower():
                        skip = True
                        break
                if skip:
                    continue
                models.append(CloudModel(
                    id=mid,
                    name=mid,
                    provider="openai",
                    description=f"OpenAI {mid}",
                ))
            # Sort: newest/most capable first
            models.sort(key=lambda x: x.id)
            return models
    except Exception as e:
        logger.warning(f"Failed to fetch OpenAI models: {e}")
        return []


async def _fetch_live_anthropic_models(api_key: str) -> List[CloudModel]:
    """Fetch live model list from Anthropic API."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.anthropic.com/v1/models?limit=100",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            if resp.status_code != 200:
                logger.warning(f"Anthropic /v1/models returned {resp.status_code}")
                return []
            data = resp.json()
            models = []
            for m in data.get("data", []):
                mid = m.get("id", "")
                display = m.get("display_name", mid)
                models.append(CloudModel(
                    id=mid,
                    name=display,
                    provider="anthropic",
                    description=f"Anthropic {display}",
                ))
            return models
    except Exception as e:
        logger.warning(f"Failed to fetch Anthropic models: {e}")
        return []


def _get_bedrock_curated_models() -> List[CloudModel]:
    """Return the curated list of popular Bedrock models with region prefix.
    
    Bedrock doesn't have a simple REST list-models API like OpenAI/Anthropic.
    The official way is via boto3 ListFoundationModels, but we don't require
    boto3 in agent-api. Instead we return a curated list of the most popular
    models with the appropriate geographic region prefix (e.g. 'us.', 'eu.')
    based on the configured AWS region.
    """
    prefix = _get_bedrock_region_prefix()
    models = []
    for base_model_id, display_name, description in BEDROCK_CURATED_MODELS:
        # Bedrock inference profiles require region prefix: us.model-id, eu.model-id, etc.
        model_id = f"{prefix}.{base_model_id}"
        models.append(CloudModel(
            id=model_id,
            name=display_name,
            provider="bedrock",
            description=f"{description} ({prefix})",
        ))
    return models


async def _fetch_live_cloud_models(provider: str, api_key: str) -> List[CloudModel]:
    """Fetch live model list from a cloud provider."""
    if provider == "openai":
        return await _fetch_live_openai_models(api_key)
    elif provider == "anthropic":
        return await _fetch_live_anthropic_models(api_key)
    elif provider == "bedrock":
        return _get_bedrock_curated_models()
    return []


async def _get_litellm_config() -> Optional[Dict[str, Any]]:
    """Fetch current LiteLLM config via /config/yaml."""
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # LiteLLM endpoint compatibility: some versions accept GET with no body,
            # others require a request body and return 422 otherwise.
            attempts = [
                ("GET", None),
                ("GET", {}),
                ("POST", {}),
            ]
            last_status = None
            last_text = ""
            for method, payload in attempts:
                try:
                    request_kwargs: Dict[str, Any] = {"headers": headers}
                    if payload is not None:
                        request_kwargs["json"] = payload
                    resp = await client.request(method, f"{base_url}/config/yaml", **request_kwargs)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict) and data.get("model_list"):
                            return data
                        logger.debug(f"/config/yaml 200 but no model_list (got keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__})")
                    last_status = resp.status_code
                    last_text = resp.text[:200]
                except Exception:
                    continue
            if last_status is not None:
                logger.warning(f"/config/yaml returned {last_status}: {last_text}")
    except Exception as e:
        logger.error(f"Failed to fetch LiteLLM config: {e}")

    # Fallback: read mounted config file directly inside the agent container.
    try:
        import os
        import yaml

        local_paths = [
            "/app/litellm-config.yaml",
            "/app/config.yaml",
            "/app/config/litellm-config.yaml",
            "/srv/config/litellm-config.yaml",
        ]
        for path in local_paths:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if isinstance(data, dict) and data.get("model_list"):
                    logger.info(f"Loaded LiteLLM config from local file fallback: {path}")
                    return data
    except Exception as e:
        logger.warning(f"Local LiteLLM config fallback failed: {e}")

    return None


def _read_local_litellm_config() -> Optional[Dict[str, Any]]:
    """Read the mounted litellm-config.yaml file (sync, no network)."""
    import os
    try:
        import yaml
    except ImportError:
        return None
    for path in [
        "/app/litellm-config.yaml",
        "/app/config.yaml",
    ]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if isinstance(data, dict) and data.get("model_list"):
                    return data
            except Exception:
                continue
    return None


async def sync_config_models_to_litellm() -> None:
    """
    Ensure every model in the mounted litellm-config.yaml is registered
    in LiteLLM's DB.  Called at agent startup so that STORE_MODEL_IN_DB=True
    environments don't silently drop config-file models.

    First cleans stale encrypted data from the DB to prevent undecryptable
    blobs from breaking LiteLLM's model routing (salt key rotation scenario).
    """
    config = _read_local_litellm_config()
    if not config:
        logger.debug("No local litellm config to sync")
        return

    config_models = config.get("model_list", [])
    if not config_models:
        return

    # Collect config-file model names for cleanup
    config_model_names = [
        entry.get("model_name", "")
        for entry in config_models
        if entry.get("model_name")
    ]

    # Clean stale encrypted data from LiteLLM DB before re-registering.
    # This prevents "Unable to decrypt" / "LLM Provider NOT provided" errors
    # that occur when LITELLM_SALT_KEY changes between container recreates.
    await _clean_stale_litellm_db(config_model_names=config_model_names)

    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()

    # After cleanup, re-check what's still registered (cloud models survive)
    existing_names: set = set()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for method, payload in [("GET", None), ("GET", {}), ("POST", {})]:
                try:
                    kw: Dict[str, Any] = {"headers": headers}
                    if payload is not None:
                        kw["json"] = payload
                    resp = await client.request(method, f"{base_url}/model/info", **kw)
                    if resp.status_code == 200:
                        for m in resp.json().get("data", []):
                            existing_names.add(m.get("model_name", ""))
                        break
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"sync_config_models: cannot reach /model/info: {e}")
        return

    registered = 0
    for entry in config_models:
        name = entry.get("model_name", "")
        if not name or name in existing_names:
            continue
        payload = {
            "model_name": name,
            "litellm_params": entry.get("litellm_params", {}),
        }
        info = entry.get("model_info")
        if info:
            payload["model_info"] = info
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{base_url}/model/new",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code == 200:
                    registered += 1
                else:
                    logger.warning(f"sync_config_models: failed to register {name}: {resp.status_code}")
        except Exception as e:
            logger.warning(f"sync_config_models: error registering {name}: {e}")

    if registered:
        logger.info(f"Synced {registered} config-file model(s) into LiteLLM DB")


async def _get_model_info() -> List[Dict[str, Any]]:
    """
    Fetch model info from LiteLLM /model/info endpoint.
    
    This returns rich per-model data including litellm_params and model_info,
    and works regardless of STORE_MODEL_IN_DB setting. It includes both
    config-file and DB-stored models.
    
    Returns list of model info dicts, or empty list on failure.
    """
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            attempts = [
                ("GET", None),
                ("GET", {}),
                ("POST", {}),
            ]
            last_status = None
            for method, payload in attempts:
                try:
                    request_kwargs: Dict[str, Any] = {"headers": headers}
                    if payload is not None:
                        request_kwargs["json"] = payload
                    resp = await client.request(method, f"{base_url}/model/info", **request_kwargs)
                    if resp.status_code == 200:
                        data = resp.json()
                        return data.get("data", [])
                    last_status = resp.status_code
                except Exception:
                    continue
            if last_status is not None:
                logger.warning(f"/model/info returned {last_status}")
    except Exception as e:
        logger.warning(f"Failed to fetch /model/info: {e}")
    return []


async def _get_v1_models() -> List[Dict[str, Any]]:
    """
    Fetch basic model list from /v1/models (OpenAI-compatible).
    Always works, but has minimal metadata.
    """
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base_url}/v1/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", [])
    except Exception as e:
        logger.warning(f"Failed to fetch /v1/models: {e}")
    return []


async def _get_registered_model_names() -> set:
    """Get the set of model_name values currently registered in LiteLLM."""
    # Try /model/info first (richer data)
    model_infos = await _get_model_info()
    if model_infos:
        names = set()
        for entry in model_infos:
            mname = entry.get("model_name", "")
            if mname:
                names.add(mname)
            # Also check litellm_params.model
            model_id = entry.get("litellm_params", {}).get("model", "")
            if model_id:
                names.add(model_id)
                if "/" in model_id:
                    names.add(model_id.split("/", 1)[-1])
        return names
    
    # Fallback to /v1/models
    v1_models = await _get_v1_models()
    return {m.get("id", "") for m in v1_models if m.get("id")}


def _detect_provider(model_name: str, litellm_params: Dict) -> str:
    """Detect the real provider from LiteLLM model config."""
    actual_model = litellm_params.get("model", "")
    api_base = litellm_params.get("api_base", "")
    
    if "host.docker.internal" in api_base or "mlx" in api_base:
        return "mlx (local)"
    elif "vllm" in api_base:
        return "vllm (local)"
    elif actual_model.startswith("bedrock/"):
        return "aws-bedrock"
    elif actual_model.startswith("anthropic/"):
        return "anthropic"
    elif actual_model.startswith("openai/") and "mlx-community" not in actual_model:
        return "openai"
    elif "mlx-community" in actual_model or "mlx" in actual_model.lower():
        return "mlx (local)"
    elif "claude" in model_name.lower() or "claude" in actual_model.lower():
        return "anthropic"
    elif "gpt" in model_name.lower() or "o3" in model_name or "o4" in model_name:
        return "openai"
    else:
        return litellm_params.get("custom_llm_provider", "unknown")


def _strip_provider_prefix(model_id: str) -> str:
    """Strip provider prefix from model ID (e.g., openai/gpt-4.1 -> gpt-4.1)."""
    if "/" in model_id:
        parts = model_id.split("/", 1)
        if parts[0] in ("openai", "bedrock", "anthropic"):
            return parts[1]
    return model_id


def _build_purpose_map(model_entries: List[Dict]) -> Dict[str, str]:
    """
    Build purpose -> underlying model description from model entries.
    
    Accepts entries from either /model/info or /config/yaml model_list.
    """
    purpose_map = {}
    for entry in model_entries:
        name = entry.get("model_name", "")
        params = entry.get("litellm_params", {})
        model_id = params.get("model", "")
        purpose_map[name] = _strip_provider_prefix(model_id)
    return purpose_map


def _merge_model_entries(model_info_entries: List[Dict], config_entries: List[Dict]) -> List[Dict]:
    """
    Merge model entries from /model/info (DB/runtime) and config/model_list (file).

    - Keep config-defined models even if DB only has a subset.
    - Overlay DB metadata (db_model/id) when the same model_name exists.
    """
    by_name: Dict[str, Dict[str, Any]] = {}

    for entry in config_entries:
        name = entry.get("model_name", "")
        if not name:
            continue
        by_name[name] = {
            "model_name": name,
            "litellm_params": dict(entry.get("litellm_params", {}) or {}),
            "model_info": dict(entry.get("model_info", {}) or {}),
        }

    for entry in model_info_entries:
        name = entry.get("model_name", "")
        if not name:
            continue
        if name in by_name:
            merged = by_name[name]
            merged_params = dict(merged.get("litellm_params", {}) or {})
            merged_params.update(dict(entry.get("litellm_params", {}) or {}))
            merged_info = dict(merged.get("model_info", {}) or {})
            merged_info.update(dict(entry.get("model_info", {}) or {}))
            merged["litellm_params"] = merged_params
            merged["model_info"] = merged_info
            by_name[name] = merged
        else:
            by_name[name] = {
                "model_name": name,
                "litellm_params": dict(entry.get("litellm_params", {}) or {}),
                "model_info": dict(entry.get("model_info", {}) or {}),
            }

    return list(by_name.values())


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/models", response_model=ModelsResponse)
async def list_models(
    principal: Principal = Depends(get_principal),
) -> ModelsResponse:
    """
    List available models from LiteLLM with proper provider labels and purpose mapping.
    
    Uses /model/info first (richest data), falls back to /config/yaml,
    then /v1/models as last resort.
    """
    model_infos = await _get_model_info()
    config = await _get_litellm_config()
    config_entries = config.get("model_list", []) if config else []
    merged_entries = _merge_model_entries(model_infos, config_entries)

    if merged_entries:
        models = []
        for entry in merged_entries:
            mname = entry.get("model_name", "")
            params = entry.get("litellm_params", {})
            info = entry.get("model_info", {})
            provider = _detect_provider(mname, params)
            is_db = bool(info.get("db_model", False))
            models.append(ModelInfo(
                id=mname,
                provider=provider,
                description=info.get("description", ""),
                db_model=is_db,
                model_id=info.get("id", "") if is_db else None,
                actual_model=params.get("model", ""),
            ))
        purpose_map = _build_purpose_map(merged_entries)
        return ModelsResponse(models=models, purposes=purpose_map)

    # Fallback: /v1/models (minimal info)
    v1_models = await _get_v1_models()
    if v1_models:
        models = []
        for m in v1_models:
            mid = m.get("id", "unknown")
            models.append(ModelInfo(
                id=mid,
                provider=m.get("owned_by"),
            ))
        return ModelsResponse(models=models, purposes={})
    
    # Nothing available
    logger.error("Could not fetch models from any LiteLLM endpoint")
    return ModelsResponse(models=[], purposes={})


@router.post("/completions", response_model=CompletionResponse)
async def chat_completion(
    request: CompletionRequest,
    principal: Principal = Depends(get_principal),
):
    """
    Direct chat completion via LiteLLM.
    
    Supports local MLX models (via purpose names like 'fast', 'agent', 'frontier')
    and cloud models (via provider-prefixed names like 'gpt-4.1', 'claude-sonnet-4').
    """
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    
    body: Dict[str, Any] = {
        "model": request.model,
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
    }
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_tokens is not None:
        body["max_tokens"] = request.max_tokens
    
    if request.stream:
        body["stream"] = True
        return await _stream_completion(base_url, headers, body)
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            
            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})
            
            return CompletionResponse(
                model=data.get("model", request.model),
                content=message.get("content", ""),
                usage=data.get("usage"),
                finish_reason=choice.get("finish_reason"),
                raw=data,
            )
    except httpx.HTTPStatusError as e:
        error_body = e.response.text
        logger.error(f"LiteLLM completion error {e.response.status_code}: {error_body}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LiteLLM error: {error_body}"
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot connect to LiteLLM service"
        )
    except Exception as e:
        logger.error(f"Completion failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


async def _stream_completion(base_url: str, headers: Dict[str, str], body: Dict[str, Any]):
    """Stream a chat completion as Server-Sent Events."""
    
    async def event_generator():
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=body,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            yield f"{line}\n\n"
                        elif line == "":
                            continue
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/health", response_model=HealthResponse)
async def llm_health(
    principal: Principal = Depends(get_principal),
) -> HealthResponse:
    """Check health of LLM backends."""
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    litellm_healthy = False
    models_count = 0
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for health_path in ["/health/readiness", "/health/liveliness", "/health"]:
                try:
                    health_resp = await client.get(f"{base_url}{health_path}")
                    if health_resp.status_code == 200:
                        litellm_healthy = True
                        break
                except Exception:
                    continue
            
            if litellm_healthy:
                try:
                    models_resp = await client.get(f"{base_url}/v1/models", headers=headers)
                    if models_resp.status_code == 200:
                        data = models_resp.json()
                        models_count = len(data.get("data", []))
                except Exception as e:
                    logger.warning(f"Failed to count models: {e}")
    except httpx.ConnectError as e:
        logger.error(f"Cannot connect to {base_url}: {e}")
    except Exception as e:
        logger.error(f"Health check failed: {e}")
    
    return HealthResponse(
        litellm=litellm_healthy,
        litellm_url=base_url,
        models_available=models_count,
    )


# =============================================================================
# API Key Management
# =============================================================================

@router.post("/keys")
async def save_provider_key(
    request: ProviderKeyRequest,
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    """
    Save a cloud provider API key to LiteLLM.
    
    Uses the /credentials API (forward-compatible) with fallback to
    /config/update for older LiteLLM versions.
    Also sets the key in os.environ so agent-api can use it for
    direct provider API calls (e.g., fetching live model lists).
    
    For Bedrock: accepts api_key (access_key:secret_key format),
    a single Bedrock API Key (base64-encoded bearer token),
    or separate aws_access_key_id + aws_secret_access_key fields.
    
    Admin only.
    """
    _require_admin(principal)
    
    provider = request.provider.lower()
    if provider not in CLOUD_PROVIDER_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {provider}. "
                   f"Supported: {list(CLOUD_PROVIDER_CONFIG.keys())}"
        )
    
    # Build the env_vars dict to save based on provider
    if provider == "bedrock":
        env_vars_to_save = _build_bedrock_env_vars(request)
    else:
        if not request.api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="api_key is required for this provider"
            )
        env_var = CLOUD_PROVIDER_CONFIG[provider]["env_var"]
        env_vars_to_save = {env_var: request.api_key}
    
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    saved = False
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Strategy 1: Use /credentials API (LiteLLM v1.65+)
            # Models reference these via litellm_credential_name in litellm_params.
            # Delete-then-create to avoid stale encrypted data from old salt keys.
            try:
                cred_name = f"{provider}_credentials"
                await client.delete(
                    f"{base_url}/credentials/{cred_name}",
                    headers=headers,
                )
                cred_payload = {
                    "credential_name": cred_name,
                    "credential_info": {
                        "provider": provider,
                        "description": f"{provider.title()} credentials",
                    },
                    "credential_values": env_vars_to_save,
                }
                resp = await client.post(
                    f"{base_url}/credentials",
                    headers=headers,
                    json=cred_payload,
                )
                if resp.status_code == 200:
                    logger.info(f"Saved {provider} key via /credentials API")
                    saved = True
                else:
                    logger.warning(
                        f"/credentials POST returned {resp.status_code}: "
                        f"{resp.text[:200]}"
                    )
            except Exception as e:
                logger.warning(f"/credentials API failed: {e}")
            
            # Strategy 2: Also push via /config/update so LiteLLM sets these
            # as actual environment variables on its periodic DB reload.
            # /credentials is for models that reference credentials by name,
            # /config/update sets them as env vars for all models to use.
            # Clean stale encrypted env vars first to prevent salt-key-mismatch
            # issues where old ciphertext poisons LiteLLM's environment.
            await _clean_stale_config_env_vars(client, base_url, headers)
            try:
                resp = await client.post(
                    f"{base_url}/config/update",
                    headers=headers,
                    json={"environment_variables": env_vars_to_save},
                )
                if resp.status_code == 200:
                    logger.info(f"Also saved {provider} key via /config/update")
                    saved = True
                else:
                    error_text = resp.text[:500]
                    logger.warning(
                        f"/config/update returned {resp.status_code}: {error_text}"
                    )
                    if not saved and "STORE_MODEL_IN_DB" in error_text:
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="LiteLLM requires STORE_MODEL_IN_DB=True. "
                                   "Set this env var on LiteLLM and restart."
                        )
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"/config/update failed: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Key save connection failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to LiteLLM: {str(e)}"
        )
    
    if not saved:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to save key via any LiteLLM API"
        )
    
    # Also set in os.environ so agent-api can use it for direct provider calls
    import os
    for env_key, env_val in env_vars_to_save.items():
        os.environ[env_key] = env_val
    
    # Auto-register video purpose model when OpenAI key is saved
    if provider == "openai":
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                video_entry = {
                    "model_name": "video",
                    "litellm_params": {
                        "model": "openai/sora-2",
                    },
                    "model_info": {
                        "description": "Video generation (OpenAI Sora-2)",
                    },
                }
                resp = await client.post(
                    f"{base_url}/model/new",
                    headers=headers,
                    json=video_entry,
                )
                if resp.status_code == 200:
                    logger.info("Auto-registered 'video' purpose model (sora-2) in LiteLLM")
                else:
                    # 409 or other - model may already exist, that's fine
                    logger.debug(
                        f"video model registration returned {resp.status_code}: "
                        f"{resp.text[:200]}"
                    )
        except Exception as e:
            logger.warning(f"Failed to auto-register video model: {e}")
    
    return {
        "success": True,
        "provider": provider,
        "message": f"{provider.title()} credentials configured successfully"
    }


@router.post("/keys/clean-stale")
async def clean_stale_encrypted_data(
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    """
    Clean stale encrypted data from LiteLLM's database.

    Use this when LiteLLM shows "Unable to decrypt" errors after a salt key
    change. Cleans model entries (config-file models), environment variables,
    and credentials. Config-file models are automatically re-synced; cloud
    models and API keys must be re-entered via the admin UI.

    Admin only.
    """
    _require_admin(principal)

    config = _read_local_litellm_config()
    config_model_names = None
    if config:
        config_model_names = [
            entry.get("model_name", "")
            for entry in config.get("model_list", [])
            if entry.get("model_name")
        ]

    await _clean_stale_litellm_db(config_model_names=config_model_names)

    # Re-sync config-file models with fresh encryption
    resync_count = 0
    if config:
        try:
            await sync_config_models_to_litellm()
            resync_count = len(config_model_names or [])
        except Exception as e:
            logger.warning(f"Failed to re-sync config models after cleanup: {e}")

    return {
        "success": True,
        "cleaned": {
            "config_models": len(config_model_names or []),
            "environment_variables": True,
            "credentials": True,
        },
        "resynced_models": resync_count,
        "message": (
            "Stale encrypted data cleaned. Config-file models re-synced. "
            "Cloud provider API keys must be re-entered in Settings > AI Models."
        ),
    }


def _build_bedrock_env_vars(request: ProviderKeyRequest) -> Dict[str, str]:
    """Build env vars dict for Bedrock from request fields.
    
    Supports three modes:
    1. Separate fields: aws_access_key_id + aws_secret_access_key (+ optional aws_region)
    2. Combined api_key in access_key:secret_key format
    3. Single Bedrock API Key (base64-encoded bearer token, no colon) -> AWS_BEARER_TOKEN_BEDROCK
    """
    env_vars: Dict[str, str] = {}
    
    if request.aws_access_key_id and request.aws_secret_access_key:
        # Mode 1: Separate IAM credential fields
        env_vars["AWS_ACCESS_KEY_ID"] = request.aws_access_key_id
        env_vars["AWS_SECRET_ACCESS_KEY"] = request.aws_secret_access_key
    elif request.api_key:
        if ":" in request.api_key:
            # Mode 2: Combined format (access_key:secret_key)
            parts = request.api_key.split(":", 1)
            env_vars["AWS_ACCESS_KEY_ID"] = parts[0]
            env_vars["AWS_SECRET_ACCESS_KEY"] = parts[1]
        else:
            # Mode 3: Single Bedrock API Key (bearer token)
            # Used with LiteLLM's AWS_BEARER_TOKEN_BEDROCK env var
            env_vars["AWS_BEARER_TOKEN_BEDROCK"] = request.api_key
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bedrock requires either aws_access_key_id + aws_secret_access_key, "
                   "api_key in 'access_key:secret_key' format, or a Bedrock API Key"
        )
    
    # Region (default to us-east-1)
    region = request.aws_region or "us-east-1"
    env_vars["AWS_REGION_NAME"] = region
    
    return env_vars


@router.get("/keys", response_model=KeysResponse)
async def list_provider_keys(
    principal: Principal = Depends(get_principal),
) -> KeysResponse:
    """List which cloud providers have API keys configured. Admin only."""
    _require_admin(principal)
    
    env_vars = await _get_configured_env_vars()
    
    providers_info = []
    for provider, env_var in [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")]:
        configured = _is_key_configured(env_vars, env_var)
        providers_info.append(ProviderKeyInfo(
            provider=provider,
            configured=configured,
            # Keys in DB may be encrypted - just show configured status
            masked_key="****configured****" if configured else None,
        ))
    
    # Bedrock: check IAM credentials or Bedrock API Key (bearer token)
    bedrock_iam_configured = (
        _is_key_configured(env_vars, "AWS_ACCESS_KEY_ID") and
        _is_key_configured(env_vars, "AWS_SECRET_ACCESS_KEY")
    )
    bedrock_apikey_configured = _is_key_configured(env_vars, "AWS_BEARER_TOKEN_BEDROCK")
    bedrock_configured = bedrock_iam_configured or bedrock_apikey_configured
    bedrock_region = env_vars.get("AWS_REGION_NAME", "")
    bedrock_masked = None
    if bedrock_iam_configured:
        access_key = env_vars.get("AWS_ACCESS_KEY_ID", "")
        bedrock_masked = f"{_mask_key(access_key)} (IAM, region: {bedrock_region or 'us-east-1'})"
    elif bedrock_apikey_configured:
        bearer_token = env_vars.get("AWS_BEARER_TOKEN_BEDROCK", "")
        bedrock_masked = f"{_mask_key(bearer_token)} (API Key, region: {bedrock_region or 'us-east-1'})"
    providers_info.append(ProviderKeyInfo(
        provider="bedrock",
        configured=bedrock_configured,
        masked_key=bedrock_masked,
    ))
    
    return KeysResponse(providers=providers_info)


# =============================================================================
# Video Generation Proxy  (Busibox Portal -> Agent API -> LiteLLM -> OpenAI)
# =============================================================================

class VideoCreateRequest(BaseModel):
    """Request to create a video via LiteLLM/OpenAI Sora."""
    model: str = Field("video", description="Video model purpose name (resolved via model registry)")
    prompt: str = Field(..., description="Text prompt for video generation")
    seconds: str = Field(..., description="Duration in seconds (e.g. '4', '8', '12')")
    size: str = Field(..., description="Resolution (e.g. '1920x1080')")
    input_reference_base64: Optional[str] = Field(
        None, description="Base64-encoded reference image (will be converted to file upload)"
    )
    input_reference_filename: Optional[str] = Field(
        None, description="Filename for the reference image"
    )


@router.post("/videos/create")
async def create_video(
    request: VideoCreateRequest,
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    """
    Create a video generation job via LiteLLM (proxied to OpenAI Sora).

    Any authenticated user can call this (not admin-only).
    The OpenAI API key is managed by LiteLLM -- it never leaves this service.
    """
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            # Build multipart form if reference image is provided,
            # otherwise use JSON.
            if request.input_reference_base64:
                import base64 as b64mod
                img_bytes = b64mod.b64decode(request.input_reference_base64)
                fname = request.input_reference_filename or f"reference-{id(request)}.jpg"

                # Multipart: LiteLLM forwards to OpenAI /v1/videos
                files = {
                    "input_reference": (fname, img_bytes, "image/jpeg"),
                }
                data = {
                    "model": request.model,
                    "prompt": request.prompt,
                    "seconds": request.seconds,
                    "size": request.size,
                }
                # Remove Content-Type from headers so httpx sets multipart boundary
                mp_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
                resp = await client.post(
                    f"{base_url}/v1/videos",
                    headers=mp_headers,
                    data=data,
                    files=files,
                )
            else:
                body = {
                    "model": request.model,
                    "prompt": request.prompt,
                    "seconds": request.seconds,
                    "size": request.size,
                }
                resp = await client.post(
                    f"{base_url}/v1/videos",
                    headers=headers,
                    json=body,
                )

            if not resp.is_success:
                error_text = resp.text[:1000]
                logger.error(f"LiteLLM /videos create failed {resp.status_code}: {error_text}")
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Video creation failed: {error_text}",
                )
            return resp.json()
    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot connect to LiteLLM service for video generation",
        )
    except Exception as e:
        logger.error(f"Video create proxy failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get("/videos/{video_id}")
async def get_video_status(
    video_id: str,
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    """
    Retrieve video status from LiteLLM (proxied to OpenAI).

    Any authenticated user can call this.
    """
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{base_url}/v1/videos/{video_id}",
                headers=headers,
            )
            if not resp.is_success:
                error_text = resp.text[:500]
                logger.error(f"LiteLLM /videos/{video_id} failed {resp.status_code}: {error_text}")
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Video status check failed: {error_text}",
                )
            return resp.json()
    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot connect to LiteLLM service",
        )
    except Exception as e:
        logger.error(f"Video status proxy failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get("/videos/{video_id}/content")
async def get_video_content(
    video_id: str,
    principal: Principal = Depends(get_principal),
):
    """
    Stream video content bytes from LiteLLM (proxied to OpenAI).

    Returns the raw video binary. Uses StreamingResponse for memory efficiency.
    Any authenticated user can call this.
    """
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()

    async def _stream():
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "GET",
                f"{base_url}/v1/videos/{video_id}/content",
                headers=headers,
            ) as resp:
                if not resp.is_success:
                    error_text = ""
                    async for chunk in resp.aiter_bytes():
                        error_text += chunk.decode("utf-8", errors="replace")
                    logger.error(
                        f"LiteLLM /videos/{video_id}/content failed "
                        f"{resp.status_code}: {error_text[:500]}"
                    )
                    raise HTTPException(
                        status_code=resp.status_code,
                        detail=f"Video content download failed: {error_text[:500]}",
                    )
                async for chunk in resp.aiter_bytes():
                    yield chunk

    try:
        return StreamingResponse(
            _stream(),
            media_type="video/mp4",
        )
    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot connect to LiteLLM service",
        )
    except Exception as e:
        logger.error(f"Video content proxy failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


# =============================================================================
# Media Proxy Endpoints
# =============================================================================

class ImageCreateRequest(BaseModel):
    """Request to create an image via LiteLLM."""
    model: str = Field("image", description="Image model purpose name (resolved via model registry)")
    prompt: str = Field(..., description="Text prompt for image generation")
    size: str = Field("1024x1024", description="Output image size")
    n: int = Field(1, ge=1, le=4, description="Number of images to generate")
    response_format: Optional[str] = Field(
        None,
        description="Response format (url or b64_json). "
        "Omit for gpt-image-1 models which only support b64_json."
    )


class TTSRequest(BaseModel):
    """Request to generate speech audio from text."""
    model: str = Field("voice", description="Voice model purpose name (resolved via model registry)")
    input: str = Field(..., description="Text to convert to speech")
    voice: str = Field("alloy", description="Voice preset")
    response_format: str = Field("mp3", description="Audio format (mp3, wav, etc.)")
    speed: float = Field(1.0, ge=0.25, le=4.0, description="Speech speed")


@router.post("/images/create")
async def create_image(
    request: ImageCreateRequest,
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    """
    Create image(s) via LiteLLM image generation endpoint.

    Any authenticated user can call this.
    """
    try:
        await _ensure_media_server("image", principal)
        return await _litellm_generate_image(
            model=request.model,
            prompt=request.prompt,
            size=request.size,
            n=request.n,
            response_format=request.response_format,
        )
    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot connect to LiteLLM service for image generation",
        )
    except Exception as e:
        logger.error(f"Image generation proxy failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/audio/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    model: str = Form("transcribe"),
    language: Optional[str] = Form(None),
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    """
    Transcribe uploaded audio via LiteLLM transcription endpoint.

    Any authenticated user can call this.
    """
    try:
        await _ensure_media_server("transcribe", principal)
        file_bytes = await file.read()
        return await _litellm_transcribe_audio(
            file_bytes=file_bytes,
            filename=file.filename or "audio-input.wav",
            content_type=file.content_type or "application/octet-stream",
            model=model,
            language=language,
        )
    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot connect to LiteLLM service for audio transcription",
        )
    except Exception as e:
        logger.error(f"Audio transcription proxy failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/audio/speech")
async def create_speech(
    request: TTSRequest,
    principal: Principal = Depends(get_principal),
) -> Response:
    """
    Convert text to speech via LiteLLM audio speech endpoint.

    Any authenticated user can call this.
    """
    try:
        audio_bytes, content_type = await _litellm_text_to_speech(
            model=request.model,
            input_text=request.input,
            voice=request.voice,
            response_format=request.response_format,
            speed=request.speed,
        )
        return Response(
            content=audio_bytes,
            media_type=content_type,
            headers={"Content-Disposition": f'inline; filename="speech.{request.response_format}"'},
        )
    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot connect to LiteLLM service for text-to-speech",
        )
    except Exception as e:
        logger.error(f"Text-to-speech proxy failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


# =============================================================================
# Real-time Audio Transcription (WebSocket)
# =============================================================================

_TS_RATE = 16000         # Expected sample rate from browser
_TS_BPS = 2              # Bytes per sample (int16)
_TS_FRAME_MS = 30        # VAD analysis frame size in ms
_TS_FRAME_BYTES = int(_TS_RATE * _TS_BPS * _TS_FRAME_MS / 1000)  # 960 bytes per frame
_TS_SILENCE_THRESH = 300 # RMS threshold below which a frame is "silence" (int16 scale)
_TS_SILENCE_DUR_MS = 600 # Consecutive silence ms needed to trigger a chunk boundary
_TS_MIN_SPEECH_S = 1.5   # Don't send chunks shorter than this (avoids noise-only blips)
_TS_MAX_CHUNK_S = 15.0   # Force-send even without silence after this much audio
_TS_POLL_S = 0.08        # How often the loop checks the buffer


async def _validate_ws_token(token: str) -> bool:
    """Validate a JWT using the same JWKS-based validation as regular API auth."""
    from app.auth.tokens import validate_bearer
    try:
        await validate_bearer(token)
        return True
    except Exception as e:
        logger.warning("WebSocket token validation failed: %s", e)
        return False


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 16000, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw PCM int16 bytes in a WAV container."""
    import struct
    data_len = len(pcm_bytes)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_len,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        sample_rate,
        sample_rate * channels * sample_width,
        channels * sample_width,
        sample_width * 8,
        b"data",
        data_len,
    )
    return header + pcm_bytes


def _rms_int16(frame: bytes) -> float:
    """Compute RMS energy of a frame of int16 PCM samples."""
    import array
    import math
    samples = array.array("h", frame)
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def _find_silence_boundary(pcm: bytearray) -> Optional[int]:
    """Scan PCM for a sustained silence gap and return byte offset of its start.

    Returns None if no silence boundary meeting _TS_SILENCE_DUR_MS is found.
    Scans from the end backwards so we split at the *latest* silence gap,
    keeping the chunk as large as possible.
    """
    min_bytes = int(_TS_MIN_SPEECH_S * _TS_RATE * _TS_BPS)
    if len(pcm) < min_bytes:
        return None

    frames_needed = int(_TS_SILENCE_DUR_MS / _TS_FRAME_MS)
    total_frames = len(pcm) // _TS_FRAME_BYTES
    if total_frames < frames_needed:
        return None

    min_frame = max(0, min_bytes // _TS_FRAME_BYTES)

    consecutive_silent = 0
    for i in range(total_frames - 1, min_frame - 1, -1):
        start = i * _TS_FRAME_BYTES
        end = start + _TS_FRAME_BYTES
        rms = _rms_int16(pcm[start:end])
        if rms < _TS_SILENCE_THRESH:
            consecutive_silent += 1
            if consecutive_silent >= frames_needed:
                return (i + frames_needed) * _TS_FRAME_BYTES
        else:
            consecutive_silent = 0

    return None


async def _run_transcribe_loop(
    websocket: WebSocket,
    audio_buffer: bytearray,
    buffer_lock: asyncio.Lock,
    language: str,
    stop_event: asyncio.Event,
) -> None:
    """Drain audio buffer at silence boundaries and transcribe via LiteLLM REST.

    Strategy:
    - Accumulate PCM from the shared buffer into a local pending chunk
    - Scan pending chunk for sustained silence gaps (VAD)
    - On silence boundary: slice there, send everything up to that point for transcription
    - Safety valve: if no silence found after _TS_MAX_CHUNK_S, force-send anyway
    - On stop: flush whatever remains
    """
    import json as json_mod

    cumulative_pcm = bytearray()
    pending_pcm = bytearray()
    prev_text = ""
    max_chunk_bytes = int(_TS_MAX_CHUNK_S * _TS_RATE * _TS_BPS)

    async def _transcribe_and_send(pcm_chunk: bytes, final: bool = False) -> str:
        nonlocal prev_text
        wav_bytes = _pcm_to_wav(pcm_chunk)
        try:
            result = await _litellm_transcribe_audio(
                file_bytes=wav_bytes,
                filename="stream-final.wav" if final else "stream.wav",
                content_type="audio/wav",
                model="transcribe",
                language=language or None,
            )
            text = result.get("text", "").strip()
            if not text or text == prev_text:
                return prev_text

            event_type = "transcription.done" if final else "transcription.delta"
            key = "text" if final else "delta"
            await websocket.send_text(json_mod.dumps({
                "type": event_type,
                key: text,
            }))
            prev_text = text
            return text
        except Exception as e:
            logger.warning("Live transcription chunk failed: %s", e)
            try:
                await websocket.send_text(json_mod.dumps({
                    "type": "error",
                    "message": f"Transcription error: {e}",
                }))
            except Exception:
                pass
            return prev_text

    while not stop_event.is_set():
        await asyncio.sleep(_TS_POLL_S)

        async with buffer_lock:
            if audio_buffer:
                pending_pcm.extend(audio_buffer)
                audio_buffer.clear()

        if not pending_pcm:
            continue

        split_at = _find_silence_boundary(pending_pcm)

        if split_at is None and len(pending_pcm) >= max_chunk_bytes:
            split_at = len(pending_pcm)

        if split_at is not None:
            chunk = bytes(pending_pcm[:split_at])
            pending_pcm = pending_pcm[split_at:]
            cumulative_pcm.extend(chunk)
            await _transcribe_and_send(bytes(cumulative_pcm))

    async with buffer_lock:
        if audio_buffer:
            pending_pcm.extend(audio_buffer)
            audio_buffer.clear()

    if pending_pcm:
        cumulative_pcm.extend(pending_pcm)

    if cumulative_pcm:
        await _transcribe_and_send(bytes(cumulative_pcm), final=True)


@router.websocket("/transcribe/stream")
async def transcribe_stream(websocket: WebSocket):
    """Real-time audio transcription via WebSocket.

    Accepts audio chunks from the browser, accumulates them, and periodically
    sends the growing audio buffer to LiteLLM /v1/audio/transcriptions for
    progressive transcription. Works with any backend LiteLLM supports
    (MLX Whisper, OpenAI, Groq, etc.) without requiring upstream WebSocket.

    Client protocol (JSON text frames):
      -> session.update {model, language}
      -> input_audio_buffer.append {audio: base64-encoded int16 PCM}
      -> input_audio_buffer.commit  (signals end of stream)

    Server responses:
      <- session.created {session_id}
      <- transcription.delta {delta: progressive text}
      <- transcription.done {text: final transcription}
      <- error {message}

    Authentication via ?token=<jwt> query parameter.
    """
    import base64
    import json as json_mod
    import uuid as uuid_mod

    await websocket.accept()

    token = websocket.query_params.get("token")
    if not token:
        await websocket.send_text(json_mod.dumps({
            "type": "error", "message": "Authentication required. Pass token as query parameter.",
        }))
        await websocket.close(code=4001)
        return

    if not await _validate_ws_token(token):
        await websocket.send_text(json_mod.dumps({
            "type": "error", "message": "Invalid or expired token.",
        }))
        await websocket.close(code=4003)
        return

    session_id = str(uuid_mod.uuid4())
    await websocket.send_text(json_mod.dumps({
        "type": "session.created",
        "session_id": session_id,
    }))

    logger.info("Transcribe stream started: session=%s", session_id)

    audio_buffer = bytearray()
    buffer_lock = asyncio.Lock()
    stop_event = asyncio.Event()
    language = "en"
    transcribe_task: Optional[asyncio.Task] = None

    try:
        while True:
            data = await websocket.receive_text()
            event = json_mod.loads(data)
            event_type = event.get("type", "")

            if event_type == "session.update":
                language = event.get("language", "en")
                if transcribe_task is None:
                    transcribe_task = asyncio.create_task(
                        _run_transcribe_loop(
                            websocket, audio_buffer, buffer_lock,
                            language, stop_event,
                        )
                    )

            elif event_type == "input_audio_buffer.append":
                audio_b64 = event.get("audio", "")
                if audio_b64:
                    pcm_bytes = base64.b64decode(audio_b64)
                    async with buffer_lock:
                        audio_buffer.extend(pcm_bytes)

                if transcribe_task is None:
                    transcribe_task = asyncio.create_task(
                        _run_transcribe_loop(
                            websocket, audio_buffer, buffer_lock,
                            language, stop_event,
                        )
                    )

            elif event_type == "input_audio_buffer.commit":
                stop_event.set()
                if transcribe_task:
                    try:
                        await asyncio.wait_for(transcribe_task, timeout=30.0)
                    except asyncio.TimeoutError:
                        logger.warning("Final transcription timed out")
                break

    except WebSocketDisconnect:
        logger.debug("Client disconnected from transcribe stream: session=%s", session_id)
        stop_event.set()
    except Exception as e:
        logger.error("Transcribe stream error: %s", e)
        stop_event.set()
        try:
            await websocket.send_text(json_mod.dumps({
                "type": "error", "message": str(e),
            }))
        except Exception:
            pass
    finally:
        stop_event.set()
        if transcribe_task and not transcribe_task.done():
            transcribe_task.cancel()
            try:
                await transcribe_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await websocket.close()
        except Exception:
            pass


# =============================================================================
# Cloud Model Discovery & Registration
# =============================================================================

@router.get("/cloud-models/{provider}", response_model=CloudModelsResponse)
async def list_cloud_models(
    provider: str,
    principal: Principal = Depends(get_principal),
) -> CloudModelsResponse:
    """
    List available cloud models for a provider.
    
    For OpenAI/Anthropic: queries the provider API live.
    For Bedrock: returns a curated list of popular models.
    
    Returns the model list with registration status (whether each model
    is already configured in LiteLLM).
    Requires the provider's credentials to be configured.
    Admin only.
    """
    _require_admin(principal)
    
    provider = provider.lower()
    if provider not in CLOUD_PROVIDER_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {provider}. "
                   f"Supported: {list(CLOUD_PROVIDER_CONFIG.keys())}"
        )
    
    # Check if API key is available for direct provider calls
    api_key = await _get_api_key_for_provider(provider)
    key_configured = api_key is not None
    
    # Also check LiteLLM DB (key might be stored there even if not in os.environ)
    if not key_configured:
        env_vars = await _get_configured_env_vars()
        if provider == "bedrock":
            # Bedrock: check IAM creds or bearer token
            key_configured = (
                (_is_key_configured(env_vars, "AWS_ACCESS_KEY_ID") and
                 _is_key_configured(env_vars, "AWS_SECRET_ACCESS_KEY")) or
                _is_key_configured(env_vars, "AWS_BEARER_TOKEN_BEDROCK")
            )
        else:
            env_var = CLOUD_PROVIDER_CONFIG[provider]["env_var"]
            key_configured = _is_key_configured(env_vars, env_var)
    
    models: List[CloudModel] = []
    needs_resave = False
    provider_error: Optional[str] = None
    
    if api_key:
        # Fetch live models from the provider
        models = await _fetch_live_cloud_models(provider, api_key)
        
        if models:
            # Mark which are already registered in LiteLLM
            registered = await _get_registered_model_names()
            for m in models:
                m.registered = m.id in registered
        else:
            provider_error = (
                f"Could not list {provider.title()} models. "
                f"The API key is saved but the provider API returned no models. "
                f"This may indicate an invalid key, network issue, or API outage."
            )
            logger.warning(
                f"{provider} API key is accessible but model listing returned "
                f"empty. Possible invalid key or provider API issue."
            )
    
    if not api_key and key_configured:
        # Key exists in LiteLLM DB but we can't read the plaintext (encrypted
        # and agent-api can't decrypt it, e.g. after a restart with new salt).
        needs_resave = True
        logger.info(
            f"{provider} key is in LiteLLM but not accessible to agent-api "
            f"for direct API calls. Re-save the key to enable live model listing."
        )
    
    return CloudModelsResponse(
        provider=provider,
        models=models,
        api_key_configured=key_configured,
        needs_key_resave=needs_resave,
        provider_error=provider_error,
    )


@router.post("/cloud-models/register")
async def register_cloud_models(
    request: RegisterModelsRequest,
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    """
    Register cloud models in LiteLLM so they become available for use.
    
    Accepts any model ID from the provider (fetched via live API).
    Admin only.
    """
    _require_admin(principal)
    
    provider = request.provider.lower()
    if provider not in CLOUD_PROVIDER_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {provider}"
        )
    
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    
    # Already registered models
    registered = await _get_registered_model_names()
    
    # Build new model entries for LiteLLM.
    # Models rely on environment variables for auth (set via /config/update
    # when keys are saved). We do NOT use litellm_credential_name because
    # it merges credential values into litellm_params, which breaks providers
    # like Bedrock that use env vars (AWS_REGION_NAME etc.) rather than params.
    new_models = []
    for model_id in request.model_ids:
        if model_id in registered:
            continue  # Skip already registered
        
        litellm_model = model_id
        if provider == "openai":
            litellm_model = f"openai/{model_id}"
        elif provider == "anthropic":
            litellm_model = f"anthropic/{model_id}"
        elif provider == "bedrock":
            litellm_model = f"bedrock/{model_id}"
        
        new_models.append({
            "model_name": model_id,
            "litellm_params": {
                "model": litellm_model,
            },
            "model_info": {
                "description": f"{provider.title()} {model_id}",
            },
        })
    
    if not new_models:
        return {"success": True, "registered": 0, "message": "All models already registered"}
    
    # Use LiteLLM /model/new to add each model
    registered_count = 0
    errors = []
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for model_entry in new_models:
                try:
                    resp = await client.post(
                        f"{base_url}/model/new",
                        headers=headers,
                        json=model_entry,
                    )
                    if resp.status_code == 200:
                        registered_count += 1
                        logger.info(f"Registered cloud model: {model_entry['model_name']}")
                    else:
                        error_text = resp.text[:200]
                        logger.warning(
                            f"Failed to register {model_entry['model_name']}: "
                            f"{resp.status_code} - {error_text}"
                        )
                        errors.append(f"{model_entry['model_name']}: {resp.status_code}")
                except Exception as e:
                    errors.append(f"{model_entry['model_name']}: {str(e)}")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to LiteLLM: {e}"
        )
    
    result: Dict[str, Any] = {
        "success": registered_count > 0 or len(errors) == 0,
        "registered": registered_count,
        "message": f"Registered {registered_count} model(s)",
    }
    if errors:
        result["errors"] = errors
    
    return result


# =============================================================================
# Purpose Mapping
# =============================================================================

@router.get("/purposes")
async def get_purpose_mappings(
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    """
    Get current purpose-to-model mappings from LiteLLM config.
    
    Returns the mapping of purposes (agent, fast, frontier, etc.) to their
    underlying model names, along with all available models that could be
    assigned to purposes.
    
    Uses /model/info first, falls back to /config/yaml, then /v1/models.
    """
    model_infos = await _get_model_info()
    config = await _get_litellm_config()
    config_entries = config.get("model_list", []) if config else []
    merged_entries = _merge_model_entries(model_infos, config_entries)

    if merged_entries:
        purpose_map_all = _build_purpose_map(merged_entries)
        purpose_map = {p: purpose_map_all.get(p, "") for p in CONFIGURABLE_PURPOSES if p in purpose_map_all}
        available_models = []
        for entry in merged_entries:
            mname = entry.get("model_name", "")
            params = entry.get("litellm_params", {})
            actual_model = params.get("model", "")
            info = entry.get("model_info", {})
            available_models.append({
                "model_name": mname,
                "actual_model": actual_model,
                "description": info.get("description", ""),
            })
        return {
            "purposes": purpose_map,
            "configurable_purposes": CONFIGURABLE_PURPOSES,
            "available_models": available_models,
        }

    # Fallback: /v1/models (minimal - no purpose mapping possible)
    v1_models = await _get_v1_models()
    if v1_models:
        available_models = [
            {"model_name": m.get("id", ""), "actual_model": "", "description": ""}
            for m in v1_models
        ]
        return {
            "purposes": {},
            "configurable_purposes": CONFIGURABLE_PURPOSES,
            "available_models": available_models,
        }
    
    # Nothing available
    logger.error("Could not fetch model info from any LiteLLM endpoint")
    return {
        "purposes": {},
        "configurable_purposes": CONFIGURABLE_PURPOSES,
        "available_models": [],
    }


@router.post("/purposes")
async def update_purpose_mapping(
    request: PurposeMappingUpdate,
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    """
    Update a purpose-to-model mapping in LiteLLM config.
    
    This changes which model backs a given purpose (e.g., change 'agent' from
    a local MLX model to a cloud model like 'claude-sonnet-4-20250514').
    
    Admin only.
    """
    _require_admin(principal)
    
    if request.purpose not in CONFIGURABLE_PURPOSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Purpose '{request.purpose}' is not configurable. "
                   f"Allowed: {CONFIGURABLE_PURPOSES}"
        )
    
    # Merge runtime DB entries with declarative config entries.
    model_infos = await _get_model_info()
    config = await _get_litellm_config()
    config_entries = config.get("model_list", []) if config else []
    model_entries = _merge_model_entries(model_infos, config_entries)
    
    if not model_entries:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot read LiteLLM model configuration"
        )
    
    # Verify the target model exists in LiteLLM
    all_model_names = set()
    for entry in model_entries:
        all_model_names.add(entry.get("model_name", ""))
    
    if request.model_name not in all_model_names:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{request.model_name}' is not registered in LiteLLM. "
                   f"Register it first via the cloud models endpoint."
        )
    
    # Find the target model's litellm_params
    target_params = None
    target_info = None
    for entry in model_entries:
        if entry.get("model_name") == request.model_name:
            target_params = entry.get("litellm_params", {})
            target_info = entry.get("model_info", {})
            break
    
    if not target_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not find config for model '{request.model_name}'"
        )
    
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    
    # Check if purpose already has a DB-stored model entry that we need to 
    # delete first. Config-file entries (db_model=False) can't be deleted
    # via API, but adding a DB entry with the same model_name will override.
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Find and delete any existing DB model entry for this purpose
            for entry in model_entries:
                if entry.get("model_name") == request.purpose:
                    entry_info = entry.get("model_info", {})
                    if entry_info.get("db_model", False):
                        # This is a DB-stored entry, delete it by its UUID
                        model_id = entry_info.get("id", "")
                        if model_id:
                            try:
                                await client.post(
                                    f"{base_url}/model/delete",
                                    headers=headers,
                                    json={"id": model_id},
                                )
                                logger.info(f"Deleted existing DB purpose entry: {request.purpose} (id={model_id})")
                            except Exception as e:
                                logger.warning(f"Failed to delete old purpose DB entry: {e}")
            
            # Create new purpose entry pointing to the target model.
            # IMPORTANT: Only pass minimal fields. The full model_info from
            # /model/info has many metadata/pricing fields that cause
            # "Failed to add model to db" errors in LiteLLM's /model/new.
            clean_params = {"model": target_params.get("model", "")}
            # Preserve api_base if set (needed for local models like MLX)
            if target_params.get("api_base"):
                clean_params["api_base"] = target_params["api_base"]
            
            clean_info: Dict[str, Any] = {}
            if target_info and target_info.get("description"):
                clean_info["description"] = target_info["description"]
            
            new_entry = {
                "model_name": request.purpose,
                "litellm_params": clean_params,
                "model_info": clean_info,
            }
            
            resp = await client.post(
                f"{base_url}/model/new",
                headers=headers,
                json=new_entry,
            )
            
            if resp.status_code == 200:
                logger.info(
                    f"Updated purpose '{request.purpose}' -> "
                    f"'{request.model_name}' ({target_params.get('model', '')})"
                )
                return {
                    "success": True,
                    "purpose": request.purpose,
                    "model_name": request.model_name,
                    "message": f"Purpose '{request.purpose}' now uses '{request.model_name}'"
                }
            else:
                error_text = resp.text[:300]
                logger.error(f"LiteLLM model/new failed: {resp.status_code} - {error_text}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"LiteLLM error: {error_text}"
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to update purpose mapping: {e}"
        )


# =============================================================================
# Model Deletion
# =============================================================================

class ModelDeleteRequest(BaseModel):
    """Request to delete one or more models from LiteLLM."""
    model_ids: List[str]  # LiteLLM model UUIDs (from model_info.id)


@router.post("/models/delete")
async def delete_models(
    request: ModelDeleteRequest,
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    """
    Delete one or more DB-stored models from LiteLLM.
    
    Only models with db_model=True can be deleted. Config-file models
    cannot be removed via this API.
    
    Admin only.
    """
    _require_admin(principal)
    
    if not request.model_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No model_ids provided"
        )
    
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    
    deleted = 0
    errors: List[str] = []
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for model_id in request.model_ids:
                try:
                    resp = await client.post(
                        f"{base_url}/model/delete",
                        headers=headers,
                        json={"id": model_id},
                    )
                    if resp.status_code == 200:
                        deleted += 1
                        logger.info(f"Deleted model {model_id}")
                    else:
                        error_text = resp.text[:200]
                        logger.warning(f"Failed to delete model {model_id}: {resp.status_code} - {error_text}")
                        errors.append(f"{model_id}: {error_text}")
                except Exception as e:
                    errors.append(f"{model_id}: {str(e)}")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to LiteLLM: {e}"
        )
    
    result: Dict[str, Any] = {
        "success": deleted > 0 or len(errors) == 0,
        "deleted": deleted,
        "message": f"Deleted {deleted} model(s)",
    }
    if errors:
        result["errors"] = errors
    
    return result
