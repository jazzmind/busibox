# =============================================================================
# Busibox Embedding API Dockerfile
# =============================================================================
#
# Dedicated embedding service for Busibox platform.
# Loads FastEmbed model once at startup and provides HTTP API for all consumers.
#
# Build:
#   docker build -t busibox-embedding .
#
# Run:
#   docker run -p 8004:8004 busibox-embedding
#
# This service is designed to stay running during development while other
# services (data-api, search-api) can be restarted without model reload delay.
#
# =============================================================================

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Set Python path
ENV PYTHONPATH=/app/src:/app
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8005

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8005/health || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]
