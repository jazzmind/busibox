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
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
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
    provider: str = Field(..., description="Provider name: openai, anthropic")
    api_key: str = Field(..., min_length=1, description="API key for the provider")


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


class RegisterModelsRequest(BaseModel):
    """Request to register cloud models in LiteLLM."""
    provider: str = Field(..., description="Provider: openai, anthropic")
    model_ids: List[str] = Field(..., description="Model IDs to register")


class PurposeMappingUpdate(BaseModel):
    """Request to update a purpose-to-model mapping."""
    purpose: str = Field(..., description="Purpose name (e.g. agent, fast, frontier)")
    model_name: str = Field(..., description="LiteLLM model name to assign")


# =============================================================================
# Known Cloud Models (curated lists - these are available when API key is set)
# =============================================================================

OPENAI_MODELS = [
    CloudModel(id="gpt-4.1", name="GPT-4.1", provider="openai",
               description="Most capable OpenAI model, strong reasoning", context_window=1047576),
    CloudModel(id="gpt-4.1-mini", name="GPT-4.1 Mini", provider="openai",
               description="Fast, cost-effective for most tasks", context_window=1047576),
    CloudModel(id="gpt-4.1-nano", name="GPT-4.1 Nano", provider="openai",
               description="Fastest, cheapest OpenAI model", context_window=1047576),
    CloudModel(id="o3", name="o3", provider="openai",
               description="Advanced reasoning model", context_window=200000),
    CloudModel(id="o3-mini", name="o3 Mini", provider="openai",
               description="Fast reasoning model", context_window=200000),
    CloudModel(id="o4-mini", name="o4 Mini", provider="openai",
               description="Latest reasoning model", context_window=200000),
]

ANTHROPIC_MODELS = [
    CloudModel(id="claude-sonnet-4-20250514", name="Claude Sonnet 4", provider="anthropic",
               description="High capability, balanced speed and intelligence", context_window=200000),
    CloudModel(id="claude-3-7-sonnet-20250219", name="Claude 3.7 Sonnet", provider="anthropic",
               description="Extended thinking, strong reasoning", context_window=200000),
    CloudModel(id="claude-3-5-haiku-20241022", name="Claude 3.5 Haiku", provider="anthropic",
               description="Fast and cost-effective", context_window=200000),
]

CLOUD_MODELS: Dict[str, List[CloudModel]] = {
    "openai": OPENAI_MODELS,
    "anthropic": ANTHROPIC_MODELS,
}

# Purposes that can be overridden via the UI
CONFIGURABLE_PURPOSES = [
    "fast", "agent", "chat", "frontier", "tool_calling", "test", "default",
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


async def _get_litellm_config() -> Optional[Dict[str, Any]]:
    """Fetch current LiteLLM config via /config/yaml."""
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base_url}/config/yaml", headers=headers)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch LiteLLM config: {e}")
    return None


async def _get_registered_model_names() -> set:
    """Get the set of model_name values currently registered in LiteLLM."""
    config = await _get_litellm_config()
    if not config:
        return set()
    model_list = config.get("model_list", [])
    names = set()
    for entry in model_list:
        names.add(entry.get("model_name", ""))
        # Also extract the actual model ID from litellm_params
        params = entry.get("litellm_params", {})
        model_id = params.get("model", "")
        # Strip provider prefixes like "openai/" or "bedrock/"
        if "/" in model_id:
            names.add(model_id.split("/", 1)[-1])
        names.add(model_id)
    return names


