#!/usr/bin/env bash
#
# Busibox State Management Library
#
# Manages persistent state for the interactive menu system.
# Supports the deployment profile system (.busibox/profiles.json) with
# backward compatibility for legacy .busibox-state-* files.
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

# Source profiles library if available
_STATE_REPO_ROOT="$(_get_repo_root "$(pwd)")"
if [[ -f "${_STATE_REPO_ROOT}/scripts/lib/profiles.sh" ]]; then
    # Only source if not already loaded (avoid double-source)
    if [[ -z "${_PROFILES_DIR:-}" ]]; then
        REPO_ROOT="$_STATE_REPO_ROOT" source "${_STATE_REPO_ROOT}/scripts/lib/profiles.sh"
    fi
fi

# Get container prefix from environment variable, active profile, or default
_get_env_prefix() {
    # If profiles system is available and initialized, use it
    if [[ -f "${_STATE_REPO_ROOT}/.busibox/profiles.json" ]] && type profile_get_active &>/dev/null; then
        local active
        active=$(profile_get_active 2>/dev/null)
        if [[ -n "$active" ]]; then
            profile_get_env_prefix "$active"
            return
        fi
    fi

    # Fallback: use BUSIBOX_ENV or ENV
    local env="${BUSIBOX_ENV:-${ENV:-development}}"
    case "$env" in
        demo) echo "demo" ;;
        development) echo "dev" ;;
        staging) echo "staging" ;;
        production) echo "prod" ;;
        *) echo "dev" ;;
    esac
}

# Determine state file path from active profile or legacy behavior
_get_state_file_path() {
    # If BUSIBOX_STATE_FILE is explicitly set (e.g., by install.sh), use it
    if [[ -n "${BUSIBOX_STATE_FILE_OVERRIDE:-}" ]]; then
        echo "$BUSIBOX_STATE_FILE_OVERRIDE"
        return
    fi

    # If profiles system is available and initialized, use active profile's state file
    if [[ -f "${_STATE_REPO_ROOT}/.busibox/profiles.json" ]] && type profile_get_state_file &>/dev/null; then
        local active
        active=$(profile_get_active 2>/dev/null)
        if [[ -n "$active" ]]; then
            local pf
            pf=$(profile_get_state_file "$active" 2>/dev/null)
            if [[ -n "$pf" ]]; then
                echo "$pf"
                return
            fi
        fi
    fi

    # Fallback: legacy behavior
    echo "${_STATE_REPO_ROOT}/.busibox-state-$(_get_env_prefix)"
}

# State file location
# Priority: BUSIBOX_STATE_FILE (explicit) > active profile > legacy env-based
BUSIBOX_STATE_FILE="${BUSIBOX_STATE_FILE:-$(_get_state_file_path)}"

# Get env file path (matches state file naming or profile)
get_env_file_path() {
    # If profiles system is available, use profile's env file
    if [[ -f "${_STATE_REPO_ROOT}/.busibox/profiles.json" ]] && type profile_get_env_file &>/dev/null; then
        local active
        active=$(profile_get_active 2>/dev/null)
        if [[ -n "$active" ]]; then
            local ef
            ef=$(profile_get_env_file "$active" 2>/dev/null)
            if [[ -n "$ef" ]]; then
                echo "$ef"
                return
            fi
        fi
    fi
    # Fallback
    echo "${_STATE_REPO_ROOT}/.env.$(_get_env_prefix)"
}

