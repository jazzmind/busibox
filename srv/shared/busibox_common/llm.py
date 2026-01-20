"""
Shared LLM Access Utilities for Busibox Services.

This module provides a unified interface for LLM access via LiteLLM proxy:
- LiteLLMClient for making LLM calls
- ModelRegistry for model purpose configuration
- Environment configuration for OpenAI-compatible libraries (PydanticAI)

Environment Variables:
- LITELLM_BASE_URL: Base URL for LiteLLM proxy (default: http://10.96.200.207:4000)
- LITELLM_API_KEY: API key for LiteLLM proxy
- MODEL_REGISTRY_PATH: Path to model registry JSON file (optional)

Usage:
    from busibox_common.llm import LiteLLMClient, get_registry
    
    # Direct LiteLLM calls
    client = LiteLLMClient()
    response = await client.chat_completion("cleanup", [
        {"role": "user", "content": "Fix this text: actuallyunderstood"}
    ])
    
    # Configure environment for PydanticAI
    client.configure_openai_env()
    
    # Get model config from registry
    registry = get_registry()
    config = registry.get_config("cleanup")
"""

import json
import os
from typing import Any, Dict, List, Optional

import httpx
import structlog

logger = structlog.get_logger()


# ============================================================================
# Model Registry
# ============================================================================

