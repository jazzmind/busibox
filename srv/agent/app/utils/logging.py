"""
Structured logging and OpenTelemetry initialization.

Provides:
- JSON-formatted structured logging with trace context
- OpenTelemetry tracing for requests and agent executions
- Automatic trace/span ID injection into logs
"""

import logging
import sys
from typing import Optional

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

# Optional imports for enhanced instrumentation
try:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    HAS_SQLALCHEMY_INSTRUMENTATION = True
except ImportError:
    HAS_SQLALCHEMY_INSTRUMENTATION = False

try:
    from pythonjsonlogger import jsonlogger
    HAS_JSON_LOGGER = True
except ImportError:
    HAS_JSON_LOGGER = False

from app.config.settings import Settings


class TraceContextFilter(logging.Filter):
    """
    Inject OpenTelemetry trace context into log records.
    
    Adds trace_id and span_id fields to every log record for correlation.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span()
        if span and span.get_span_context().is_valid:
            ctx = span.get_span_context()
            record.trace_id = format(ctx.trace_id, "032x")
            record.span_id = format(ctx.span_id, "016x")
        else:
            record.trace_id = "0" * 32
            record.span_id = "0" * 16
        return True


def setup_logging(settings: Settings) -> None:
    """
    Configure structured logging with JSON output and trace context.
    
    Args:
        settings: Application settings with log_level configuration
    """
    # Create JSON formatter with trace context (if available)
    if HAS_JSON_LOGGER:
        log_format = "%(asctime)s %(levelname)s %(name)s %(trace_id)s %(span_id)s %(message)s"
        formatter = jsonlogger.JsonFormatter(
            log_format,
            rename_fields={
                "asctime": "timestamp",
                "levelname": "level",
                "name": "logger",
            },
        )
    else:
        # Fallback to standard formatter
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(trace_id)s | %(span_id)s | %(message)s"
        )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper()))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add console handler with JSON formatting
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(TraceContextFilter())
    root_logger.addHandler(console_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    
    # Add filter to suppress health check access logs
    class HealthCheckFilter(logging.Filter):
        """Filter out health check requests from access logs."""
        
        EXCLUDED_PATHS = {"/health", "/health/", "/", "/readiness", "/liveness"}
        
        def filter(self, record: logging.LogRecord) -> bool:
            # Check if this is an access log with a health check path
            message = record.getMessage()
            for path in self.EXCLUDED_PATHS:
                # Match patterns like 'GET /health' or '"GET /health HTTP/1.1"'
                if f"GET {path} " in message or f"GET {path}\"" in message:
                    return False
            return True
    
    # Apply filter to uvicorn access logger
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.addFilter(HealthCheckFilter())


def setup_tracing(settings: Settings, app_name: Optional[str] = None) -> None:
    """
    Configure OpenTelemetry tracing with OTLP exporter.
    
    Args:
        settings: Application settings with environment configuration
        app_name: Service name for traces (defaults to settings.app_name)
    """
    service_name = app_name or settings.app_name

    # Create resource with service metadata
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "1.0.0",
            "deployment.environment": settings.environment,
        }
    )

    # Create tracer provider
    provider = TracerProvider(resource=resource)

    # Add console exporter for development
    if settings.debug or settings.environment == "development":
        console_exporter = ConsoleSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(console_exporter))

    # Add OTLP exporter if configured
    otlp_endpoint = getattr(settings, "otlp_endpoint", None)
    if otlp_endpoint:
        otlp_exporter = OTLPSpanExporter(endpoint=str(otlp_endpoint), insecure=True)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    # Set global tracer provider
    trace.set_tracer_provider(provider)

    # Instrument libraries
    HTTPXClientInstrumentor().instrument()
    
    if HAS_SQLALCHEMY_INSTRUMENTATION:
        SQLAlchemyInstrumentor().instrument(enable_commenter=True)


def instrument_fastapi(app) -> None:
    """
    Instrument FastAPI application with OpenTelemetry.
    
    Excludes health check endpoints from tracing to reduce log noise.
    
    Args:
        app: FastAPI application instance
    """
    # Exclude health check endpoints from tracing
    def exclude_health_check(scope) -> bool:
        """Return True to exclude from tracing."""
        path = scope.get("path", "")
        return path in ("/health", "/health/", "/", "/readiness", "/liveness")
    
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="health,health/,readiness,liveness",
        tracer_provider=trace.get_tracer_provider(),
    )
