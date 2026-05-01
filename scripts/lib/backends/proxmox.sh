#!/usr/bin/env bash
# =============================================================================
# Busibox Proxmox Backend
# =============================================================================
#
# Proxmox-specific status checks and service actions via SSH + systemctl.
# Source common.sh BEFORE this file.
#
# Implements the unified backend interface:
#   - backend_get_service_status SERVICE
#   - backend_service_action SERVICE ACTION [ENV]
#   - backend_start_all
#   - backend_stop_all
#   - backend_restart_all
#   - backend_detect_installation ENV
#
# =============================================================================

[[ -n "${_BACKEND_PROXMOX_LOADED:-}" ]] && return 0
_BACKEND_PROXMOX_LOADED=1

# ============================================================================
# Helpers
# ============================================================================

# Get network base IP for environment
_proxmox_get_network_base() {
    local env="$1"
    case "$env" in
        staging) echo "10.96.201" ;;
        production) echo "10.96.200" ;;
        *) echo "10.96.201" ;;
    esac
}

# Get container IP for a service
_proxmox_get_service_ip() {
    local service="$1"
    local env="$2"
    local network_base
    network_base=$(_proxmox_get_network_base "$env")

    case "$service" in
        postgres|pg) echo "${network_base}.203" ;;
        minio|files) echo "${network_base}.205" ;;
        milvus) echo "${network_base}.204" ;;
        neo4j|graph) echo "${network_base}.213" ;;
        agent|agent-api) echo "${network_base}.202" ;;
        ingest|data|data-api|data-worker|redis) echo "${network_base}.206" ;;
        bridge|bridge-api) echo "${network_base}.211" ;;
        authz|authz-api|deploy|deploy-api|docs|docs-api) echo "${network_base}.210" ;;
        search|search-api) echo "${network_base}.204" ;;
        embedding|embedding-api) echo "${network_base}.206" ;;
        core-apps|apps|busibox-portal|busibox-admin|busibox-agents|busibox-chat|busibox-appbuilder|busibox-media|busibox-documents) echo "${network_base}.201" ;;
        nginx|proxy) echo "${network_base}.200" ;;
        litellm) echo "${network_base}.207" ;;
        vllm) echo "${network_base}.208" ;;
        user-apps) echo "${network_base}.212" ;;
        custom-services) echo "${network_base}.212" ;;
        *) echo "" ;;
    esac
}

# Get systemd service name for a service
_proxmox_get_systemd_name() {
    local service="$1"
    case "$service" in
        authz|authz-api) echo "authz" ;;
        agent|agent-api) echo "agent" ;;
        ingest|data|data-api) echo "data-api" ;;
        data-worker) echo "data-worker" ;;
        search|search-api) echo "search-api" ;;
        deploy|deploy-api) echo "deploy-api" ;;
        bridge|bridge-api) echo "bridge" ;;
        docs|docs-api) echo "docs-api" ;;
        embedding|embedding-api) echo "embedding-api" ;;
        nginx|proxy) echo "nginx" ;;
        busibox-portal) echo "busibox-portal" ;;
        busibox-admin) echo "busibox-admin" ;;
        busibox-agents) echo "busibox-agents" ;;
        busibox-chat) echo "busibox-chat" ;;
        busibox-appbuilder) echo "busibox-appbuilder" ;;
        busibox-media) echo "busibox-media" ;;
        busibox-documents) echo "busibox-documents" ;;
        postgres|pg) echo "postgresql" ;;
        minio|files) echo "minio" ;;
        milvus) echo "milvus" ;;
        neo4j|graph) echo "neo4j" ;;
        litellm) echo "litellm" ;;
        vllm) echo "vllm" ;;
        redis) echo "redis" ;;
        *) echo "$service" ;;
    esac
}

