FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for SSH, PostgreSQL client, and Docker CLI
RUN apt-get update && apt-get install -y \
    openssh-client \
    ansible \
    postgresql-client \
    curl \
    gnupg \
    lsb-release \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
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
