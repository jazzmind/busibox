# =============================================================================
# Busibox Search API Dockerfile
# =============================================================================
#
# Semantic search service for Busibox platform.
# Provides hybrid keyword + semantic search with reranking.
#
# Build:
#   docker build -t busibox-search .
#
# Run:
#   docker run -p 8003:8003 --env-file .env busibox-search
#
# =============================================================================

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first (for caching)
COPY search/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Download nltk data (if needed for text processing)
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('punkt_tab'); nltk.download('stopwords')"

# Download spacy model (using pip for reliable install)
RUN pip install --no-cache-dir https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl

# Copy source code
COPY search/src/ ./src/

# Copy busibox_common shared library
COPY shared/ ./shared/

# Install busibox_common
RUN pip install --no-cache-dir ./shared

# Set Python path
ENV PYTHONPATH=/app/src:/app
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8003

# Health check using Python (no curl needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health/live')" || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8003"]