_proxmox_vllm_status() {
    local env="$1"
    local ip
    ip=$(_proxmox_get_service_ip "vllm" "$env")
    if [[ -z "$ip" ]]; then
        echo "unknown"
        return
    fi

    ssh "root@${ip}" '
        units=$(systemctl list-unit-files "vllm-*.service" --no-legend --no-pager 2>/dev/null | awk "{print \$1}")
        if [ -z "$units" ]; then
            echo "stopped"
            exit 0
        fi
        active=0
        inactive=0
        failed=0
        for unit in $units; do
            state=$(systemctl is-active "$unit" 2>/dev/null || true)
            case "$state" in
                active) active=$((active + 1)) ;;
                failed) failed=$((failed + 1)) ;;
                *) inactive=$((inactive + 1)) ;;
            esac
        done
        if [ "$failed" -gt 0 ]; then
            echo "unhealthy"
        elif [ "$active" -gt 0 ] && [ "$inactive" -eq 0 ]; then
            echo "healthy"
        elif [ "$active" -gt 0 ]; then
            echo "unhealthy"
        else
            echo "stopped"
        fi
    ' 2>/dev/null || echo "unreachable"
}

_proxmox_vllm_units_command() {
    local action="$1"
    case "$action" in
        start|stop|restart)
            echo "systemctl list-unit-files 'vllm-*.service' --no-legend --no-pager 2>/dev/null | awk '{print \$1}' | xargs -r systemctl ${action}"
            ;;
    esac
}

# Get inventory path for environment
_proxmox_get_inventory() {
    local env="$1"
    case "$env" in
        staging) echo "inventory/staging" ;;
        production) echo "inventory/production" ;;
        *) echo "inventory/staging" ;;
    esac
}

# ============================================================================
# Status
# ============================================================================

# Get status using health endpoint or ping fallback
backend_get_service_status() {
    local service="$1"
    local env="${CURRENT_ENV:-staging}"

    if [[ "$service" == "vllm" ]]; then
        _proxmox_vllm_status "$env"
        return
    fi

    # Normalize service name for registry lookup
    local lookup_service="${service//-/_}"
    case "$service" in
        pg) lookup_service="postgres" ;;
        files) lookup_service="minio" ;;
        agent) lookup_service="agent_api" ;;
        ingest|data) lookup_service="data_api" ;;
        search) lookup_service="search_api" ;;
        deploy) lookup_service="deploy_api" ;;
        docs) lookup_service="docs_api" ;;
        apps) lookup_service="ai_portal" ;;
        proxy) lookup_service="nginx" ;;
        core-apps) lookup_service="ai_portal" ;;
        user-apps) lookup_service="user_apps" ;;
    esac

    # Try health URL from service registry (if services.sh is loaded)
    local health_url=""
    if type get_service_health_url &>/dev/null; then
        health_url=$(get_service_health_url "$lookup_service" "$env" "proxmox" 2>/dev/null)
    fi

    if [[ -z "$health_url" ]]; then
        # Fallback to ping
        local ip
        ip=$(_proxmox_get_service_ip "$service" "$env")
        if [[ -z "$ip" ]]; then
            echo "unknown"
            return
        fi
        if ping -c 1 -W 1 "$ip" &>/dev/null 2>&1; then
            echo "running"
        else
            echo "unreachable"
        fi
        return
    fi

    # Health endpoint check
    local http_code
    http_code=$(curl -s -w "%{http_code}" --max-time 3 --connect-timeout 2 -o /dev/null "$health_url" 2>/dev/null || echo "000")

    case "$http_code" in
        200|301|302|401|403)
            echo "healthy"
            ;;
        000)
            local ip=""
            if type get_service_ip &>/dev/null; then
                ip=$(get_service_ip "$lookup_service" "$env" "proxmox" 2>/dev/null || echo "")
            fi
            if [[ -z "$ip" ]]; then
                ip=$(_proxmox_get_service_ip "$service" "$env")
            fi
            if [[ -n "$ip" ]] && ping -c 1 -W 1 "$ip" &>/dev/null 2>&1; then
                echo "stopped"
            else
                echo "unreachable"
            fi
            ;;
        5*)
            echo "unhealthy"
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

# ============================================================================
# Custom Service Actions (Proxmox / LXC)
# ============================================================================

