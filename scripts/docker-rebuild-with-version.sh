#!/usr/bin/env bash
# =============================================================================
# Docker Rebuild with Version Labels
# =============================================================================
#
# Execution Context: Admin workstation
# Purpose: Rebuild Docker containers with git commit version labels
#
# This script:
# 1. Gets the current git commit hash
# 2. Exports it as GIT_COMMIT environment variable
# 3. Rebuilds Docker containers with version labels
#
# Usage:
#   bash scripts/docker-rebuild-with-version.sh
#
# =============================================================================

set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source UI library
source "${REPO_ROOT}/scripts/lib/ui.sh"

# Get current git commit
GIT_COMMIT=$(git rev-parse --short HEAD)
export GIT_COMMIT

info "Rebuilding Docker containers with version: ${GIT_COMMIT}"
echo ""

# Stop existing containers
info "Stopping existing containers..."
docker compose -f "${REPO_ROOT}/docker-compose.local.yml" down

# Rebuild with version labels
info "Building containers with version labels..."
GIT_COMMIT="${GIT_COMMIT}" docker compose -f "${REPO_ROOT}/docker-compose.local.yml" build \
    authz-api \
    ingest-api \
    search-api \
    agent-api \
    docs-api

# Start containers
info "Starting containers..."
docker compose -f "${REPO_ROOT}/docker-compose.local.yml" up -d

echo ""
success "Docker containers rebuilt with version: ${GIT_COMMIT}"
echo ""
info "Verify with: docker inspect local-ingest-api --format '{{.Config.Labels.version}}'"
