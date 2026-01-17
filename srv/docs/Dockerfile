# =============================================================================
# Busibox Docs API Dockerfile
# =============================================================================
#
# Lightweight documentation service for Busibox platform.
# Serves markdown documentation and OpenAPI specifications via REST API.
#
# Build:
#   docker build -t busibox-docs .
#
# Run:
#   docker run -p 8004:8004 \
#     -v /path/to/docs:/app/docs:ro \
#     -v /path/to/openapi:/app/openapi:ro \
#     busibox-docs
#
# =============================================================================

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first (for caching)
COPY docs/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY docs/src/ ./src/

# Set Python path
ENV PYTHONPATH=/app/src:/app
ENV PYTHONUNBUFFERED=1

# Default paths (override with volumes in production)
ENV DOCS_PATH=/app/docs
ENV OPENAPI_PATH=/app/openapi

# Expose port
EXPOSE 8004

# Health check using Python (no curl needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8004/health/live')" || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8004"]
