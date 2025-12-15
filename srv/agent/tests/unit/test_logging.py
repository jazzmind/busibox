"""
Unit tests for structured logging and OpenTelemetry setup.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry import trace

from app.config.settings import Settings
from app.utils.logging import TraceContextFilter, setup_logging, setup_tracing


def test_trace_context_filter_with_valid_span():
    """Test that TraceContextFilter injects trace context from active span."""
    # Create a mock span with valid context
    mock_span = MagicMock()
    mock_context = MagicMock()
    mock_context.is_valid = True
    mock_context.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
    mock_context.span_id = 0x1234567890ABCDEF
    mock_span.get_span_context.return_value = mock_context

    # Create a log record
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="test message",
        args=(),
        exc_info=None,
    )

    # Apply filter with mocked span
    with patch("app.utils.logging.trace.get_current_span", return_value=mock_span):
        filter_instance = TraceContextFilter()
        result = filter_instance.filter(record)

    # Verify trace context was injected
    assert result is True
    assert record.trace_id == "1234567890abcdef1234567890abcdef"
    assert record.span_id == "1234567890abcdef"


def test_trace_context_filter_without_span():
    """Test that TraceContextFilter uses zero IDs when no active span."""
    # Create a log record
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="test message",
        args=(),
        exc_info=None,
    )

    # Apply filter with no active span
    with patch("app.utils.logging.trace.get_current_span", return_value=None):
        filter_instance = TraceContextFilter()
        result = filter_instance.filter(record)

    # Verify zero IDs were used
    assert result is True
    assert record.trace_id == "0" * 32
    assert record.span_id == "0" * 16


def test_setup_logging_configures_json_formatter():
    """Test that setup_logging configures JSON formatter with trace context."""
    settings = Settings(log_level="DEBUG")

    # Setup logging
    setup_logging(settings)

    # Verify root logger is configured
    root_logger = logging.getLogger()
    assert root_logger.level == logging.DEBUG
    assert len(root_logger.handlers) > 0

    # Verify handler has TraceContextFilter
    handler = root_logger.handlers[0]
    filter_names = [type(f).__name__ for f in handler.filters]
    assert "TraceContextFilter" in filter_names


def test_setup_tracing_creates_tracer_provider():
    """Test that setup_tracing creates and configures tracer provider."""
    settings = Settings(environment="test", debug=True)

    # Setup tracing
    setup_tracing(settings, app_name="test-service")

    # Verify tracer provider is set
    provider = trace.get_tracer_provider()
    assert provider is not None

    # Verify we can get a tracer
    tracer = trace.get_tracer("test")
    assert tracer is not None


def test_setup_tracing_with_otlp_endpoint():
    """Test that setup_tracing configures OTLP exporter when endpoint provided."""
    settings = Settings(
        environment="production",
        debug=False,
        otlp_endpoint="http://localhost:4317",
    )

    # Setup tracing (should not raise)
    setup_tracing(settings)

    # Verify tracer provider is set
    provider = trace.get_tracer_provider()
    assert provider is not None





