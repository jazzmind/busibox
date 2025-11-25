"""
Model Purpose Registry

Central registry for model purposes. Maps abstract purposes (embedding, cleanup, analysis)
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
    
    Maps abstract purposes (embedding, cleanup, analysis) to actual model names.
    Allows easy model swapping without code changes.
    """
    
    # Minimal fallback models (ONLY used if model_registry.json is not found)
    # The real model registry comes from provision/ansible/group_vars/all/model_registry.yml
    # which is deployed as JSON to /etc/ingest/model_registry.json via Ansible
    # These defaults should never be used in production - they're just for development/testing
    DEFAULT_MODELS = {
        "embedding": {"model": "embedding", "provider": "litellm"},
        "cleanup": {"model": "cleanup", "provider": "litellm", "temperature": 0.1, "max_tokens": 32768},
        "parsing": {"model": "parsing", "provider": "litellm", "temperature": 0.1, "max_tokens": 8192},
        "analysis": {"model": "analysis", "provider": "litellm", "temperature": 0.7},
    }
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize model registry.
        
        Args:
            config_path: Optional path to JSON config file with custom model mappings
        """
        # Start with empty dict - will be populated from deployed JSON or fallback to defaults
        self.models = {}
        
        # Load deployed model registry JSON (from Ansible deployment)
        # This is the single source of truth: provision/ansible/group_vars/all/model_registry.yml
        # which gets deployed as JSON to /etc/ingest/model_registry.json
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
            # This should only happen in development/testing
            self.models = self.DEFAULT_MODELS.copy()
            if config_path:
                logger.warning(
                    "Model registry file not found, using defaults",
                    expected_path=config_path,
                    fallback_purposes=list(self.models.keys())
                )
            else:
                logger.info(
                    "Model registry initialized with defaults (no config path provided)",
                    purposes=list(self.models.keys())
                )
        
        logger.info(
            "Model registry initialized",
            purposes=list(self.models.keys()),
            source="deployed_json" if (config_path and os.path.exists(config_path)) else "defaults"
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

