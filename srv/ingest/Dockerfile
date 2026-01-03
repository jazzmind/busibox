# =============================================================================
# Busibox Ingest API Dockerfile
# =============================================================================
#
# Document ingestion and processing service for Busibox platform.
# Handles file upload, text extraction, chunking, and embedding generation.
#
# Build:
#   docker build -t busibox-ingest .
#
# Run:
#   docker run -p 8002:8002 --env-file .env busibox-ingest
#
# Note: This image is large (~3GB) due to ML dependencies (PyTorch, Marker, etc.)
#
# =============================================================================

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first (for caching)
COPY ingest/requirements.txt .

# Install Python dependencies (this takes a while due to ML libraries)
RUN pip install --no-cache-dir -r requirements.txt

# Download spacy model (using pip for reliable install)
RUN pip install --no-cache-dir https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl

# Copy source code
COPY ingest/src/ ./src/

# Copy shared_auth module
COPY shared_auth/ ./shared_auth/

# Create temp directory for processing
RUN mkdir -p /tmp/ingest

# Set Python path to include both src and shared_auth
ENV PYTHONPATH=/app/src:/app
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8002

# Health check using Python (no curl needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health/live')" || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8002"]
