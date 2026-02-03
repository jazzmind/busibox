#!/usr/bin/env bash
#
# Busibox Service Management
# ==========================
#
# Manage specific service(s) via Ansible or Docker commands,
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
#   redeploy - Rebuild and restart (Docker) or redeploy (Ansible)
#
set -eo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Auto-detect deployed environment BEFORE sourcing state library
# This allows the state library to use the correct state file
_auto_detect_env() {
    # If BUSIBOX_ENV is already set, use it
    if [[ -n "${BUSIBOX_ENV:-}" ]]; then
        echo "$BUSIBOX_ENV"
        return
    fi
    
    # Look for state files in order of likelihood
    # Note: Check prod first since it's most critical to get right
    if [[ -f "${REPO_ROOT}/.busibox-state-prod" ]]; then
        # Check if prod containers are actually running
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

# ============================================================================
# Configuration
# ============================================================================

# Map service names to Docker container names (without prefix)
get_container_for_service() {
    local service="$1"
    case "$service" in
        # Infrastructure
        postgres|pg) echo "postgres" ;;
        redis) echo "redis" ;;
        minio|files) echo "minio" ;;
        milvus) echo "milvus" ;;
        etcd) echo "etcd" ;;
        
        # APIs
        authz|authz-api) echo "authz-api" ;;
        agent|agent-api) echo "agent-api" ;;
        ingest|data-api) echo "data-api" ;;
        data-worker) echo "data-worker" ;;
        search|search-api) echo "search-api" ;;
        deploy|deploy-api) echo "deploy-api" ;;
        docs|docs-api) echo "docs-api" ;;
        embedding|embedding-api) echo "embedding-api" ;;
        
        # LLM
        litellm) echo "litellm" ;;
        vllm) echo "vllm" ;;
        # NOTE: ollama is deprecated - use vLLM instead
        
        # Frontend
        core-apps|apps|ai-portal|agent-manager) echo "core-apps" ;;
        nginx|proxy) echo "nginx" ;;
        
        # User apps
        user-apps) echo "user-apps" ;;
        
        # Unknown - return as-is
        *) echo "" ;;
    esac
}

# Check if service is valid
is_valid_service() {
    local service="$1"
    local container
    container=$(get_container_for_service "$service")
    [[ -n "$container" ]]
}

# ============================================================================
# Functions
# ============================================================================

# Get the current environment from state
get_current_env() {
    local env
    env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "")
    
    if [[ -z "$env" ]]; then
        # Try to detect from state file existence
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

# Get full container name
get_full_container_name() {
    local service="$1"
    local prefix="$2"
    local container
    container=$(get_container_for_service "$service")
    echo "${prefix}-${container}"
}

# Validate action
validate_action() {
    local action="$1"
    case "$action" in
        start|stop|restart|logs|status|redeploy) return 0 ;;
        *) return 1 ;;
    esac
}

# Get service status (Docker)
get_docker_status() {
    local container="$1"
    
    if ! docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
        echo "missing"
        return
    fi
    
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
        local health
        health=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "")
        if [[ "$health" == "healthy" ]]; then
            echo "${GREEN}healthy${NC}"
        elif [[ "$health" == "unhealthy" ]]; then
            echo "${RED}unhealthy${NC}"
        else
            echo "${YELLOW}running${NC}"
        fi
    else
        echo "${RED}stopped${NC}"
    fi
}

# Map service to ansible tag
get_ansible_tag() {
    local service="$1"
    case "$service" in
        authz*) echo "authz" ;;
        agent*) echo "agent" ;;
        ingest*) echo "data" ;;
        search*) echo "search" ;;
        deploy*) echo "deploy" ;;
        docs*) echo "docs" ;;
        embedding*) echo "embedding" ;;
        postgres|pg) echo "postgres" ;;
        minio|files) echo "minio" ;;
        *) echo "$service" ;;
    esac
}

