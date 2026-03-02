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
            if [[ "$service" == busibox-* && "$svc_container" == "core-apps" ]]; then
                local app_short="${service#busibox-}"
                info "Starting ${app_short} inside core-apps..."
                docker exec "$container" supervisorctl start "busibox-${app_short}" 2>&1 || warn "Failed to start ${app_short}"
                success "${app_short} started"
            else
                info "Starting ${service}..."
                docker start "$container" 2>/dev/null || {
                    warn "Container not found, trying to bring up..."
                    cd "$REPO_ROOT"
                    make docker-up SERVICE="${svc_container}" ENV="$env"
                }
                success "Service started"
            fi
            ;;

        stop)
            if [[ "$service" == busibox-* && "$svc_container" == "core-apps" ]]; then
                local app_short="${service#busibox-}"
                info "Stopping ${app_short} inside core-apps..."
                docker exec "$container" supervisorctl stop "busibox-${app_short}" 2>&1 || warn "Failed to stop ${app_short}"
                success "${app_short} stopped"
            else
                info "Stopping ${service}..."
                docker stop "$container" 2>/dev/null || warn "Container not running"
                success "Service stopped"
            fi
            ;;

        restart)
            if [[ "$service" == busibox-* && "$svc_container" == "core-apps" ]]; then
                local app_short="${service#busibox-}"
                info "Restarting ${app_short} inside core-apps..."
                docker exec "$container" supervisorctl restart "busibox-${app_short}" 2>&1 || warn "Failed to restart ${app_short}"
                success "${app_short} restarted"
            elif [[ "$svc_container" == "core-apps" && ( -n "${CORE_APPS_MODE:-}" || -n "${CORE_APPS_SOURCE:-}" ) ]]; then
                info "Restarting core-apps (mode=${CORE_APPS_MODE:-auto}, source=${CORE_APPS_SOURCE:-auto})..."
                cd "$REPO_ROOT"
                export CORE_APPS_MODE
                export CORE_APPS_SOURCE
                make docker-up SERVICE="core-apps" ENV="$env"
                success "core-apps restarted"
                return $?
            else
                info "Restarting ${service}..."
                docker restart "$container" 2>/dev/null || {
                    warn "Container not found, trying to bring up..."
                    cd "$REPO_ROOT"
                    make docker-up SERVICE="${svc_container}" ENV="$env"
                }
                success "Service restarted"
            fi
            ;;

        logs)
            if [[ "$service" == busibox-* && "$svc_container" == "core-apps" ]]; then
                local app_short="${service#busibox-}"
                info "Showing logs for ${app_short} inside core-apps (Ctrl+C to exit)..."
                echo ""
                docker exec "$container" supervisorctl tail -f "busibox-${app_short}" 2>/dev/null || error "No logs available"
            else
                info "Showing logs for ${service} (Ctrl+C to exit)..."
                echo ""
                docker logs -f "$container" 2>/dev/null || error "No logs available"
            fi
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
            # Individual frontend app: rebuild inside running core-apps container
            # without recreating the container (which would kill other apps).
            if [[ "$service" == busibox-* && "$svc_container" == "core-apps" ]]; then
                local app_short_name="${service#busibox-}"
                info "Redeploying ${app_short_name} inside core-apps container..."
                if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
                    error "core-apps container (${container}) is not running"
                    return 1
                fi
                if docker exec "$container" /usr/local/bin/entrypoint.sh deploy "${app_short_name}"; then
                    success "${app_short_name} redeployed"
                else
                    error "Failed to redeploy ${app_short_name}"
                    return 1
                fi
                return 0
            fi

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
            # Ensure LLM backend context is available to Ansible/docker compose.
            # Use explicit env first, then fall back to persisted install/profile state.
            local llm_backend llm_tier env_file
            llm_backend="${LLM_BACKEND:-$(get_state "LLM_BACKEND" "" 2>/dev/null || echo "")}"
            llm_tier="${LLM_TIER:-$(get_state "LLM_TIER" "" 2>/dev/null || echo "")}"
            if [[ -z "$llm_backend" ]] && type profile_get_active &>/dev/null && type profile_get &>/dev/null; then
                local active_profile profile_llm_backend profile_model_tier profile_memory_tier
                active_profile="$(profile_get_active 2>/dev/null || echo "")"
                if [[ -n "$active_profile" ]]; then
                    profile_llm_backend="$(profile_get "$active_profile" "hardware.llm_backend" 2>/dev/null || echo "")"
                    profile_model_tier="$(profile_get "$active_profile" "model_tier" 2>/dev/null || echo "")"
                    profile_memory_tier="$(profile_get "$active_profile" "hardware.memory_tier" 2>/dev/null || echo "")"
                    if [[ -n "$profile_llm_backend" ]]; then
                        llm_backend="$profile_llm_backend"
                    fi
                    if [[ -z "$llm_tier" ]]; then
                        llm_tier="${profile_model_tier:-$profile_memory_tier}"
                    fi
                fi
            fi
            if [[ -z "$llm_backend" || -z "$llm_tier" ]]; then
                env_file="$(get_env_file_path 2>/dev/null || echo "${REPO_ROOT}/.env.${prefix}")"
                if [[ -f "$env_file" ]]; then
                    if [[ -z "$llm_backend" ]]; then
                        llm_backend="$(awk -F= '/^LLM_BACKEND=/{print $2; exit}' "$env_file" | tr -d '"' || true)"
                    fi
                    if [[ -z "$llm_tier" ]]; then
                        llm_tier="$(awk -F= '/^LLM_TIER=/{print $2; exit}' "$env_file" | tr -d '"' || true)"
                    fi
                fi
            fi
            if [[ -n "$llm_backend" ]]; then
                export LLM_BACKEND="$llm_backend"
                export DETECTED_LLM_BACKEND="$llm_backend"
            fi
            if [[ -n "$llm_tier" ]]; then
                export LLM_TIER="$llm_tier"
            fi

            if [[ "$prefix" != "dev" ]]; then
                export DOCKER_DEV_MODE="github"
            fi

            local cmd="ansible-playbook -i ${inventory} ${playbook} --tags ${tag} -e docker_force_recreate=true"

            local _vpd="${BUSIBOX_VAULT_PASS_DIR:-${HOME}}"
            local vault_pass_file="${_vpd}/.busibox-vault-pass-${prefix}"
            if [[ ! -f "$vault_pass_file" ]]; then
                vault_pass_file="$HOME/.busibox-vault-pass-${prefix}"
            fi

            # Prefer env-injected vault password (used by CLI/TUI manage flows).
            # This avoids requiring plaintext vault password files on disk.
            local env_script="${REPO_ROOT}/scripts/lib/vault-pass-from-env.sh"
            if [[ -n "${ANSIBLE_VAULT_PASSWORD:-}" && -f "$env_script" ]]; then
                [[ -x "$env_script" ]] || chmod +x "$env_script"
                cmd="${cmd} --vault-password-file ${env_script}"
            elif [[ -f "$vault_pass_file" ]]; then
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
