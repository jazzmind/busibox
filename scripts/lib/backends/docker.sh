#!/usr/bin/env bash
# =============================================================================
# Busibox Docker Backend
# =============================================================================
#
# Docker-specific status checks and service actions.
# Source common.sh BEFORE this file.
#
# Implements the unified backend interface:
#   - backend_get_service_status SERVICE
#   - backend_service_action SERVICE ACTION [ENV] [PREFIX]
#   - backend_start_all
#   - backend_stop_all
#   - backend_restart_all
#   - backend_detect_installation ENV
#
# =============================================================================

[[ -n "${_BACKEND_DOCKER_LOADED:-}" ]] && return 0
_BACKEND_DOCKER_LOADED=1

# ============================================================================
# Status
# ============================================================================

# Get status of a single Docker service
# Returns: healthy, running, stopped, missing, unhealthy, unknown
backend_get_service_status() {
    local service="$1"
    local prefix="${CONTAINER_PREFIX:-dev}"
    local container_name="${prefix}-$(get_container_for_service "$service")"

    # Special case: MLX runs on host (not in Docker).
    # From inside a container, reach the host via host.docker.internal.
    if [[ "$service" == "mlx" ]]; then
        local host_addr="localhost"
        if _is_inside_container 2>/dev/null; then
            host_addr="host.docker.internal"
        fi

        local host_agent_port
        host_agent_port=$(get_state "HOST_AGENT_PORT" "8089" 2>/dev/null || echo "8089")
        local host_agent_token
        host_agent_token="${HOST_AGENT_TOKEN:-$(get_state "HOST_AGENT_TOKEN" "" 2>/dev/null || echo "")}"

        if [[ -n "$host_agent_token" ]]; then
            local response
            response=$(curl -s -w "%{http_code}" -o /dev/null --max-time 2 \
                -H "Authorization: Bearer $host_agent_token" \
                "http://${host_addr}:${host_agent_port}/mlx/status" 2>/dev/null || echo "000")
            if [[ "$response" == "200" ]]; then
                echo "running"
                return
            fi
        fi

        local mlx_response
        mlx_response=$(curl -s -w "%{http_code}" -o /dev/null --max-time 2 \
            "http://${host_addr}:8080/v1/models" 2>/dev/null || echo "000")
        if [[ "$mlx_response" == "200" ]]; then
            echo "running"
        else
            echo "stopped"
        fi
        return
    fi

    # Check if container exists
    if ! docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${container_name}$"; then
        echo "missing"
        return
    fi

    # Check if running
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container_name}$"; then
        local health
        health=$(docker inspect --format='{{.State.Health.Status}}' "$container_name" 2>/dev/null || echo "")
        if [[ "$health" == "healthy" ]]; then
            echo "healthy"
        elif [[ "$health" == "unhealthy" ]]; then
            echo "unhealthy"
        else
            echo "running"
        fi
    else
        echo "stopped"
    fi
}