class ModelRegistry:
    """
    Central registry for model purposes.
    
    Maps abstract purposes (embedding, cleanup, analysis) to actual model names.
    Allows easy model swapping without code changes.
    
    The registry can be loaded from:
    1. A JSON config file (deployed via Ansible to /etc/ingest/model_registry.json)
    2. Default fallback values (for development/testing)
    
    Usage:
        registry = ModelRegistry()
        model = registry.get_model("embedding")  # Returns "embedding"
        config = registry.get_config("cleanup")  # Returns full config dict
    """
    
    # Minimal fallback models (ONLY used if model_registry.json is not found)
    # The real model registry comes from provision/ansible/group_vars/all/model_registry.yml
    # which is deployed as JSON to /etc/ingest/model_registry.json via Ansible
    DEFAULT_MODELS = {
        "embedding": {"model": "embedding", "provider": "litellm"},
        "cleanup": {"model": "cleanup", "provider": "litellm", "temperature": 0.1, "max_tokens": 32768},
        "parsing": {"model": "parsing", "provider": "litellm", "temperature": 0.1, "max_tokens": 8192},
        "analysis": {"model": "analysis", "provider": "litellm", "temperature": 0.7},
        "agent": {"model": "agent", "provider": "litellm", "temperature": 0.7},
        "fast": {"model": "fast", "provider": "litellm", "temperature": 0.5},
        "frontier": {"model": "frontier", "provider": "litellm", "temperature": 0.7},
    }
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize model registry.
        
        Args:
            config_path: Optional path to JSON config file with custom model mappings
        """
        # Start with empty dict - will be populated from deployed JSON or fallback to defaults
        self.models: Dict[str, Dict] = {}
        
        # Load deployed model registry JSON (from Ansible deployment)
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    registry_data = json.load(f)
                    # New structure: JSON has "available_models" and "purposes"
                    # purposes maps purpose -> model key, available_models has full config
                    available_models = registry_data.get("available_models", {})
                    purposes = registry_data.get("purposes", {})
                    
                    # Build models dict: purpose -> full model config
                    for purpose, model_key in purposes.items():
                        if model_key in available_models:
                            self.models[purpose] = available_models[model_key].copy()
                        else:
                            logger.warning(
                                "Model key not found in available_models",
                                purpose=purpose,
                                model_key=model_key
                            )
                    logger.info(
                        "Loaded model registry from deployed JSON",
                        config_path=config_path,
                        purposes=list(purposes.keys()),
                        source="ansible_deployment"
                    )
            except Exception as e:
                logger.error(
                    "Failed to load deployed model registry",
                    config_path=config_path,
                    error=str(e)
                )
                # Fall back to defaults if JSON load fails
                self.models = self.DEFAULT_MODELS.copy()
                logger.warning(
                    "Using fallback model registry (deployed JSON not available)",
                    fallback_purposes=list(self.models.keys())
                )
        else:
            # No config path provided or file doesn't exist - use defaults
            self.models = self.DEFAULT_MODELS.copy()
            if config_path:
                logger.warning(
                    "Model registry file not found, using defaults",
                    expected_path=config_path,
                    fallback_purposes=list(self.models.keys())
                )
            else:
                logger.debug(
                    "Model registry initialized with defaults (no config path provided)",
                    purposes=list(self.models.keys())
                )
    
    def get_model(self, purpose: str) -> str:
        """
        Get model name for a purpose.
        
        Args:
            purpose: Model purpose (e.g., "embedding", "cleanup", "analysis")
            
        Returns:
            Model name (e.g., "qwen-3-embedding")
            
        Raises:
            ValueError: If purpose is unknown
        """
        if purpose not in self.models:
            available = ", ".join(self.models.keys())
            raise ValueError(
                f"Unknown purpose: {purpose}. Available purposes: {available}"
            )
        return self.models[purpose]["model"]
    
    def get_config(self, purpose: str) -> Dict:
        """
        Get full config for a purpose.
        
        Args:
            purpose: Model purpose
            
        Returns:
            Dictionary with model config (model, max_tokens, temperature, etc.)
            
        Raises:
            ValueError: If purpose is unknown
        """
        if purpose not in self.models:
            available = ", ".join(self.models.keys())
            raise ValueError(
                f"Unknown purpose: {purpose}. Available purposes: {available}"
            )
        return self.models[purpose].copy()
    
    def list_purposes(self) -> List[str]:
        """
        List all available purposes.
        
        Returns:
            List of purpose names
        """
        return list(self.models.keys())
    
    def update_model(self, purpose: str, model: str, **kwargs) -> None:
        """
        Update model for a purpose at runtime.
        
        Args:
            purpose: Model purpose
            model: New model name
            **kwargs: Additional config to update (max_tokens, temperature, etc.)
        """
        if purpose not in self.models:
            logger.warning(
                "Creating new purpose in registry",
                purpose=purpose,
                model=model
            )
            self.models[purpose] = {"model": model, "description": f"Custom: {purpose}"}
        else:
            self.models[purpose]["model"] = model
        
        # Update additional config
        self.models[purpose].update(kwargs)
        
        logger.info(
            "Updated model registry",
            purpose=purpose,
            model=model,
            config=self.models[purpose]
        )
    
    def get_embedding_config(self, purpose: str = "embedding") -> Dict:
        """
        Get embedding model configuration including dimension.
        
        Args:
            purpose: Embedding purpose (default: "embedding")
            
        Returns:
            Dict with model, model_name, dimension, and optional matryoshka info
            
        Raises:
            ValueError: If purpose is unknown
        """
        config = self.get_config(purpose)
        
        # Ensure dimension is present (fallback for old configs)
        if "dimension" not in config:
            # Default dimensions for known models
            model_name = config.get("model_name", config.get("model", ""))
            if "large" in model_name.lower():
                config["dimension"] = 1024
            elif "base" in model_name.lower():
                config["dimension"] = 768
            elif "small" in model_name.lower():
                config["dimension"] = 384
            else:
                config["dimension"] = 1024  # Safe default
            logger.warning(
                "Embedding dimension not in config, using inferred value",
                model=model_name,
                dimension=config["dimension"]
            )
        
        return config
    
    def get_embedding_dimension(self, purpose: str = "embedding") -> int:
        """
        Get embedding dimension for a purpose.
        
        Args:
            purpose: Embedding purpose (default: "embedding")
            
        Returns:
            Embedding dimension (e.g., 1024 for bge-large)
        """
        return self.get_embedding_config(purpose).get("dimension", 1024)


# Global registry instance
_registry: Optional[ModelRegistry] = None


def get_registry() -> ModelRegistry:
    """
    Get global model registry instance.
    
    Returns:
        ModelRegistry instance
    """
    global _registry
    if _registry is None:
        config_path = os.getenv("MODEL_REGISTRY_PATH")
        _registry = ModelRegistry(config_path)
    return _registry


def reset_registry() -> None:
    """Reset global registry (useful for testing)."""
    global _registry
    _registry = None


# ============================================================================
# LiteLLM Client
# ============================================================================

class LiteLLMClient:
    """
    Unified client for LiteLLM proxy calls.
    
    Provides:
    - Direct chat completion calls to LiteLLM
    - Environment configuration for OpenAI-compatible libraries (PydanticAI)
    - Model registry integration for purpose-based model selection
    
    Usage:
        client = LiteLLMClient()
        
        # Make a chat completion
        response = await client.chat_completion("cleanup", [
            {"role": "system", "content": "You are a text editor."},
            {"role": "user", "content": "Fix this: actuallyunderstood"}
        ])
        
        # Configure env for PydanticAI
        client.configure_openai_env()
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
    ):
        """
        Initialize LiteLLM client.
        
        Args:
            base_url: LiteLLM proxy base URL (default from env)
            api_key: LiteLLM API key (default from env)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url or os.environ.get(
            "LITELLM_BASE_URL", "http://10.96.200.207:4000"
        )
        # Normalize base URL (remove trailing /v1 if present, we'll add it as needed)
        self.base_url = self.base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        
        self.api_key = api_key or os.environ.get(
            "LITELLM_API_KEY", ""
        ) or os.environ.get("LITELLM_MASTER_KEY", "")
        self.timeout = timeout
        self._registry = get_registry()
    
    @classmethod
    def from_config(cls, config: Dict) -> "LiteLLMClient":
        """
        Create a LiteLLMClient from a config dictionary.
        
        Args:
            config: Dictionary with litellm_base_url, litellm_api_key keys
        
        Returns:
            Configured LiteLLMClient instance
        """
        return cls(
            base_url=config.get("litellm_base_url"),
            api_key=config.get("litellm_api_key"),
        )
    
    def configure_openai_env(self) -> None:
        """
        Set OPENAI_BASE_URL and OPENAI_API_KEY environment variables.
        
        This configures OpenAI-compatible libraries (like PydanticAI) to use
        the LiteLLM proxy instead of direct OpenAI API calls.
        
        Note: This always overrides existing OpenAI keys because all LLM calls
        should go through the LiteLLM proxy for routing and cost tracking.
        """
        # Set to LiteLLM endpoint (with /v1 suffix for OpenAI compatibility)
        os.environ["OPENAI_BASE_URL"] = f"{self.base_url}/v1"
        
        # Use LiteLLM API key
        if self.api_key:
            os.environ["OPENAI_API_KEY"] = self.api_key
        else:
            logger.warning(
                "LITELLM_API_KEY not configured - LLM calls may fail",
                base_url=self.base_url
            )
            os.environ["OPENAI_API_KEY"] = "sk-not-configured"
        
        logger.debug(
            "OpenAI environment configured for LiteLLM",
            base_url=os.environ["OPENAI_BASE_URL"]
        )
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for LiteLLM requests."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    
    async def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict:
        """
        Make a chat completion request to LiteLLM.
        
        Args:
            model: Model name or purpose (e.g., "cleanup", "agent")
            messages: List of message dicts with role and content
            temperature: Optional temperature override
            max_tokens: Optional max_tokens override
            **kwargs: Additional parameters for the API call
        
        Returns:
            Full API response dict
            
        Raises:
            httpx.HTTPStatusError: On non-200 response
        """
        # Try to get config from registry (model may be a purpose name)
        model_config = {}
        try:
            model_config = self._registry.get_config(model)
            actual_model = model  # Use purpose name for LiteLLM routing
        except ValueError:
            # Not a purpose name, use as-is
            actual_model = model
        
        # Build request body
        body: Dict[str, Any] = {
            "model": actual_model,
            "messages": messages,
        }
        
        # Apply config defaults, then overrides
        if temperature is not None:
            body["temperature"] = temperature
        elif "temperature" in model_config:
            body["temperature"] = model_config["temperature"]
        
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        elif "max_tokens" in model_config:
            body["max_tokens"] = model_config["max_tokens"]
        
        # Add any extra kwargs
        body.update(kwargs)
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._get_headers(),
                json=body,
            )
            response.raise_for_status()
            return response.json()
    
    async def get_chat_response(
        self,
        model: str,
        messages: List[Dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """
        Make a chat completion and return just the text response.
        
        This is a convenience method that extracts the assistant's message.
        
        Args:
            model: Model name or purpose
            messages: List of message dicts
            **kwargs: Additional parameters
        
        Returns:
            The assistant's response text
        """
        result = await self.chat_completion(model, messages, **kwargs)
        return result["choices"][0]["message"]["content"].strip()
    
    async def health_check(self) -> bool:
        """
        Check if LiteLLM service is healthy.
        
        Returns:
            True if healthy, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # LiteLLM has a /health endpoint
                response = await client.get(
                    f"{self.base_url}/health",
                    headers=self._get_headers(),
                )
                return response.status_code == 200
        except Exception as e:
            logger.error("LiteLLM health check failed", error=str(e))
            return False


# ============================================================================
# Convenience Functions
# ============================================================================

def ensure_openai_env(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> None:
    """
    Ensure OpenAI environment is configured for LiteLLM.
    
    This is a convenience function that creates a client and configures env.
    Called lazily when agents are instantiated, not at module import time.
    
    Args:
        base_url: Optional override for LITELLM_BASE_URL
        api_key: Optional override for LITELLM_API_KEY
    """
    client = LiteLLMClient(base_url=base_url, api_key=api_key)
    client.configure_openai_env()


# Global client instance (lazy initialization)
_client: Optional[LiteLLMClient] = None


def get_client() -> LiteLLMClient:
    """
    Get global LiteLLM client instance.
    
    Returns:
        LiteLLMClient instance
    """
    global _client
    if _client is None:
        _client = LiteLLMClient()
    return _client


def reset_client() -> None:
    """Reset global client (useful for testing)."""
    global _client
    _client = None
