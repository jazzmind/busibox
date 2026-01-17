"""
Enhanced structured logging with structlog for dispatcher and CRUD operations.

Provides:
- Structured logging with consistent field names
- Integration with standard logging (no double-JSON encoding)
- Context-aware logging for dispatcher decisions
- Integration with existing OpenTelemetry tracing
"""

import logging
from typing import Any

import structlog
from opentelemetry import trace


def add_trace_context(logger: Any, method_name: str, event_dict: dict) -> dict:
    """
    Add OpenTelemetry trace context to structlog events.
    
    Args:
        logger: The logger instance
        method_name: The method name being called
        event_dict: The event dictionary to enhance
        
    Returns:
        Enhanced event dictionary with trace context
    """
    span = trace.get_current_span()
    if span and span.get_span_context().is_valid:
        ctx = span.get_span_context()
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_structlog() -> None:
    """
    Configure structlog to work with stdlib logging.
    
    Uses ProcessorFormatter to let the root logger handle JSON rendering,
    avoiding double-JSON encoding when python-json-logger is also configured.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            add_trace_context,
            structlog.processors.UnicodeDecoder(),
            # Use ProcessorFormatter.wrap_for_formatter to pass events to stdlib
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """
    Get a configured structlog logger instance.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Configured structlog logger
    """
    return structlog.get_logger(name)


# Configure structlog on module import
configure_structlog()








