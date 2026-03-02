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

# Source libraries (profiles first, then state which depends on it)
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/profiles.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"

# Source backend libraries
source "${REPO_ROOT}/scripts/lib/backends/common.sh"

# Initialize profiles
profile_init

# Active profile info
_active_profile=$(profile_get_active)

# Set BUSIBOX_ENV from active profile so state.sh and backends pick it up
if [[ -n "$_active_profile" ]]; then
    export BUSIBOX_ENV=$(profile_get "$_active_profile" "environment")
fi

# ============================================================================
# Functions
# ============================================================================

# Get the current environment (profile-aware)
get_current_env() {
    # Prefer active profile
    if [[ -n "$_active_profile" ]]; then
        profile_get "$_active_profile" "environment"
        return
    fi

    # Fallback to BUSIBOX_ENV
    if [[ -n "${BUSIBOX_ENV:-}" ]]; then
        echo "$BUSIBOX_ENV"
        return
    fi

    # Fallback to state file
    local env
    env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "")

    if [[ -z "$env" ]]; then
        env="development"
    fi

    echo "$env"
}

# Get the backend type for the environment (profile-aware)
get_backend_type() {
    local env="$1"
    local backend=""

    # Prefer active profile
    if [[ -n "$_active_profile" ]]; then
        backend=$(profile_get "$_active_profile" "backend")
    fi

    # Fallback to state file
    if [[ -z "$backend" ]]; then
        backend=$(get_backend "$env" 2>/dev/null || echo "")
    fi

    if [[ -z "$backend" ]]; then
        case "$env" in
            development|demo) backend="docker" ;;
            *) backend="docker" ;;
        esac
    fi

    # Normalize to lowercase (profiles may store mixed case)
    echo "$backend" | tr '[:upper:]' '[:lower:]'
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
        echo "Core Developer Mode (dev mode with volume mounts only):"
        echo "  make manage SERVICE=core-apps  # Toggle via interactive menu (options 8, 9)"
        echo "  CORE_APPS_MODE=dev  make manage SERVICE=core-apps ACTION=restart  # Force dev mode (hot-reload)"
        echo "  CORE_APPS_MODE=prod make manage SERVICE=core-apps ACTION=restart  # Force prod mode (default)"
        echo ""
        echo "Core Apps Source (switch between monorepo and legacy repos):"
        echo "  CORE_APPS_SOURCE=monorepo make manage SERVICE=core-apps ACTION=restart  # Use busibox-frontend"
        echo "  CORE_APPS_SOURCE=legacy   make manage SERVICE=core-apps ACTION=restart  # Use separate repos"
        echo ""
        echo "Services: postgres, redis, minio, milvus, authz, agent, data,"
        echo "          search, deploy, docs, embedding, litellm, mlx,"
        echo "          core-apps, busibox-portal, busibox-admin, busibox-agents,"
        echo "          busibox-chat, busibox-appbuilder, busibox-media,"
        echo "          busibox-documents, nginx"
        echo ""
        echo "Actions: start, stop, restart, logs, status, redeploy, sync (mlx only)"
        echo ""
        exit 1
    fi

    # Validate action
    if ! validate_action "$action"; then
        error "Unknown action: $action"
        echo ""
        echo "Valid actions: start, stop, restart, logs, status, redeploy, sync"
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