# Execute action on a custom service deployed to an LXC container via SSH.
# Custom services run as docker compose projects inside the user-apps LXC.
_proxmox_custom_service_action() {
    local service="$1"
    local action="$2"
    local ip="$3"
    local app_dir="/srv/custom-services/${service}"
    local compose_file="${app_dir}/docker-compose.yml"

    if [[ -z "$ip" ]]; then
        error "No IP for custom services container"
        return 1
    fi

    case "$action" in
        start)
            info "Starting custom service ${service} on ${ip}..."
            ssh "root@${ip}" "docker compose -f ${compose_file} up -d" 2>&1
            success "Custom service ${service} started"
            ;;
        stop)
            info "Stopping custom service ${service} on ${ip}..."
            ssh "root@${ip}" "docker compose -f ${compose_file} stop" 2>&1
            success "Custom service ${service} stopped"
            ;;
        restart)
            info "Restarting custom service ${service} on ${ip}..."
            ssh "root@${ip}" "docker compose -f ${compose_file} restart" 2>&1
            success "Custom service ${service} restarted"
            ;;
        logs)
            info "Showing logs for custom service ${service} on ${ip} (Ctrl+C to exit)..."
            echo ""
            ssh "root@${ip}" "docker compose -f ${compose_file} logs -f" 2>&1
            ;;
        status)
            echo "  ${BOLD}${service}${NC} (custom service on ${ip}):"
            ssh "root@${ip}" "docker compose -f ${compose_file} ps" 2>&1 | while IFS= read -r line; do
                echo "    $line"
            done
            ;;
        redeploy)
            info "Redeploying custom service ${service} on ${ip}..."
            ssh "root@${ip}" "cd ${app_dir} && docker compose -f ${compose_file} build --no-cache && docker compose -f ${compose_file} up -d" 2>&1
            success "Custom service ${service} redeployed"
            ;;
        *)
            error "Unknown action '${action}' for custom service ${service}"
            return 1
            ;;
    esac
}

# ============================================================================
# Actions
# ============================================================================

