"""
Worker module for document ingestion.

Organized into separate concerns for maintainability.
"""

from .error_handler import ErrorHandler
from .history_logger import HistoryLogger

__all__ = ["ErrorHandler", "HistoryLogger"]

