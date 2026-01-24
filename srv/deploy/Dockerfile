FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for SSH and PostgreSQL client
RUN apt-get update && apt-get install -y \
    openssh-client \
    ansible \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Expose port
EXPOSE 8011

# Run the service
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8011"]
