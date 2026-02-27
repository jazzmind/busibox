#!/usr/bin/env bash
# =============================================================================
# Manager Container Runner
# =============================================================================
# Execution context: Admin workstation (host)
# Purpose: Run a command inside the busibox manager container
#
# This script:
#   1. Ensures the manager Docker image exists (builds if needed)
#   2. Discovers vault password files on the host
#   3. Launches an ephemeral container with proper volume mounts
#   4. Passes the command through to execute inside the container
#
# Usage:
#   bash scripts/make/manager-run.sh <command> [args...]
#   bash scripts/make/manager-run.sh bash scripts/make/service-deploy.sh authz
#   bash scripts/make/manager-run.sh --interactive bash scripts/make/launcher.sh
#
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MANAGER_IMAGE="busibox-manager:latest"
MANAGER_DOCKERFILE="provision/docker/manager.Dockerfile"

# Parse flags
INTERACTIVE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --interactive|-it)
            INTERACTIVE="yes"
            shift
            ;;
        *)
            break
            ;;
    esac
done

if [[ $# -eq 0 ]]; then
    set -- bash
fi

# ─── Ensure manager image exists ─────────────────────────────────────────────
if ! docker image inspect "${MANAGER_IMAGE}" >/dev/null 2>&1; then
    echo "[manager] Building manager container image (first run)..."
    docker build -t "${MANAGER_IMAGE}" \
        -f "${REPO_ROOT}/${MANAGER_DOCKERFILE}" \
        "${REPO_ROOT}/provision/docker" >&2
    echo "[manager] Image built successfully." >&2
fi

# ─── Assemble docker run arguments ───────────────────────────────────────────
# Mount the repo at the SAME path as on the host so that paths seen by the
# Docker daemon (via socket mount) match the container's filesystem.  This
# avoids build-context and volume-mount mismatches when docker compose is
# invoked from inside the manager.
DOCKER_ARGS=(
    run --rm
    --workdir "${REPO_ROOT}"
)

# Interactive TTY support
if [[ -n "$INTERACTIVE" ]]; then
    DOCKER_ARGS+=(-it)
fi

# ─── Volume mounts ───────────────────────────────────────────────────────────

# Docker socket
DOCKER_ARGS+=(-v /var/run/docker.sock:/var/run/docker.sock)

# Busibox repo at its real host path (read-write -- install scripts write state/env/ssl files)
DOCKER_ARGS+=(-v "${REPO_ROOT}:${REPO_ROOT}:rw")

# SSH keys for Proxmox backend
if [[ -d "${HOME}/.ssh" ]]; then
    DOCKER_ARGS+=(-v "${HOME}/.ssh:/root/.ssh:ro")
fi

# Vault password files: mount each one found in the host's home directory
# so existing installations can still read them. New vault pass files are
# created in the repo root (BUSIBOX_VAULT_PASS_DIR) which is already
# mounted read-write.
for vf in "${HOME}"/.busibox-vault-pass-*; do
    if [[ -f "$vf" ]]; then
        local_basename="$(basename "$vf")"
        DOCKER_ARGS+=(-v "${vf}:/root/${local_basename}:ro")
    fi
done

# Legacy vault pass file
if [[ -f "${HOME}/.vault_pass" ]]; then
    DOCKER_ARGS+=(-v "${HOME}/.vault_pass:/root/.vault_pass:ro")
fi

# ─── Environment variables ───────────────────────────────────────────────────

# Host path for docker compose volume resolution
DOCKER_ARGS+=(-e "BUSIBOX_HOST_PATH=${REPO_ROOT}")

# Vault password files are stored in the repo root inside the manager
# container (since HOME=/root is ephemeral). The repo root is mounted rw.
DOCKER_ARGS+=(-e "BUSIBOX_VAULT_PASS_DIR=${REPO_ROOT}")

# Absolute host path for SSH key mount resolution inside docker compose.
# Needed because manager container sets HOME=/root, which would otherwise
# expand ~/.ssh to /root/.ssh on the host daemon (invalid on macOS).
DOCKER_ARGS+=(-e "HOST_SSH_DIR=${HOME}/.ssh")

# Container prefix and project name
DOCKER_ARGS+=(-e "CONTAINER_PREFIX=${CONTAINER_PREFIX:-dev}")
DOCKER_ARGS+=(-e "COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-dev-busibox}")
DOCKER_ARGS+=(-e "BUSIBOX_ENV=${BUSIBOX_ENV:-development}")

# HOME=/root inside the container so scripts find mounted vault files
DOCKER_ARGS+=(-e "HOME=/root")

# Terminal
DOCKER_ARGS+=(-e "TERM=${TERM:-xterm-256color}")

# Forward host platform info so scripts inside the manager container
# can detect capabilities without relying on uname (which would report
# the container's Linux arch, not the host's).
HOST_OS="${HOST_OS:-$(uname -s)}"
HOST_ARCH="${HOST_ARCH:-$(uname -m)}"
if [[ -z "${HOST_RAM_GB:-}" ]]; then
    if [[ "$HOST_OS" == "Darwin" ]]; then
        _ram_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo "")
        if [[ -n "$_ram_bytes" && "$_ram_bytes" =~ ^[0-9]+$ ]]; then
            HOST_RAM_GB=$((_ram_bytes / 1024 / 1024 / 1024))
        fi
    else
        _mem_kb=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo "")
        if [[ -n "$_mem_kb" && "$_mem_kb" =~ ^[0-9]+$ ]]; then
            HOST_RAM_GB=$((_mem_kb / 1024 / 1024))
        fi
    fi