backend_service_action() {
    local service="$1"
    local action="$2"
    local env="${3:-${CURRENT_ENV:-staging}}"

    # Staging uses production vLLM — redirect the operator
    if [[ "$service" == "vllm" && "$env" == "staging" ]]; then
        echo ""
        warn "Staging uses production vLLM (use_production_vllm: true)."
        warn "To manage vLLM, switch to the production profile."
        echo ""
        return 0
    fi

    local inventory
    inventory=$(_proxmox_get_inventory "$env")
    local container_ip
    container_ip=$(_proxmox_get_service_ip "$service" "$env")

    # Custom service handling -- managed via docker compose on user-apps LXC
    local svc_map
    svc_map=$(get_container_for_service "$service")
    if [[ "$svc_map" == custom:* ]]; then
        local custom_ip
        custom_ip=$(_proxmox_get_service_ip "custom-services" "$env")
        _proxmox_custom_service_action "$service" "$action" "$custom_ip"
        return $?
    fi

    local systemd_service
    systemd_service=$(_proxmox_get_systemd_name "$service")

    case "$action" in
        start)
            info "Starting ${service}..."
            if [[ -n "$container_ip" ]]; then
                if [[ "$service" == "user-apps" ]]; then
                    # user-apps LXC hosts multiple app services — start all enabled
                    ssh "root@${container_ip}" "systemctl list-unit-files --type=service --state=enabled --no-legend 2>/dev/null | awk '{print \$1}' | grep -v '^ssh' | xargs -r systemctl start" 2>/dev/null || error "Failed to start user app services"
                    success "User app services started"
                elif [[ "$service" == "vllm" ]]; then
                    ssh "root@${container_ip}" "$(_proxmox_vllm_units_command start)" 2>/dev/null || error "Failed to start vLLM services"
                    success "vLLM services started"
                else
                    ssh "root@${container_ip}" "systemctl start ${systemd_service}" 2>/dev/null || error "Failed to start"
                    success "Service started"
                fi
            else
                error "Unknown service IP for ${service}"
            fi
            ;;

        stop)
            info "Stopping ${service}..."
            if [[ -n "$container_ip" ]]; then
                if [[ "$service" == "user-apps" ]]; then
                    ssh "root@${container_ip}" "systemctl list-unit-files --type=service --state=enabled --no-legend 2>/dev/null | awk '{print \$1}' | grep -v '^ssh' | xargs -r systemctl stop" 2>/dev/null || error "Failed to stop user app services"
                    success "User app services stopped"
                elif [[ "$service" == "vllm" ]]; then
                    ssh "root@${container_ip}" "$(_proxmox_vllm_units_command stop)" 2>/dev/null || error "Failed to stop vLLM services"
                    success "vLLM services stopped"
                else
                    ssh "root@${container_ip}" "systemctl stop ${systemd_service}" 2>/dev/null || error "Failed to stop"
                    success "Service stopped"
                fi
            else
                error "Unknown service IP for ${service}"
            fi
            ;;

        restart)
            info "Restarting ${service}..."
            if [[ -n "$container_ip" ]]; then
                if [[ "$service" == "user-apps" ]]; then
                    ssh "root@${container_ip}" "systemctl list-unit-files --type=service --state=enabled --no-legend 2>/dev/null | awk '{print \$1}' | grep -v '^ssh' | xargs -r systemctl restart" 2>/dev/null || error "Failed to restart user app services"
                    success "User app services restarted"
                elif [[ "$service" == "vllm" ]]; then
                    ssh "root@${container_ip}" "$(_proxmox_vllm_units_command restart)" 2>/dev/null || error "Failed to restart vLLM services"
                    success "vLLM services restarted"
                else
                    ssh "root@${container_ip}" "systemctl restart ${systemd_service}" 2>/dev/null || error "Failed to restart"
                    success "Service restarted"
                fi
            else
                error "Unknown service IP for ${service}"
            fi
            ;;

        logs)
            info "Showing logs for ${service} (Ctrl+C to exit)..."
            echo ""
            if [[ -n "$container_ip" ]]; then
                if [[ "$service" == "user-apps" ]]; then
                    # user-apps LXC hosts multiple app services — show all journal output
                    ssh "root@${container_ip}" "journalctl -f --no-pager -n 100" 2>&1 || echo "Could not get logs for ${service}"
                elif [[ "$service" == "vllm" ]]; then
                    ssh "root@${container_ip}" "journalctl -u 'vllm-*' -f --no-pager -n 100" 2>&1 || echo "Could not get logs for ${service}"
                else
                    ssh "root@${container_ip}" "journalctl -u ${systemd_service} -f --no-pager -n 100" 2>&1 || echo "Could not get logs for ${service}"
                fi
            else
                echo "ERROR: Unknown service IP for ${service}"
            fi
            ;;

        status)
            if [[ -n "$container_ip" ]]; then
                if [[ "$service" == "user-apps" ]]; then
                    # user-apps LXC hosts multiple app services — show status of all
                    echo "  ${BOLD}${service}${NC} (container: ${container_ip}):"
                    ssh "root@${container_ip}" "systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null | grep -v -E '^(ssh|systemd|dbus|cron|getty)'" 2>/dev/null | while IFS= read -r line; do
                        echo "    $line"
                    done
                    local running_count
                    running_count=$(ssh "root@${container_ip}" "systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null | grep -v -E '^(ssh|systemd|dbus|cron|getty)' | wc -l" 2>/dev/null || echo "0")
                    if [[ "$running_count" -eq 0 ]]; then
                        echo "    (no user app services running)"
                    fi
                elif [[ "$service" == "vllm" ]]; then
                    echo "  ${BOLD}${service}${NC} (container: ${container_ip}):"
                    ssh "root@${container_ip}" "systemctl list-units 'vllm-*.service' --all --no-pager --no-legend 2>/dev/null" 2>/dev/null | while IFS= read -r line; do
                        echo "    $line"
                    done
                else
                    local status
                    status=$(ssh "root@${container_ip}" "systemctl is-active ${systemd_service}" 2>/dev/null || echo "unknown")
                    echo "  ${BOLD}${service}${NC}: ${status}"
                fi
            else
                echo "  ${BOLD}${service}${NC}: unknown (no IP mapping)"
            fi
            ;;

        redeploy)
            info "Redeploying ${service}..."
            cd "${REPO_ROOT}/provision/ansible"

            local deploy_ref="${DEPLOY_REF:-}"
            local frontend_apps="busibox-portal busibox-admin busibox-agents busibox-chat busibox-appbuilder busibox-media busibox-documents"

            case "$service" in
                busibox-portal|busibox-admin|busibox-agents|busibox-chat|busibox-appbuilder|busibox-media|busibox-documents)
                    if [[ -n "$deploy_ref" ]]; then
                        info "Deploying ${service} at ref: ${BOLD}${deploy_ref}${NC}"
                        if ! make deploy-app-ref APP="$service" REF="$deploy_ref" INV="$inventory"; then
                            error "Failed to redeploy"
                            return 1
                        fi
                    else
                        if ! make "deploy-${service}" INV="$inventory"; then
                            error "Failed to redeploy"
                            return 1
                        fi
                    fi
                    ;;
                core-apps|apps)
                    # Deploy all 7 monorepo frontend apps
                    if [[ -n "$deploy_ref" ]]; then
                        for app in $frontend_apps; do
                            info "Deploying ${app} at ref: ${BOLD}${deploy_ref}${NC}"
                            if ! make deploy-app-ref APP="$app" REF="$deploy_ref" INV="$inventory"; then
                                error "Failed to redeploy ${app}"
                                return 1
                            fi
                            echo ""
                        done
                    else
                        if ! make deploy-frontend INV="$inventory"; then
                            error "Failed to redeploy frontend"
                            return 1
                        fi
                    fi
                    ;;
                user-apps)
                    # user-apps LXC: re-provision the container prerequisites (Node.js, git, etc.)
                    # Individual apps are deployed at runtime via the Deploy API.
                    if ! make user-apps INV="$inventory"; then
                        error "Failed to redeploy user-apps container"
                        return 1
                    fi
                    ;;
                *)
                    local make_target
                    make_target=$(get_proxmox_make_target "$service")
                    if ! make "$make_target" INV="$inventory"; then
                        error "Failed to redeploy"
                        return 1
                    fi
                    ;;
            esac
            success "Service redeployed"
            ;;
    esac
}

