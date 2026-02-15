#!/usr/bin/env bash
#
# Busibox Service Management
# ==========================
#
# Manage specific service(s) via the backend-specific implementation,
# automatically detecting the current environment and backend.
#
# Usage:
#   make manage SERVICE=authz ACTION=restart
#   make manage SERVICE=authz,agent ACTION=stop
#   make manage SERVICE=authz ACTION=logs
#   bash scripts/make/service-manage.sh authz restart
#
# Actions:
#   start    - Start the service
#   stop     - Stop the service
#   restart  - Restart the service
#   logs     - View service logs (follows)
#   status   - Show service status
#   redeploy - Rebuild and restart (Docker) or redeploy (Ansible/K8s)
#
set -eo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Auto-detect deployed environment BEFORE sourcing state library
_auto_detect_env() {
    if [[ -n "${BUSIBOX_ENV:-}" ]]; then
        echo "$BUSIBOX_ENV"
        return
    fi

    # Look for state files in order of likelihood
    if [[ -f "${REPO_ROOT}/.busibox-state-prod" ]]; then
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^prod-"; then
            echo "production"
            return
        fi
    fi

    if [[ -f "${REPO_ROOT}/.busibox-state-staging" ]]; then
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^staging-"; then
            echo "staging"
            return
        fi
    fi

    if [[ -f "${REPO_ROOT}/.busibox-state-demo" ]]; then
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^demo-"; then
            echo "demo"
            return
        fi
    fi

    if [[ -f "${REPO_ROOT}/.busibox-state-dev" ]]; then
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^dev-"; then
            echo "development"
            return
        fi
    fi

    # Fallback: check which containers are actually running
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^prod-"; then
        echo "production"
    elif docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^staging-"; then
        echo "staging"
    elif docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^demo-"; then
        echo "demo"
    else
        echo "development"
    fi
}

# Set environment before sourcing state library
export BUSIBOX_ENV="${BUSIBOX_ENV:-$(_auto_detect_env)}"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"

# Source backend libraries
source "${REPO_ROOT}/scripts/lib/backends/common.sh"

# ============================================================================
# Functions
# ============================================================================

# Get the current environment from state
get_current_env() {
    local env
    env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "")

    if [[ -z "$env" ]]; then
        if [[ -f "${REPO_ROOT}/.busibox-state-prod" ]]; then
            env="production"
        elif [[ -f "${REPO_ROOT}/.busibox-state-staging" ]]; then
            env="staging"
        elif [[ -f "${REPO_ROOT}/.busibox-state-demo" ]]; then
            env="demo"
        else
            env="development"
        fi
    fi

    echo "$env"
}

# Get the backend type for the environment
get_backend_type() {
    local env="$1"
    local backend
    backend=$(get_backend "$env" 2>/dev/null || echo "")

    if [[ -z "$backend" ]]; then
        case "$env" in
            development|demo) backend="docker" ;;
            staging|production) backend="docker" ;;
            *) backend="docker" ;;
        esac
    fi

    echo "$backend"
}

# Map environment to container prefix
get_container_prefix() {
    local env="$1"
    case "$env" in
        demo) echo "demo" ;;
        development) echo "dev" ;;
        staging) echo "staging" ;;
        production) echo "prod" ;;
        *) echo "dev" ;;
    esac
}

# ============================================================================
# Main
# ============================================================================

main() {
    local services_input="${1:-}"
    local action="${2:-status}"

    if [[ -z "$services_input" ]]; then
        error "No service specified"
        echo ""
        echo "Usage: make manage SERVICE=<service>[,<service>...] ACTION=<action>"
        echo ""
        echo "Examples:"
        echo "  make manage SERVICE=authz ACTION=restart"
        echo "  make manage SERVICE=authz,agent ACTION=stop"
        echo "  make manage SERVICE=authz ACTION=logs"
        echo "  make manage SERVICE=authz ACTION=status"
        echo ""
        echo "Core-apps mode switching (dev mode with volume mounts only):"
        echo "  CORE_APPS_MODE=prod make manage SERVICE=core-apps ACTION=restart"
        echo "  CORE_APPS_MODE=dev  make manage SERVICE=core-apps ACTION=restart"
        echo ""
        echo "Services: postgres, redis, minio, milvus, authz, agent, data,"
        echo "          search, deploy, docs, embedding, litellm, mlx, core-apps, nginx"
        echo ""
        echo "Actions: start, stop, restart, logs, status, redeploy"
        echo ""
        exit 1
    fi

    # Validate action
    if ! validate_action "$action"; then
        error "Unknown action: $action"
        echo ""
        echo "Valid actions: start, stop, restart, logs, status, redeploy"
        exit 1
    fi

    # Get environment info
    local env backend prefix
    env=$(get_current_env)
    backend=$(get_backend_type "$env")
    prefix=$(get_container_prefix "$env")

    # Set globals for backend files
    export CONTAINER_PREFIX="$prefix"
    export CURRENT_ENV="$env"

    # Load the appropriate backend
    load_backend "$backend"

    # Split services by comma
    IFS=',' read -ra services <<< "$services_input"

    # Only show header for non-logs actions
    if [[ "$action" != "logs" ]]; then
        echo ""
        box_start 70 single "$CYAN"
        box_header "SERVICE MANAGEMENT"
        box_empty
        box_line "  Environment: ${BOLD}${env}${NC}"
        box_line "  Backend:     ${BOLD}${backend}${NC}"
        box_line "  Action:      ${BOLD}${action}${NC}"
        box_empty
        box_footer
        echo ""
    fi

    local any_failed=false

    # Expand services to include companions
    local expanded_services=()
    for service in "${services[@]}"; do
        service=$(echo "$service" | xargs)
        expanded_services+=("$service")
        # For redeploy, skip companion expansion - the Ansible playbook already
        # deploys all services under the same tag (e.g. --tags data deploys both
        # data-api and data-worker). Expanding companions would run it twice.
        if [[ "$action" != "redeploy" ]]; then
            local companions
            companions=$(get_companion_services "$service")
            if [[ -n "$companions" ]]; then
                for companion in $companions; do
                    local already_listed=false
                    for existing in "${expanded_services[@]}"; do
                        if [[ "$existing" == "$companion" ]]; then
                            already_listed=true
                            break
                        fi
                    done
                    if ! $already_listed; then
                        expanded_services+=("$companion")
                    fi
                done
            fi
        fi
    done

    for service in "${expanded_services[@]}"; do
        if ! is_valid_service "$service"; then
            error "Unknown service: $service"
            any_failed=true
            continue
        fi

        # Host-native services bypass the backend
        if is_host_native_service "$service"; then
            host_native_action "$service" "$action" || any_failed=true
        else
            backend_service_action "$service" "$action" "$env" "$prefix" || any_failed=true
        fi
    done

    if $any_failed; then
        exit 1
    fi
}

main "$@"
