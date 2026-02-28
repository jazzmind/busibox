"""
Embedding API Configuration.

Environment variables:
- FASTEMBED_MODEL: Model name (default: nomic-ai/nomic-embed-text-v1.5)
- FASTEMBED_CACHE_DIR: Cache directory for model files (default: /root/.cache/fastembed)
- EMBEDDING_BATCH_SIZE: Batch size for embedding generation (default: 32)
- EMBEDDING_DIMENSION: Output dimension override for Matryoshka models (truncation)
- PORT: Server port (default: 8005)
"""

import os
from dataclasses import dataclass, field
from typing import Optional, List


# Native (full) dimensions for supported FastEmbed models
MODEL_DIMENSIONS = {
    # Nomic (Matryoshka)
    "nomic-ai/nomic-embed-text-v1.5": 768,
    "nomic-embed-text-v1.5": 768,
    # BGE family
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    "bge-small-en-v1.5": 384,
    "bge-base-en-v1.5": 768,
    "bge-large-en-v1.5": 1024,
}

# Models that support Matryoshka (dimension truncation with renormalization)
MATRYOSHKA_MODELS = {
    "nomic-ai/nomic-embed-text-v1.5": [64, 128, 256, 512, 768],
    "nomic-embed-text-v1.5": [64, 128, 256, 512, 768],
}


@dataclass
class Config:
    """Embedding API configuration."""
    
    # Model configuration
    model_name: str = os.getenv("FASTEMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
    
    # Cache directory for model files
    cache_dir: Optional[str] = os.getenv("FASTEMBED_CACHE_DIR", "/root/.cache/fastembed")
    
    # Output dimension override -- for Matryoshka models this truncates + renormalizes.
    # If not set, uses the model's native dimension.
    _output_dim: Optional[str] = field(default_factory=lambda: os.getenv("EMBEDDING_DIMENSION"))
    
    # Server configuration
    port: int = int(os.getenv("PORT", "8005"))
    
    @property
    def native_dimension(self) -> int:
        """Full (untruncated) dimension for the model."""
        return MODEL_DIMENSIONS.get(self.model_name, 768)
    
    @property
    def dimension(self) -> int:
        """Effective output dimension (after truncation if configured)."""
        if self._output_dim:
            return int(self._output_dim)
        return self.native_dimension
    
    @property
    def matryoshka(self) -> bool:
        """Whether the model supports Matryoshka dimension truncation."""
        return self.model_name in MATRYOSHKA_MODELS
    
    @property
    def matryoshka_dimensions(self) -> List[int]:
        """Supported truncation dimensions for Matryoshka models."""
        return MATRYOSHKA_MODELS.get(self.model_name, [])
    
    @property
    def truncate(self) -> bool:
        """Whether truncation is active (output_dim < native_dim on a Matryoshka model)."""
        return self.matryoshka and self.dimension < self.native_dimension


# Global config instance
config = Config()
