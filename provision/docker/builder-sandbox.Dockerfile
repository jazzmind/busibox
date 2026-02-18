# =============================================================================
# Builder Sandbox Container
# =============================================================================
#
# Dedicated development sandbox for AI app building workflows.
# This container hosts per-project source trees and dev servers.
#
# It is intentionally separate from user-apps:
# - user-apps remains isolated for running deployed/untrusted apps
# - builder-sandbox provides a controlled build/dev environment
#
# Process Management:
#   supervisord runs as PID 1 and manages per-project dev servers.
#
# =============================================================================

FROM node:20-slim

# Runtime/development utilities plus supervisord
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        curl \
        procps \
        ca-certificates \
        supervisor \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Project and log directories
RUN mkdir -p /srv/projects /var/log/builder /var/log/supervisor /etc/supervisor/conf.d

# Supervisord base config
COPY provision/docker/builder-sandbox-supervisord.conf /etc/supervisor/supervisord.conf

WORKDIR /srv/projects

# supervisord runs as PID 1 and manages project processes
CMD ["supervisord", "-c", "/etc/supervisor/supervisord.conf"]
