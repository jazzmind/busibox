# =============================================================================
# Busibox Agent API Dockerfile
# =============================================================================
#
# AI Agent service for Busibox platform.
# Provides agent execution, tool calling, and workflow management.
#
# Build:
#   docker build -t busibox-agent .
#
# Run:
#   docker run -p 8000:8000 --env-file .env busibox-agent
#
# =============================================================================

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first (for caching)
COPY agent/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy and install busibox_common shared library
COPY shared/ ./shared/
RUN pip install --no-cache-dir ./shared

# Install aider-chat in an isolated venv to avoid dependency conflicts.
# The BuilderLocalAgent calls `aider` as a subprocess CLI tool.
RUN python -m venv /opt/aider && \
    /opt/aider/bin/pip install --no-cache-dir aider-chat>=0.78.0
# Keep system Python first for migrations/runtime; only use aider CLI when invoked.
ENV PATH="$PATH:/opt/aider/bin"

# Copy source code
COPY agent/app/ ./app/
COPY agent/alembic/ ./alembic/
COPY agent/alembic.ini .

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8000

# Health check using Python (no curl needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run the application with auto-migrations
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
