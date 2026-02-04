# =============================================================================
# User Apps Container - Sandboxed Environment for User-Deployed Applications
# =============================================================================
#
# This container runs UNTRUSTED user-deployed applications.
# Security isolation is critical - all app code executes inside this container.
#
# Pre-installs common tools needed for app deployment to avoid apt permission
# issues at runtime.
# =============================================================================

FROM node:20-slim

# Install common tools needed for app deployment
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        curl \
        procps \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create app directories
RUN mkdir -p /srv/apps /srv/dev-apps /var/log/user-apps

WORKDIR /srv/apps

# Container stays running - deploy-api uses docker exec to manage apps
CMD ["sleep", "infinity"]
