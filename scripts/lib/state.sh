#!/usr/bin/env bash
#
# Busibox State Management Library
#
# Manages persistent state for the interactive menu system.
# State is stored in .busibox-state in the project root.
#
# Usage: source "$(dirname "$0")/lib/state.sh"

# Get repository root (works from any subdirectory)
_get_repo_root() {
    local dir="$1"
    while [[ "$dir" != "/" ]]; do
        if [[ -f "$dir/Makefile" ]] && [[ -d "$dir/scripts" ]]; then
            echo "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    # Fallback: assume we're in scripts/lib or scripts/make
    echo "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
}

# Get container prefix from environment variable or default
_get_env_prefix() {
    local env="${BUSIBOX_ENV:-${ENV:-development}}"
    case "$env" in
        demo) echo "demo" ;;
        development) echo "dev" ;;
        staging) echo "staging" ;;
        production) echo "prod" ;;
        *) echo "dev" ;;
    esac
}

# State file location
# Supports environment-specific state files via BUSIBOX_STATE_FILE or ENV variable
# Examples:
#   .busibox-state-demo    (for make demo)
#   .busibox-state-dev     (for local development)
#   .busibox-state-staging (for staging)
#   .busibox-state-prod    (for production)
BUSIBOX_STATE_FILE="${BUSIBOX_STATE_FILE:-$(_get_repo_root "$(pwd)")/.busibox-state-$(_get_env_prefix)}"

# Get env file path (matches state file naming)
get_env_file_path() {
    echo "$(_get_repo_root "$(pwd)")/.env.$(_get_env_prefix)"
}

# Get vault password file path (in home directory)
get_vault_pass_path() {
    echo "${HOME}/.busibox-vault-pass-$(_get_env_prefix)"
}

# ============================================================================
# State File Format
# ============================================================================
# The state file uses simple KEY=VALUE format:
#
# ENVIRONMENT=development|demo|staging|production
# BACKEND_DEVELOPMENT=docker (always)
# BACKEND_DEMO=docker (always)
# BACKEND_STAGING=docker|proxmox
# BACKEND_PRODUCTION=docker|proxmox
# INSTALL_STATUS=not_installed|installed|configured|deployed|healthy
# LAST_COMMAND="make test-docker SERVICE=agent"
# LAST_COMMAND_TIME="2026-01-16T10:30:00"
# SERVICES_DEPLOYED="authz,postgres,milvus,agent"
#
# Environment behavior:
#   development - Docker with dev overlay (volume mounts, npm link busibox-app)
#   demo        - Docker with prod overlay (for demos, uses GitHub/npm packages)
#   staging     - Docker or Proxmox (10.96.201.x network)
#   production  - Docker or Proxmox (10.96.200.x network)
# ============================================================================

# Initialize state file if it doesn't exist
init_state() {
    if [[ ! -f "$BUSIBOX_STATE_FILE" ]]; then
        cat > "$BUSIBOX_STATE_FILE" << 'EOF'
# Busibox State File
# This file is auto-generated. Do not edit manually unless you know what you're doing.

# Current environment: development, demo, staging, production
ENVIRONMENT=

# Backend type per environment: docker or proxmox
# development and demo are always docker
BACKEND_DEVELOPMENT=docker
BACKEND_DEMO=docker
BACKEND_STAGING=
BACKEND_PRODUCTION=

# Installation status: not_installed, installed, configured, deployed, healthy
INSTALL_STATUS=not_installed

# Last command for re-run feature
LAST_COMMAND=
LAST_COMMAND_TIME=

# Comma-separated list of deployed services
SERVICES_DEPLOYED=
EOF
    fi
}