# ============================================================================
# Bulk Actions
# ============================================================================

backend_start_all() {
    local env="${CURRENT_ENV:-staging}"
    local inventory
    inventory=$(_proxmox_get_inventory "$env")

    info "Starting all services..."
    cd "${REPO_ROOT}/provision/ansible"
    make start-all INV="$inventory"
    success "Services started"
}

backend_stop_all() {
    local env="${CURRENT_ENV:-staging}"
    local inventory
    inventory=$(_proxmox_get_inventory "$env")

    info "Stopping all services..."
    cd "${REPO_ROOT}/provision/ansible"
    make stop-all INV="$inventory"
    success "Services stopped"
}

backend_restart_all() {
    local env="${CURRENT_ENV:-staging}"
    local inventory
    inventory=$(_proxmox_get_inventory "$env")

    info "Restarting all services..."
    cd "${REPO_ROOT}/provision/ansible"
    make restart-all INV="$inventory"
    success "Services restarted"
}

# ============================================================================
# Installation Detection
# ============================================================================

backend_detect_installation() {
    local env="${1:-staging}"

    if ! command -v pct &>/dev/null; then
        echo "not_installed"
        return
    fi

    local base_ctid
    case "$env" in
        production) base_ctid=200 ;;
        staging) base_ctid=300 ;;
        *) base_ctid=300 ;;
    esac

    local status
    status=$(pct status "$base_ctid" 2>/dev/null | awk '{print $2}')
    if [[ "$status" == "running" ]]; then
        echo "installed"
    else
        echo "not_installed"
    fi
}
