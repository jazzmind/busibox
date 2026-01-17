# Milvus Schema Initialization Image
# Pre-installs pymilvus to avoid pip timeout issues at runtime

FROM python:3.11-slim

# Install pymilvus during build
RUN pip install --no-cache-dir pymilvus>=2.4.0

WORKDIR /app

# Default command - can be overridden
CMD ["python", "/app/hybrid_schema.py"]