# Read a value from state file
# Usage: value=$(get_state "ENVIRONMENT")
get_state() {
    local key="$1"
    local default="${2:-}"
    
    init_state
    
    local value
    value=$(grep "^${key}=" "$BUSIBOX_STATE_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-)
    
    # Remove surrounding quotes if present
    value="${value#\"}"
    value="${value%\"}"
    value="${value#\'}"
    value="${value%\'}"
    
    if [[ -n "$value" ]]; then
        echo "$value"
    else
        echo "$default"
    fi
}

# Set a value in state file
# Usage: set_state "ENVIRONMENT" "test"
set_state() {
    local key="$1"
    local value="$2"
    
    init_state
    
    # Escape special characters in value
    local escaped_value
    escaped_value=$(printf '%s' "$value" | sed 's/[&/\]/\\&/g')
    
    # Check if key exists
    if grep -q "^${key}=" "$BUSIBOX_STATE_FILE" 2>/dev/null; then
        # Update existing key
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^${key}=.*|${key}=${escaped_value}|" "$BUSIBOX_STATE_FILE"
        else
            sed -i "s|^${key}=.*|${key}=${escaped_value}|" "$BUSIBOX_STATE_FILE"
        fi
    else
        # Append new key
        echo "${key}=${value}" >> "$BUSIBOX_STATE_FILE"
    fi
}

# Get current environment
get_environment() {
    get_state "ENVIRONMENT" ""
}

# Set current environment
set_environment() {
    local env="$1"
    set_state "ENVIRONMENT" "$env"
}

# Get backend for an environment
# Usage: backend=$(get_backend "staging")
get_backend() {
    local env="$1"
    local key="BACKEND_$(echo "$env" | tr '[:lower:]' '[:upper:]')"
    get_state "$key" ""
}

# Set backend for an environment
# Usage: set_backend "staging" "docker"
set_backend() {
    local env="$1"
    local backend="$2"
    local key="BACKEND_$(echo "$env" | tr '[:lower:]' '[:upper:]')"
    set_state "$key" "$backend"
}

# Get current backend (for current environment)
get_current_backend() {
    local env
    env=$(get_environment)
    if [[ -n "$env" ]]; then
        get_backend "$env"
    else
        echo ""
    fi
}

# Get installation status
# Returns: not_installed, installed, configured, deployed, healthy
get_install_status() {
    get_state "INSTALL_STATUS" "not_installed"
}

# Set installation status
set_install_status() {
    local status="$1"
    set_state "INSTALL_STATUS" "$status"
}

# Check if a feature is available based on install status
# Usage: if is_feature_available "deploy"; then ...
is_feature_available() {
    local feature="$1"
    local status
    status=$(get_install_status)
    
    case "$feature" in
        install|setup)
            # Always available
            return 0
            ;;
        configure)
            # Available after installed
            [[ "$status" == "installed" || "$status" == "configured" || "$status" == "deployed" || "$status" == "healthy" ]]
            ;;
        deploy)
            # Available after configured
            [[ "$status" == "configured" || "$status" == "deployed" || "$status" == "healthy" ]]
            ;;
        test)
            # Available after deployed
            [[ "$status" == "deployed" || "$status" == "healthy" ]]
            ;;
        *)
            return 1
            ;;
    esac
}

# Save last command for re-run feature
# Usage: save_last_command "make test-docker SERVICE=agent"
save_last_command() {
    local command="$1"
    set_state "LAST_COMMAND" "$command"
    set_state "LAST_COMMAND_TIME" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}

# Get last command
get_last_command() {
    get_state "LAST_COMMAND" ""
}

# Get last command time
get_last_command_time() {
    get_state "LAST_COMMAND_TIME" ""
}

# Get human-readable time since last command
get_last_command_ago() {
    local last_time
    last_time=$(get_last_command_time)
    
    if [[ -z "$last_time" ]]; then
        echo "never"
        return
    fi
    
    local now last_epoch now_epoch diff
    
    # Convert to epoch seconds
    if [[ "$OSTYPE" == "darwin"* ]]; then
        last_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$last_time" "+%s" 2>/dev/null || echo "0")
        now_epoch=$(date "+%s")
    else
        last_epoch=$(date -d "$last_time" "+%s" 2>/dev/null || echo "0")
        now_epoch=$(date "+%s")
    fi
    
    if [[ "$last_epoch" == "0" ]]; then
        echo "unknown"
        return
    fi
    
    diff=$((now_epoch - last_epoch))
    
    if [[ $diff -lt 60 ]]; then
        echo "just now"
    elif [[ $diff -lt 3600 ]]; then
        echo "$((diff / 60)) minutes ago"
    elif [[ $diff -lt 86400 ]]; then
        echo "$((diff / 3600)) hours ago"
    else
        echo "$((diff / 86400)) days ago"
    fi
}

# Get deployed services as array
# Usage: services=($(get_deployed_services))
get_deployed_services() {
    local services
    services=$(get_state "SERVICES_DEPLOYED" "")
    if [[ -n "$services" ]]; then
        echo "$services" | tr ',' ' '
    fi
}

# Add a service to deployed list
add_deployed_service() {
    local service="$1"
    local current
    current=$(get_state "SERVICES_DEPLOYED" "")
    
    # Check if already in list
    if [[ ",$current," == *",$service,"* ]]; then
        return 0
    fi
    
    if [[ -n "$current" ]]; then
        set_state "SERVICES_DEPLOYED" "${current},${service}"
    else
        set_state "SERVICES_DEPLOYED" "$service"
    fi
}

# Check if a service is deployed
is_service_deployed() {
    local service="$1"
    local current
    current=$(get_state "SERVICES_DEPLOYED" "")
    [[ ",$current," == *",$service,"* ]]
}

# Clear all deployed services
clear_deployed_services() {
    set_state "SERVICES_DEPLOYED" ""
}

# Reset state to defaults
reset_state() {
    rm -f "$BUSIBOX_STATE_FILE"
    init_state
}

