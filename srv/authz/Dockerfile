# =============================================================================
# Busibox AuthZ API Dockerfile
# =============================================================================
#
# Authentication and Authorization service for Busibox platform.
# Provides JWT signing, OAuth2 token exchange, and RBAC.
#
# Build:
#   docker build -t busibox-authz .
#
# Run:
#   docker run -p 8010:8010 --env-file .env busibox-authz
#
# =============================================================================

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first (for caching)
COPY authz/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy and install busibox_common shared library
COPY shared/ ./shared/
RUN pip install --no-cache-dir ./shared

# Copy source code
COPY authz/src/ ./src/

# Set Python path
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8010

# Health check using Python (no curl needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/health/live')" || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