# Execute action on a service (Docker)
docker_action() {
    local service="$1"
    local action="$2"
    local container="$3"
    local env="$4"
    local prefix="$5"
    
    local svc_container
    svc_container=$(get_container_for_service "$service")
    
    case "$action" in
        start)
            info "Starting ${service}..."
            docker start "$container" 2>/dev/null || {
                warn "Container not found, trying to bring up..."
                cd "$REPO_ROOT"
                make docker-up SERVICE="${svc_container}" ENV="$env"
            }
            success "Service started"
            ;;
        
        stop)
            info "Stopping ${service}..."
            docker stop "$container" 2>/dev/null || warn "Container not running"
            success "Service stopped"
            ;;
        
        restart)
            info "Restarting ${service}..."
            docker restart "$container" 2>/dev/null || {
                warn "Container not found, trying to bring up..."
                cd "$REPO_ROOT"
                make docker-up SERVICE="${svc_container}" ENV="$env"
            }
            success "Service restarted"
            ;;
        
        logs)
            info "Showing logs for ${service} (Ctrl+C to exit)..."
            echo ""
            docker logs -f "$container" 2>/dev/null || error "No logs available"
            ;;
        
        status)
            local status
            status=$(get_docker_status "$container")
            echo "  ${BOLD}${service}${NC}: ${status}"
            
            # Show additional info if running
            if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
                local ports
                ports=$(docker port "$container" 2>/dev/null | head -3 || echo "")
                if [[ -n "$ports" ]]; then
                    echo "    Ports: $(echo "$ports" | tr '\n' ', ' | sed 's/, $//')"
                fi
            fi
            ;;
        
        redeploy)
            info "Redeploying ${service}..."
            cd "$REPO_ROOT"
            
            # Use Ansible for proper deployment with secrets
            local inventory="inventory/docker"
            local playbook="docker.yml"
            local tag
            tag=$(get_ansible_tag "$service")
            
            export CONTAINER_PREFIX="$prefix"
            export BUSIBOX_ENV="$env"
            
            # For non-dev environments, don't use local-dev mode
            if [[ "$prefix" != "dev" ]]; then
                export DOCKER_DEV_MODE="github"
            fi
            
            cd "${REPO_ROOT}/provision/ansible"
            local cmd="ansible-playbook -i ${inventory} ${playbook} --tags ${tag}"
            
            # Check for environment-specific vault password file
            local vault_pass_file="$HOME/.busibox-vault-pass-${prefix}"
            if [[ -f "$vault_pass_file" ]]; then
                cmd="${cmd} --vault-password-file ${vault_pass_file}"
            elif [[ -f "$HOME/.vault_pass" ]]; then
                # Legacy fallback
                cmd="${cmd} --vault-password-file $HOME/.vault_pass"
            else
                warn "No vault password file found at ${vault_pass_file}"
                warn "Attempting without vault password (may fail if vault is encrypted)"
            fi
            
            echo "Running: ${cmd}"
            if eval "$cmd"; then
                success "Service redeployed"
            else
                error "Failed to redeploy"
                return 1
            fi
            ;;
    esac
}

# Execute action on a service (Proxmox/Ansible)
proxmox_action() {
    local service="$1"
    local action="$2"
    local env="$3"
    
    local inventory
    case "$env" in
        staging) inventory="inventory/staging" ;;
        production) inventory="inventory/production" ;;
        *) inventory="inventory/staging" ;;
    esac
    
    cd "${REPO_ROOT}/provision/ansible"
    
    case "$action" in
        start)
            info "Starting ${service}..."
            make "start-${service}" INV="$inventory" || error "Failed to start"
            ;;
        
        stop)
            info "Stopping ${service}..."
            make "stop-${service}" INV="$inventory" || error "Failed to stop"
            ;;
        
        restart)
            info "Restarting ${service}..."
            make "restart-${service}" INV="$inventory" || error "Failed to restart"
            ;;
        
        logs)
            info "Showing logs for ${service}..."
            echo "(This may require SSH access to the container)"
            make "logs-${service}" INV="$inventory" 2>/dev/null || {
                warn "Log target not available, trying direct SSH..."
                local container_ip
                container_ip=$(grep -A2 "${service}" "$inventory/hosts.yml" 2>/dev/null | grep "ansible_host" | awk '{print $2}' || echo "")
                if [[ -n "$container_ip" ]]; then
                    ssh "root@${container_ip}" "journalctl -u ${service} -f --no-pager -n 100" 2>/dev/null || error "Could not get logs"
                fi
            }
            ;;
        
        status)
            info "Checking status of ${service}..."
            make "status-${service}" INV="$inventory" 2>/dev/null || {
                make "health-${service}" INV="$inventory" 2>/dev/null || echo "  Status: unknown"
            }
            ;;
        
        redeploy)
            info "Redeploying ${service}..."
            make "deploy-${service}" INV="$inventory" || error "Failed to redeploy"
            ;;
    esac
}

# ============================================================================
# Main
# ============================================================================

main() {
    local services_input="${1:-}"
    local action="${2:-status}"  # Default to status if no action provided
    
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
        echo "Services: postgres, redis, minio, milvus, authz, agent, data,"
        echo "          search, deploy, docs, embedding, litellm, core-apps, nginx"
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
    
    for service in "${services[@]}"; do
        # Trim whitespace
        service=$(echo "$service" | xargs)
        
        if ! is_valid_service "$service"; then
            error "Unknown service: $service"
            any_failed=true
            continue
        fi
        
        if [[ "$backend" == "docker" ]]; then
            local container
            container=$(get_full_container_name "$service" "$prefix")
            docker_action "$service" "$action" "$container" "$env" "$prefix" || any_failed=true
        else
            proxmox_action "$service" "$action" "$env" || any_failed=true
        fi
    done
    
    if $any_failed; then
        exit 1
    fi
}

main "$@"
