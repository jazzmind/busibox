"""
Model Purpose Registry

Central registry for model purposes. Maps abstract purposes (embedding, cleanup, chat)
to actual model names. Allows easy model swapping without code changes.

Usage:
    from shared.model_registry import get_registry
    
    registry = get_registry()
    model = registry.get_model("embedding")  # Returns "qwen-3-embedding"
    config = registry.get_config("cleanup")  # Returns full config dict
"""

from typing import Dict, Optional
import json
import os
import structlog

logger = structlog.get_logger()


class ModelRegistry:
    """
    Central registry for model purposes.
    
    Maps abstract purposes (embedding, cleanup, chat) to actual model names.
    Allows easy model swapping without code changes.
    """
    
    # Minimal fallback models (should be configured via Ansible)
    DEFAULT_MODELS = {
        "embedding": {"model": "qwen-3-embedding", "provider": "litellm"},
        "cleanup": {"model": "qwen-2.5-32b", "provider": "litellm", "temperature": 0.1},
        "chat": {"model": "qwen-2.5-72b", "provider": "litellm", "temperature": 0.7},
    }
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize model registry.
        
        Args:
            config_path: Optional path to JSON config file with custom model mappings
        """
        self.models = self.DEFAULT_MODELS.copy()
        
        # Load custom config if provided
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    custom = json.load(f)
                    custom_purposes = custom.get("purposes", {})
                    self.models.update(custom_purposes)
                    logger.info(
                        "Loaded custom model registry",
                        config_path=config_path,
                        custom_purposes=list(custom_purposes.keys())
                    )
            except Exception as e:
                logger.error(
                    "Failed to load custom model registry",
                    config_path=config_path,
                    error=str(e)
                )
        
        logger.info(
            "Model registry initialized",
            purposes=list(self.models.keys()),
            custom_config=config_path is not None
        )
    
    def get_model(self, purpose: str) -> str:
        """
        Get model name for a purpose.
        
        Args:
            purpose: Model purpose (e.g., "embedding", "cleanup", "chat")
            
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
    
    def list_purposes(self) -> list:
        """
        List all available purposes.
        
        Returns:
            List of purpose names
        """
        return list(self.models.keys())
    
    def update_model(self, purpose: str, model: str, **kwargs):
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


def reset_registry():
    """Reset global registry (useful for testing)."""
    global _registry
    _registry = None

