# =============================================================================
# Busibox Manager Container
# =============================================================================
#
# Ephemeral container for running make install, make manage, and other
# orchestration commands. Ensures all dependencies (Ansible, Docker CLI,
# vault tools, SSH) are available regardless of host environment.
#
# This container is NOT a long-running service. It is invoked via:
#   docker compose run --rm manager <command>
#
# The Makefile delegates operations to this container automatically.
#
# =============================================================================

FROM python:3.11-slim

# Pinned dependency versions
ARG ANSIBLE_VERSION=10.7.0
ARG TARGETARCH

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    git \
    openssh-client \
    openssl \
    make \
    jq \
    ca-certificates \
    gnupg \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI only (no daemon -- talks to host socket)
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        docker-ce-cli \
        docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# Install Ansible and Python dependencies (PyYAML is bundled with Ansible)
RUN pip install --no-cache-dir \
    ansible==${ANSIBLE_VERSION} \
    jmespath

WORKDIR /busibox

# Entrypoint runs bash by default, allowing arbitrary commands
ENTRYPOINT ["/bin/bash", "-c"]
CMD ["bash"]
