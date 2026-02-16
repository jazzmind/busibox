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
        agent|agent-api) echo "${network_base}.202" ;;
        ingest|data-api|data-worker|redis) echo "${network_base}.206" ;;
        bridge|bridge-api) echo "${network_base}.211" ;;
        authz|authz-api|deploy|deploy-api|docs|docs-api) echo "${network_base}.210" ;;
        search|search-api|embedding|embedding-api) echo "${network_base}.204" ;;
        core-apps|apps|busibox-portal|busibox-agents) echo "${network_base}.201" ;;
        nginx|proxy) echo "${network_base}.200" ;;
        litellm) echo "${network_base}.207" ;;
        vllm) echo "${network_base}.208" ;;
        user-apps) echo "${network_base}.212" ;;
        *) echo "" ;;
    esac
}

# Get systemd service name for a service
_proxmox_get_systemd_name() {
    local service="$1"
    case "$service" in
        authz|authz-api) echo "authz" ;;
        agent|agent-api) echo "agent" ;;
        ingest|data-api) echo "data-api" ;;
        data-worker) echo "data-worker" ;;
        search|search-api) echo "search-api" ;;
        deploy|deploy-api) echo "deploy-api" ;;
        bridge|bridge-api) echo "bridge" ;;
        docs|docs-api) echo "docs-api" ;;
        embedding|embedding-api) echo "embedding-api" ;;
        nginx|proxy) echo "nginx" ;;
        busibox-portal) echo "busibox-portal" ;;
        busibox-agents) echo "busibox-agents" ;;
        postgres|pg) echo "postgresql" ;;
        minio|files) echo "minio" ;;
        milvus) echo "milvus" ;;
        litellm) echo "litellm" ;;
        vllm) echo "vllm" ;;
        redis) echo "redis" ;;
        *) echo "$service" ;;
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
# Actions
# ============================================================================

backend_service_action() {
    local service="$1"
    local action="$2"
    local env="${3:-${CURRENT_ENV:-staging}}"

    local inventory
    inventory=$(_proxmox_get_inventory "$env")
    local container_ip
    container_ip=$(_proxmox_get_service_ip "$service" "$env")
    local systemd_service
    systemd_service=$(_proxmox_get_systemd_name "$service")

    case "$action" in
        start)
            info "Starting ${service}..."
            if [[ -n "$container_ip" ]]; then
                ssh "root@${container_ip}" "systemctl start ${systemd_service}" 2>/dev/null || error "Failed to start"
                success "Service started"
            else
                error "Unknown service IP for ${service}"
            fi
            ;;

        stop)
            info "Stopping ${service}..."
            if [[ -n "$container_ip" ]]; then
                ssh "root@${container_ip}" "systemctl stop ${systemd_service}" 2>/dev/null || error "Failed to stop"
                success "Service stopped"
            else
                error "Unknown service IP for ${service}"
            fi
            ;;

        restart)
            info "Restarting ${service}..."
            if [[ -n "$container_ip" ]]; then
                ssh "root@${container_ip}" "systemctl restart ${systemd_service}" 2>/dev/null || error "Failed to restart"
                success "Service restarted"
            else
                error "Unknown service IP for ${service}"
            fi
            ;;

        logs)
            info "Showing logs for ${service} (Ctrl+C to exit)..."
            echo ""
            if [[ -n "$container_ip" ]]; then
                ssh "root@${container_ip}" "journalctl -u ${systemd_service} -f --no-pager -n 100" 2>/dev/null || error "Could not get logs"
            else
                error "Unknown service IP for ${service}"
            fi
            ;;

        status)
            if [[ -n "$container_ip" ]]; then
                local status
                status=$(ssh "root@${container_ip}" "systemctl is-active ${systemd_service}" 2>/dev/null || echo "unknown")
                echo "  ${BOLD}${service}${NC}: ${status}"
            else
                echo "  ${BOLD}${service}${NC}: unknown (no IP mapping)"
            fi
            ;;

        redeploy)
            info "Redeploying ${service}..."
            cd "${REPO_ROOT}/provision/ansible"
            local make_target
            make_target=$(get_proxmox_make_target "$service")

            local vault_pass_file="$HOME/.busibox-vault-pass-${env}"
            if [[ ! -f "$vault_pass_file" ]]; then
                vault_pass_file="$HOME/.vault_pass"
            fi

            if [[ -f "$vault_pass_file" ]]; then
                make "$make_target" INV="$inventory" || error "Failed to redeploy"
            else
                warn "No vault password file found"
                make "$make_target" INV="$inventory" || error "Failed to redeploy"
            fi
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
