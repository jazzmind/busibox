"""
Busibox Common - Shared utilities for all Busibox services.

This package provides common functionality used across services:
- Database initialization and migration management
- Shared configuration patterns
- Common utilities
"""

from .db import DatabaseInitializer, SchemaManager

__all__ = ["DatabaseInitializer", "SchemaManager"]
__version__ = "0.1.0"