fi
DOCKER_ARGS+=(-e "HOST_OS=${HOST_OS}")
DOCKER_ARGS+=(-e "HOST_ARCH=${HOST_ARCH}")
DOCKER_ARGS+=(-e "HOST_RAM_GB=${HOST_RAM_GB:-}")

# Auto-detect LLM_BACKEND from host platform if not already set
if [[ -z "${LLM_BACKEND:-}" ]]; then
    if [[ "$HOST_OS" == "Darwin" && ("$HOST_ARCH" == "arm64" || "$HOST_ARCH" == "aarch64") ]]; then
        LLM_BACKEND="mlx"
    elif command -v nvidia-smi &>/dev/null && nvidia-smi -L &>/dev/null 2>&1; then
        LLM_BACKEND="vllm"
    fi
fi

# Auto-detect HOST_AGENT_TOKEN from .env file if not already set
if [[ -z "${HOST_AGENT_TOKEN:-}" ]]; then
    _env_prefix="${CONTAINER_PREFIX:-dev}"
    _env_file="${REPO_ROOT}/.env.${_env_prefix}"
    if [[ -f "$_env_file" ]]; then
        HOST_AGENT_TOKEN=$(awk -F= '/^HOST_AGENT_TOKEN=/{val=substr($0, index($0,$2))} END{print val}' "$_env_file" 2>/dev/null | tr -d '\r\n')
    fi
fi

# Pass through optional overrides if set in caller's environment
for var in GITHUB_AUTH_TOKEN CORE_APPS_MODE CORE_APPS_SOURCE DEPLOY_REF \
           USE_ANSIBLE_FOR_DOCKER USE_ANSIBLE VERBOSE DEV_APPS_DIR LLM_BACKEND HOST_AGENT_TOKEN; do
    if [[ -n "${!var:-}" ]]; then
        DOCKER_ARGS+=(-e "${var}=${!var}")
    fi
done

# ─── Network ─────────────────────────────────────────────────────────────────
# Try to join the busibox network if it exists (needed for service health checks).
# During bootstrap the network may not exist yet, which is fine.
NETWORK_NAME="${CONTAINER_PREFIX:-dev}-busibox-net"
if docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
    DOCKER_ARGS+=(--network "${NETWORK_NAME}")
fi

# Ensure host.docker.internal resolves inside the container (Linux hosts
# without Docker Desktop need this; macOS Docker Desktop provides it natively).
if [[ "$(uname -s)" == "Linux" ]]; then
    DOCKER_ARGS+=(--add-host=host.docker.internal:host-gateway)
fi

# ─── Run ─────────────────────────────────────────────────────────────────────
# Construct the full command string for bash -c
CMD_STRING="$*"

exec docker "${DOCKER_ARGS[@]}" "${MANAGER_IMAGE}" "${CMD_STRING}"