# Export state as environment variables
# Usage: eval "$(export_state)"
export_state() {
    init_state
    echo "export BUSIBOX_ENVIRONMENT=\"$(get_environment)\""
    echo "export BUSIBOX_BACKEND=\"$(get_current_backend)\""
    echo "export BUSIBOX_INSTALL_STATUS=\"$(get_install_status)\""
}

# ============================================================================
# Test Result Tracking
# ============================================================================

# Save test result for a service
# Usage: save_test_result "authz" "passed"
# Usage: save_test_result "ingest" "failed"
save_test_result() {
    local service="$1"
    local result="$2"  # "passed" or "failed"
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    set_state "TEST_RESULT_${service}" "$result"
    set_state "TEST_TIME_${service}" "$timestamp"
}

# Get test result for a service
# Usage: result=$(get_test_result "authz")
# Returns: "passed", "failed", or "" if never run
get_test_result() {
    local service="$1"
    get_state "TEST_RESULT_${service}" ""
}

# Get test time for a service
get_test_time() {
    local service="$1"
    get_state "TEST_TIME_${service}" ""
}

# Get list of failed services
# Usage: failed_services=($(get_failed_services))
# Optional: get_failed_services "services_only" to get only authz/ingest/search/agent (no subtests)
get_failed_services() {
    local filter="${1:-}"
    init_state
    local results
    results=$(grep "^TEST_RESULT_.*=failed" "$BUSIBOX_STATE_FILE" 2>/dev/null | \
        sed 's/^TEST_RESULT_//; s/=failed$//' || true)
    
    if [[ "$filter" == "services_only" ]]; then
        # Only return core service tests without subtests (authz, ingest, search, agent)
        # Exclude entries with colons (like ingest:unit, agent:integration)
        echo "$results" | grep -E "^(authz|ingest|search|agent)$" | grep -v ":" | tr '\n' ' '
    else
        echo "$results" | tr '\n' ' '
    fi
}

# Get list of passed services
# Usage: passed_services=($(get_passed_services))
# Optional: get_passed_services "services_only" to get only authz/ingest/search/agent (no subtests)
get_passed_services() {
    local filter="${1:-}"
    init_state
    local results
    results=$(grep "^TEST_RESULT_.*=passed" "$BUSIBOX_STATE_FILE" 2>/dev/null | \
        sed 's/^TEST_RESULT_//; s/=passed$//' || true)
    
    if [[ "$filter" == "services_only" ]]; then
        # Only return core service tests without subtests (authz, ingest, search, agent)
        # Exclude entries with colons (like ingest:unit, agent:integration)
        echo "$results" | grep -E "^(authz|ingest|search|agent)$" | grep -v ":" | tr '\n' ' '
    else
        echo "$results" | tr '\n' ' '
    fi
}

# Get list of failed app tests
# Usage: failed_apps=($(get_failed_apps))
get_failed_apps() {
    init_state
    grep "^TEST_RESULT_.*=failed" "$BUSIBOX_STATE_FILE" 2>/dev/null | \
        sed 's/^TEST_RESULT_//; s/=failed$//' | \
        grep -E "^(ai-portal|agent-manager)$" | tr '\n' ' ' || true
}

# Get list of passed app tests
# Usage: passed_apps=($(get_passed_apps))
get_passed_apps() {
    init_state
    grep "^TEST_RESULT_.*=passed" "$BUSIBOX_STATE_FILE" 2>/dev/null | \
        sed 's/^TEST_RESULT_//; s/=passed$//' | \
        grep -E "^(ai-portal|agent-manager)$" | tr '\n' ' ' || true
}

# Clear all test results
clear_test_results() {
    init_state
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' '/^TEST_RESULT_/d; /^TEST_TIME_/d' "$BUSIBOX_STATE_FILE"
    else
        sed -i '/^TEST_RESULT_/d; /^TEST_TIME_/d' "$BUSIBOX_STATE_FILE"
    fi
}

# Check if any tests have failed
has_failed_tests() {
    local failed
    failed=$(get_failed_services)
    [[ -n "$failed" ]]
}

# ============================================================================
# Local Development Settings
# ============================================================================

# Get dev apps directory
# Usage: dir=$(get_dev_apps_dir)
get_dev_apps_dir() {
    get_state "DEV_APPS_DIR" ""
}

# Set dev apps directory
# Usage: set_dev_apps_dir "/Users/me/Code"
set_dev_apps_dir() {
    local dir="$1"
    set_state "DEV_APPS_DIR" "$dir"
}

# Display current state (for debugging)
show_state() {
    echo "=== Busibox State ==="
    echo "State file: $BUSIBOX_STATE_FILE"
    echo ""
    if [[ -f "$BUSIBOX_STATE_FILE" ]]; then
        grep -v "^#" "$BUSIBOX_STATE_FILE" | grep -v "^$"
    else
        echo "(no state file)"
    fi
    echo "===================="
}