# Get vault password file path (in home directory)
get_vault_pass_path() {
    # If profiles system is available, use profile's vault prefix
    if [[ -f "${_STATE_REPO_ROOT}/.busibox/profiles.json" ]] && type profile_get_vault_prefix &>/dev/null; then
        local active
        active=$(profile_get_active 2>/dev/null)
        if [[ -n "$active" ]]; then
            local vp
            vp=$(profile_get_vault_prefix "$active" 2>/dev/null)
            if [[ -n "$vp" ]]; then
                echo "${HOME}/.busibox-vault-pass-${vp}"
                return
            fi
        fi
    fi
    # Fallback
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
# BACKEND_STAGING=docker|proxmox|k8s
# BACKEND_PRODUCTION=docker|proxmox|k8s
# INSTALL_STATUS=not_installed|installed|configured|deployed|healthy
# LAST_COMMAND="make test-docker SERVICE=agent"
# LAST_COMMAND_TIME="2026-01-16T10:30:00"
# SERVICES_DEPLOYED="authz,postgres,milvus,agent"
#
# Environment behavior:
#   development - Docker with dev overlay (volume mounts, npm link busibox-app)
#   demo        - Docker with prod overlay (for demos, uses GitHub/npm packages)
#   staging     - Docker, Proxmox (10.96.201.x), or K8s (Rackspace Spot via kubeconfig)
#   production  - Docker, Proxmox (10.96.200.x), or K8s (Rackspace Spot via kubeconfig)
# ============================================================================

# Initialize state file if it doesn't exist
init_state() {
    if [[ ! -f "$BUSIBOX_STATE_FILE" ]]; then
        cat > "$BUSIBOX_STATE_FILE" << 'EOF'
# Busibox State File
# This file is auto-generated. Do not edit manually unless you know what you're doing.

# Current environment: development, demo, staging, production
ENVIRONMENT=

# Backend type per environment: docker, proxmox, or k8s
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
# Usage: save_test_result "data" "failed"
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
# Optional: get_failed_services "services_only" to get only authz/data/search/agent (no subtests)
get_failed_services() {
    local filter="${1:-}"
    init_state
    local results
    results=$(grep "^TEST_RESULT_.*=failed" "$BUSIBOX_STATE_FILE" 2>/dev/null | \
        sed 's/^TEST_RESULT_//; s/=failed$//' || true)
    
    if [[ "$filter" == "services_only" ]]; then
        # Only return core service tests without subtests (authz, data, search, agent)
        # Exclude entries with colons (like data:unit, agent:integration)
        echo "$results" | grep -E "^(authz|data|search|agent)$" | grep -v ":" | tr '\n' ' '
    else
        echo "$results" | tr '\n' ' '
    fi
}

# Get list of passed services
# Usage: passed_services=($(get_passed_services))
# Optional: get_passed_services "services_only" to get only authz/data/search/agent (no subtests)
get_passed_services() {
    local filter="${1:-}"
    init_state
    local results
    results=$(grep "^TEST_RESULT_.*=passed" "$BUSIBOX_STATE_FILE" 2>/dev/null | \
        sed 's/^TEST_RESULT_//; s/=passed$//' || true)
    
    if [[ "$filter" == "services_only" ]]; then
        # Only return core service tests without subtests (authz, data, search, agent)
        # Exclude entries with colons (like data:unit, agent:integration)
        echo "$results" | grep -E "^(authz|data|search|agent)$" | grep -v ":" | tr '\n' ' '
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
        grep -E "^(busibox-portal|busibox-agents)$" | tr '\n' ' ' || true
}

# Get list of passed app tests
# Usage: passed_apps=($(get_passed_apps))
get_passed_apps() {
    init_state
    grep "^TEST_RESULT_.*=passed" "$BUSIBOX_STATE_FILE" 2>/dev/null | \
        sed 's/^TEST_RESULT_//; s/=passed$//' | \
        grep -E "^(busibox-portal|busibox-agents)$" | tr '\n' ' ' || true
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

# Get core apps mode for Docker local dev
# Returns: "dev" (Turbopack hot-reload) or "prod" (standalone, default)
# Usage: mode=$(get_core_apps_mode)
get_core_apps_mode() {
    get_state "CORE_APPS_MODE" "prod"
}

# Set core apps mode for Docker local dev
# Usage: set_core_apps_mode "dev"  # Enable Core Developer Mode (hot-reload)
# Usage: set_core_apps_mode "prod" # Disable Core Developer Mode (standalone, memory-efficient)
set_core_apps_mode() {
    local mode="$1"
    if [[ "$mode" != "dev" && "$mode" != "prod" ]]; then
        echo "ERROR: core apps mode must be 'dev' or 'prod'" >&2
        return 1
    fi
    set_state "CORE_APPS_MODE" "$mode"
}

# Get core apps source for Docker local dev
# Returns: "legacy" (separate repos) or "monorepo" (busibox-frontend, default)
# Usage: source=$(get_core_apps_source)
get_core_apps_source() {
    get_state "CORE_APPS_SOURCE" "legacy"
}

# Set core apps source for Docker local dev
# Usage: set_core_apps_source "monorepo"  # Use busibox-frontend monorepo
# Usage: set_core_apps_source "legacy"    # Use separate busibox-portal/busibox-agents repos
set_core_apps_source() {
    local source="$1"
    if [[ "$source" != "legacy" && "$source" != "monorepo" ]]; then
        echo "ERROR: core apps source must be 'legacy' or 'monorepo'" >&2
        return 1
    fi
    set_state "CORE_APPS_SOURCE" "$source"
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

# ============================================================================
# Version Tracking
# ============================================================================
# Tracks deployed versions for repositories:
#   DEPLOYED_<REPO>_TYPE = branch | release
#   DEPLOYED_<REPO>_REF = branch name or release tag
#   DEPLOYED_<REPO>_COMMIT = short commit SHA
#   DEPLOYED_<REPO>_TIME = ISO timestamp of deployment

# List of tracked repositories
TRACKED_REPO_KEYS="busibox busibox-portal busibox-agents busibox-app"

# Save deployed version for a repository
# Usage: save_deployed_version "busibox" "branch" "main" "abc1234"
# Usage: save_deployed_version "busibox-portal" "release" "v1.2.3" "def5678"
save_deployed_version() {
    local repo_key="$1"
    local version_type="$2"  # "branch" or "release"
    local ref="$3"           # branch name or tag
    local commit="$4"        # short commit SHA
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    # Normalize repo key to uppercase for state keys
    local key_prefix="DEPLOYED_$(echo "$repo_key" | tr '[:lower:]-' '[:upper:]_')"
    
    set_state "${key_prefix}_TYPE" "$version_type"
    set_state "${key_prefix}_REF" "$ref"
    set_state "${key_prefix}_COMMIT" "$commit"
    set_state "${key_prefix}_TIME" "$timestamp"
}

# Get deployed version for a repository
# Usage: info=$(get_deployed_version "busibox")
# Returns: "type:ref:commit" or empty if not tracked
get_deployed_version() {
    local repo_key="$1"
    local key_prefix="DEPLOYED_$(echo "$repo_key" | tr '[:lower:]-' '[:upper:]_')"
    
    local version_type ref commit
    version_type=$(get_state "${key_prefix}_TYPE" "")
    ref=$(get_state "${key_prefix}_REF" "")
    commit=$(get_state "${key_prefix}_COMMIT" "")
    
    if [[ -n "$version_type" ]] && [[ -n "$ref" ]]; then
        echo "${version_type}:${ref}:${commit}"
    fi
}

# Get deployed version time
# Usage: time=$(get_deployed_version_time "busibox")
get_deployed_version_time() {
    local repo_key="$1"
    local key_prefix="DEPLOYED_$(echo "$repo_key" | tr '[:lower:]-' '[:upper:]_')"
    get_state "${key_prefix}_TIME" ""
}

# Get deployed version type (branch or release)
# Usage: type=$(get_deployed_version_type "busibox")
get_deployed_version_type() {
    local repo_key="$1"
    local key_prefix="DEPLOYED_$(echo "$repo_key" | tr '[:lower:]-' '[:upper:]_')"
    get_state "${key_prefix}_TYPE" ""
}

# Get deployed version ref (branch name or release tag)
# Usage: ref=$(get_deployed_version_ref "busibox")
get_deployed_version_ref() {
    local repo_key="$1"
    local key_prefix="DEPLOYED_$(echo "$repo_key" | tr '[:lower:]-' '[:upper:]_')"
    get_state "${key_prefix}_REF" ""
}

# Get deployed version commit
# Usage: commit=$(get_deployed_version_commit "busibox")
get_deployed_version_commit() {
    local repo_key="$1"
    local key_prefix="DEPLOYED_$(echo "$repo_key" | tr '[:lower:]-' '[:upper:]_')"
    get_state "${key_prefix}_COMMIT" ""
}

# Clear deployed version for a repository
# Usage: clear_deployed_version "busibox"
clear_deployed_version() {
    local repo_key="$1"
    local key_prefix="DEPLOYED_$(echo "$repo_key" | tr '[:lower:]-' '[:upper:]_')"
    
    set_state "${key_prefix}_TYPE" ""
    set_state "${key_prefix}_REF" ""
    set_state "${key_prefix}_COMMIT" ""
    set_state "${key_prefix}_TIME" ""
}

# Clear all deployed versions
# Usage: clear_all_deployed_versions
clear_all_deployed_versions() {
    for repo_key in $TRACKED_REPO_KEYS; do
        clear_deployed_version "$repo_key"
    done
}

# Get human-readable time since deployment
# Usage: ago=$(get_deployed_version_ago "busibox")
get_deployed_version_ago() {
    local repo_key="$1"
    local deploy_time
    deploy_time=$(get_deployed_version_time "$repo_key")
    
    if [[ -z "$deploy_time" ]]; then
        echo "never"
        return
    fi
    
    local now last_epoch now_epoch diff
    
    # Convert to epoch seconds
    if [[ "$OSTYPE" == "darwin"* ]]; then
        last_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$deploy_time" "+%s" 2>/dev/null || echo "0")
        now_epoch=$(date "+%s")
    else
        last_epoch=$(date -d "$deploy_time" "+%s" 2>/dev/null || echo "0")
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

# Check if any repository has a deployed version tracked
# Usage: if has_deployed_versions; then ...
has_deployed_versions() {
    for repo_key in $TRACKED_REPO_KEYS; do
        local version
        version=$(get_deployed_version "$repo_key")
        if [[ -n "$version" ]]; then
            return 0
        fi
    done
    return 1
}

# Display deployed versions summary
# Usage: show_deployed_versions
show_deployed_versions() {
    echo "=== Deployed Versions ==="
    for repo_key in $TRACKED_REPO_KEYS; do
        local version type ref commit ago
        version=$(get_deployed_version "$repo_key")
        if [[ -n "$version" ]]; then
            type=$(get_deployed_version_type "$repo_key")
            ref=$(get_deployed_version_ref "$repo_key")
            commit=$(get_deployed_version_commit "$repo_key")
            ago=$(get_deployed_version_ago "$repo_key")
            printf "  %-15s %s:%s@%s (%s)\n" "$repo_key" "$type" "$ref" "$commit" "$ago"
        else
            printf "  %-15s (not tracked)\n" "$repo_key"
        fi
    done
    echo "========================="
}
