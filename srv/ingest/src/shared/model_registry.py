"""
Model Purpose Registry - Re-exports from busibox_common.llm.

All functionality has been moved to busibox_common.llm.
New code should import directly from there:

    from busibox_common.llm import get_registry, ModelRegistry
"""

from busibox_common.llm import (
    ModelRegistry,
    get_registry,
    reset_registry,
)

__all__ = ["ModelRegistry", "get_registry", "reset_registry"]
