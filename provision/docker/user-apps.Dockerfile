# =============================================================================
# User Apps Container - Sandboxed Environment for User-Deployed Applications
# =============================================================================
#
# This container runs UNTRUSTED user-deployed applications.
# Security isolation is critical - all app code executes inside this container.
#
# Pre-installs common tools needed for app deployment to avoid apt permission
# issues at runtime.
#
# Process Management:
#   Uses supervisord to manage dynamically deployed applications.
#   Deploy-api creates per-app .conf files in /etc/supervisor/conf.d/
#   and uses `supervisorctl update` to hot-load them.
#
#   supervisord provides:
#     - Automatic restart on crash (autorestart=true)
#     - Log capture to /var/log/user-apps/<app>.log
#     - Clean process lifecycle management
#     - Status querying via supervisorctl
#
# =============================================================================

FROM node:20-slim

# Install common tools needed for app deployment + supervisord for process management
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        curl \
        procps \
        ca-certificates \
        supervisor \
    && rm -rf /var/lib/apt/lists/*

# Create app directories and supervisor config directory
RUN mkdir -p /srv/apps /srv/dev-apps /var/log/user-apps /var/log/supervisor /etc/supervisor/conf.d

# Copy supervisord base configuration
COPY provision/docker/user-apps-supervisord.conf /etc/supervisor/supervisord.conf

WORKDIR /srv/apps

# supervisord runs as PID 1 - manages all app processes
CMD ["supervisord", "-c", "/etc/supervisor/supervisord.conf"]