# Colored status for CLI output
backend_get_service_status_colored() {
    local service="$1"
    local prefix="${CONTAINER_PREFIX:-dev}"
    local container="${prefix}-$(get_container_for_service "$service")"

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

# ============================================================================
# Actions
# ============================================================================

# Execute action on a Docker service
# Usage: backend_service_action SERVICE ACTION [ENV] [PREFIX]
backend_service_action() {
    local service="$1"
    local action="$2"
    local env="${3:-$(get_current_env 2>/dev/null || echo "development")}"
    local prefix="${4:-${CONTAINER_PREFIX:-dev}}"

    local svc_container
    svc_container=$(get_container_for_service "$service")
    local container="${prefix}-${svc_container}"

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
            # core-apps mode/source switching support
            if [[ "$svc_container" == "core-apps" && ( -n "${CORE_APPS_MODE:-}" || -n "${CORE_APPS_SOURCE:-}" ) ]]; then
                info "Restarting core-apps (mode=${CORE_APPS_MODE:-auto}, source=${CORE_APPS_SOURCE:-auto})..."
                cd "$REPO_ROOT"
                export CORE_APPS_MODE
                export CORE_APPS_SOURCE
                make docker-up SERVICE="core-apps" ENV="$env"
                success "core-apps restarted"
                return $?
            fi

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
            status=$(backend_get_service_status_colored "$service")
            echo "  ${BOLD}${service}${NC}: ${status}"

            if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
                local ports
                ports=$(docker port "$container" 2>/dev/null | head -3 || echo "")
                if [[ -n "$ports" ]]; then
                    echo "    Ports: $(echo "$ports" | tr '\n' ', ' | sed 's/, $//')"
                fi
            fi
            ;;

        redeploy)
            if [[ "$svc_container" == "core-apps" && ( -n "${CORE_APPS_MODE:-}" || -n "${CORE_APPS_SOURCE:-}" ) ]]; then
                info "Rebuilding core-apps (mode=${CORE_APPS_MODE:-auto}, source=${CORE_APPS_SOURCE:-auto})..."
                cd "$REPO_ROOT"
                export CORE_APPS_MODE
                export CORE_APPS_SOURCE
                if [[ "$env" == "development" ]]; then
                    info "Development mode detected; clearing core-apps cache volumes..."
                    docker stop "$container" >/dev/null 2>&1 || true

                    # Collect mounted cache volumes on core-apps, then remove them.
                    # We clear:
                    # - .next caches (stale Next.js build artifacts)
                    # - node_modules volumes (stale pnpm workspace links/cached deps)
                    local cache_volumes
                    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
                        cache_volumes=$(
                            docker inspect --format '{{ range .Mounts }}{{ .Name }} {{ .Destination }} {{ .Type }}{{ "\n" }}{{ end }}' "$container" 2>/dev/null \
                                | awk '$3 == "volume" && ($2 ~ /\/\.next$/ || $2 ~ /\/node_modules$/) { print $1 }' \
                                | sort -u
                        )
                    else
                        cache_volumes=""
                        info "${container} not present; skipping mounted cache discovery"
                    fi

                    if [[ -n "$cache_volumes" ]]; then
                        while IFS= read -r volume_name; do
                            [[ -z "$volume_name" ]] && continue
                            info "Removing cache volume: ${volume_name}"
                            docker volume rm -f "$volume_name" >/dev/null 2>&1 || true
                        done <<< "$cache_volumes"
                    else
                        info "No attached .next/node_modules cache volumes found on ${container}"
                    fi
                fi
                make docker-build SERVICE="core-apps" ENV="$env" && make docker-up SERVICE="core-apps" ENV="$env"
                success "core-apps redeployed"
                return $?
            fi

            info "Redeploying ${service}..."
            cd "${REPO_ROOT}/provision/ansible"

            local inventory="inventory/docker"
            local playbook="docker.yml"
            local tag
            tag=$(get_ansible_tag "$service")

            export CONTAINER_PREFIX="$prefix"
            export COMPOSE_PROJECT_NAME="${prefix}-busibox"
            export BUSIBOX_ENV="$env"

            if [[ "$prefix" != "dev" ]]; then
                export DOCKER_DEV_MODE="github"
            fi

            local cmd="ansible-playbook -i ${inventory} ${playbook} --tags ${tag}"

            local _vpd="${BUSIBOX_VAULT_PASS_DIR:-${HOME}}"
            local vault_pass_file="${_vpd}/.busibox-vault-pass-${prefix}"
            if [[ ! -f "$vault_pass_file" ]]; then
                vault_pass_file="$HOME/.busibox-vault-pass-${prefix}"
            fi
            if [[ -f "$vault_pass_file" ]]; then
                cmd="${cmd} --vault-password-file ${vault_pass_file}"
            elif [[ -f "$HOME/.vault_pass" ]]; then
                cmd="${cmd} --vault-password-file $HOME/.vault_pass"
            else
                warn "No vault password file found at ${vault_pass_file}"
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

# ============================================================================
# Bulk Actions
# ============================================================================

backend_start_all() {
    info "Starting all services..."
    cd "$REPO_ROOT"
    make docker-up
    success "Services started"
}

backend_stop_all() {
    info "Stopping all services..."
    cd "$REPO_ROOT"
    make docker-down
    success "Services stopped"
}

backend_restart_all() {
    info "Restarting all services..."
    cd "$REPO_ROOT"
    make docker-restart
    success "Services restarted"
}

# ============================================================================
# Installation Detection
# ============================================================================

# Returns: not_installed, partial, installed
backend_detect_installation() {
    local env="${1:-development}"

    if ! command -v docker &>/dev/null || ! docker info &>/dev/null 2>&1; then
        echo "not_installed"
        return
    fi

    local prefix
    case "$env" in
        development) prefix="dev" ;;
        staging) prefix="staging" ;;
        production) prefix="prod" ;;
        demo) prefix="demo" ;;
        *) prefix="dev" ;;
    esac

    local core_services=("postgres" "authz-api" "core-apps")
    local running=0
    local total=${#core_services[@]}

    for service in "${core_services[@]}"; do
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${prefix}-${service}$"; then
            ((running++))
        fi
    done

    if [[ $running -eq $total ]]; then
        echo "installed"
    elif [[ $running -gt 0 ]]; then
        echo "partial"
    else
        echo "not_installed"
    fi
}
