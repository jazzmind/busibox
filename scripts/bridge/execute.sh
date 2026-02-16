#!/usr/bin/env bash
#
# Bridge Script for Deploy-API
# ============================
#
# This script provides a secure passthrough for deploy-api (running in container)
# to execute Makefile targets on the host system.
#
# Usage:
#   execute.sh <command> [args...]
#
# Examples:
#   execute.sh make deploy-busibox-portal INV=inventory/staging
#   execute.sh make docker-deploy-frontend
#   execute.sh make update ENV=staging
#
# Security:
#   - Only allows execution of approved commands (make targets)
#   - Runs from the busibox repository root
#   - Logs all executions for audit
#
# The deploy-api container mounts this script and calls it to execute
# trusted operations that require host access (Ansible, SSH, etc.)
#

set -euo pipefail

# Get script directory and repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Log file for audit trail
LOG_DIR="${REPO_ROOT}/.bridge-logs"
LOG_FILE="${LOG_DIR}/bridge-$(date +%Y%m%d).log"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# =============================================================================
# LOGGING
# =============================================================================

log() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $*" >> "$LOG_FILE"
    echo "$*"
}

log_error() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] ERROR: $*" >> "$LOG_FILE"
    echo "ERROR: $*" >&2
}

# =============================================================================
# COMMAND VALIDATION
# =============================================================================

# List of allowed command prefixes
# Only these commands can be executed via the bridge
ALLOWED_COMMANDS=(
    "make deploy-"
    "make docker-deploy"
    "make update"
    "make docker-status"
    "make docker-logs"
)

validate_command() {
    local cmd="$1"
    
    for allowed in "${ALLOWED_COMMANDS[@]}"; do
        if [[ "$cmd" == $allowed* ]]; then
            return 0
        fi
    done
    
    return 1
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    if [[ $# -lt 1 ]]; then
        log_error "No command provided"
        echo "Usage: $0 <command> [args...]"
        exit 1
    fi
    
    # Build full command string for validation
    local full_cmd="$*"
    
    # Validate command
    if ! validate_command "$full_cmd"; then
        log_error "Command not allowed: $full_cmd"
        echo "ERROR: Command not allowed. Only deployment commands are permitted."
        exit 1
    fi
    
    # Log the execution
    log "Executing: $full_cmd"
    log "Working directory: $REPO_ROOT"
    
    # Change to repo root and execute
    cd "$REPO_ROOT"
    
    # Execute the command
    # Use exec to replace this shell with the command
    exec "$@"
}

main "$@"