def _build_purpose_map(model_list: List[Dict]) -> Dict[str, str]:
    """
    Build purpose -> underlying model description from LiteLLM config.
    
    LiteLLM config entries look like:
      model_name: "agent"
      litellm_params:
        model: "openai/mlx-community/Qwen2.5-7B-Instruct-4bit"
    
    We want: {"agent": "mlx-community/Qwen2.5-7B-Instruct-4bit"}
    """
    purpose_map = {}
    for entry in model_list:
        name = entry.get("model_name", "")
        params = entry.get("litellm_params", {})
        model_id = params.get("model", "")
        # Strip provider prefix (openai/, bedrock/, anthropic/)
        if "/" in model_id:
            parts = model_id.split("/", 1)
            if parts[0] in ("openai", "bedrock", "anthropic"):
                model_id = parts[1]
        purpose_map[name] = model_id
    return purpose_map


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/models", response_model=ModelsResponse)
async def list_models(
    principal: Principal = Depends(get_principal),
) -> ModelsResponse:
    """
    List available models from LiteLLM with proper provider labels and purpose mapping.
    """
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    config = await _get_litellm_config()
    
    # Build a lookup from LiteLLM config for richer info
    config_lookup: Dict[str, Dict] = {}
    model_list = []
    if config:
        model_list = config.get("model_list", [])
        for entry in model_list:
            mname = entry.get("model_name", "")
            params = entry.get("litellm_params", {})
            info = entry.get("model_info", {})
            actual_model = params.get("model", "")
            api_base = params.get("api_base", "")
            
            # Determine real provider from config
            if "host.docker.internal" in api_base or "mlx" in api_base:
                provider = "mlx (local)"
            elif "vllm" in api_base:
                provider = "vllm (local)"
            elif actual_model.startswith("bedrock/"):
                provider = "aws-bedrock"
            elif actual_model.startswith("anthropic/") or "anthropic" in actual_model:
                provider = "anthropic"
            elif actual_model.startswith("openai/") and "mlx-community" not in actual_model:
                provider = "openai"
            elif "mlx-community" in actual_model or "mlx" in actual_model.lower():
                provider = "mlx (local)"
            else:
                provider = params.get("custom_llm_provider", "unknown")
            
            config_lookup[mname] = {
                "provider": provider,
                "actual_model": actual_model,
                "description": info.get("description", ""),
            }
    
    # Build models list from config (not from /v1/models which loses context)
    models = []
    for entry in model_list:
        mname = entry.get("model_name", "")
        lookup = config_lookup.get(mname, {})
        models.append(ModelInfo(
            id=mname,
            provider=lookup.get("provider"),
            description=lookup.get("description"),
        ))
    
    # Build purpose map
    purpose_map = _build_purpose_map(model_list)
    
    return ModelsResponse(models=models, purposes=purpose_map)


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
    Admin only.
    """
    _require_admin(principal)
    
    provider = request.provider.lower()
    if provider not in ("openai", "anthropic"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {provider}. Supported: openai, anthropic"
        )
    
    env_var_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    env_var = env_var_map[provider]
    
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            update_payload = {
                "environment_variables": {
                    env_var: request.api_key,
                }
            }
            
            response = await client.post(
                f"{base_url}/config/update",
                headers=headers,
                json=update_payload,
            )
            
            if response.status_code == 200:
                logger.info(f"Successfully updated {provider} API key in LiteLLM")
                return {
                    "success": True,
                    "provider": provider,
                    "message": f"{provider.title()} API key configured successfully"
                }
            else:
                logger.warning(
                    f"LiteLLM config/update returned {response.status_code}: {response.text}"
                )
    except Exception as e:
        logger.warning(f"LiteLLM config/update failed: {e}")
    
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Failed to update LiteLLM configuration."
    )


@router.get("/keys", response_model=KeysResponse)
async def list_provider_keys(
    principal: Principal = Depends(get_principal),
) -> KeysResponse:
    """List which cloud providers have API keys configured. Admin only."""
    _require_admin(principal)
    
    config = await _get_litellm_config()
    providers_info = []
    
    if config:
        env_vars = config.get("environment_variables", {})
        for provider, env_var in [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")]:
            key_val = env_vars.get(env_var, "")
            configured = bool(key_val and key_val != "" and key_val != "None")
            providers_info.append(ProviderKeyInfo(
                provider=provider,
                configured=configured,
                masked_key=_mask_key(key_val) if configured else None,
            ))
    else:
        for provider in ["openai", "anthropic"]:
            providers_info.append(ProviderKeyInfo(provider=provider, configured=False))
    
    return KeysResponse(providers=providers_info)


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
    
    Returns curated model list with registration status (whether each model
    is already configured in LiteLLM).
    Admin only.
    """
    _require_admin(principal)
    
    provider = provider.lower()
    if provider not in CLOUD_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {provider}. Supported: {list(CLOUD_MODELS.keys())}"
        )
    
    # Check if API key is configured
    config = await _get_litellm_config()
    env_vars = config.get("environment_variables", {}) if config else {}
    env_var_map = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
    key_val = env_vars.get(env_var_map.get(provider, ""), "")
    key_configured = bool(key_val and key_val != "" and key_val != "None")
    
    # Check which models are already registered
    registered = await _get_registered_model_names()
    
    models = []
    for m in CLOUD_MODELS[provider]:
        model_copy = m.model_copy()
        model_copy.registered = m.id in registered
        models.append(model_copy)
    
    return CloudModelsResponse(
        provider=provider,
        models=models,
        api_key_configured=key_configured,
    )


@router.post("/cloud-models/register")
async def register_cloud_models(
    request: RegisterModelsRequest,
    principal: Principal = Depends(get_principal),
) -> Dict[str, Any]:
    """
    Register cloud models in LiteLLM so they become available for use.
    Admin only.
    """
    _require_admin(principal)
    
    provider = request.provider.lower()
    if provider not in CLOUD_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {provider}"
        )
    
    # Validate model IDs against known models
    known_ids = {m.id for m in CLOUD_MODELS[provider]}
    invalid = set(request.model_ids) - known_ids
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown model IDs for {provider}: {invalid}"
        )
    
    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    
    # Already registered models
    registered = await _get_registered_model_names()
    
    # Build new model entries for LiteLLM
    new_models = []
    for model_id in request.model_ids:
        if model_id in registered:
            continue  # Skip already registered
        
        litellm_model = model_id
        if provider == "openai":
            litellm_model = f"openai/{model_id}"
        elif provider == "anthropic":
            litellm_model = f"anthropic/{model_id}"
        
        new_models.append({
            "model_name": model_id,
            "litellm_params": {
                "model": litellm_model,
            },
            "model_info": {
                "description": next(
                    (m.description for m in CLOUD_MODELS[provider] if m.id == model_id),
                    ""
                ),
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
    """
    config = await _get_litellm_config()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot read LiteLLM configuration"
        )
    
    model_list = config.get("model_list", [])
    purpose_map = _build_purpose_map(model_list)
    
    # Build list of all available model names that can be assigned
    available_models = []
    for entry in model_list:
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
    
    # Get current config
    config = await _get_litellm_config()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot read LiteLLM configuration"
        )
    
    model_list = config.get("model_list", [])
    
    # Verify the target model exists in LiteLLM
    all_model_names = set()
    for entry in model_list:
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
    for entry in model_list:
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
    
    # Check if purpose already exists as a model entry
    purpose_exists = request.purpose in all_model_names
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if purpose_exists:
                # Delete old purpose mapping first
                try:
                    await client.post(
                        f"{base_url}/model/delete",
                        headers=headers,
                        json={"id": request.purpose},
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete old purpose mapping: {e}")
            
            # Create new purpose entry pointing to the target model
            new_entry = {
                "model_name": request.purpose,
                "litellm_params": dict(target_params),  # Copy target model's params
                "model_info": dict(target_info) if target_info else {},
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
