#!/usr/bin/env bash
#
# Busibox Service Management
# ==========================
#
# Interactive menu for managing deployed services.
# Supports Docker, Proxmox, and K8s backends via modular backend files.
#
# Usage:
#   make manage              # Interactive management menu
#   bash scripts/make/manage.sh
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries (profiles.sh is sourced by state.sh automatically)
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/profiles.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"
source "${REPO_ROOT}/scripts/lib/status.sh"
source "${REPO_ROOT}/scripts/lib/services.sh"

# Source backend libraries
source "${REPO_ROOT}/scripts/lib/backends/common.sh"

# Initialize profiles
profile_init

# ============================================================================
# Backend Detection (profile-aware)
# ============================================================================

# Active profile info
_active_profile=$(profile_get_active)

get_current_env() {
    if [[ -n "$_active_profile" ]]; then
        profile_get "$_active_profile" "environment"
        return
    fi
    local env
    env=$(get_state "ENVIRONMENT")
    if [[ -z "$env" ]]; then
        env="${BUSIBOX_ENV:-development}"
    fi
    echo "$env"
}

get_backend_type() {
    if [[ -n "$_active_profile" ]]; then
        profile_get "$_active_profile" "backend"
        return
    fi
    local env
    env=$(get_current_env)
    local backend
    backend=$(get_backend "$env" 2>/dev/null)
    if [[ -z "$backend" ]]; then
        backend="docker"
    fi
    echo "$backend"
}

get_container_prefix() {
    if [[ -n "$_active_profile" ]]; then
        profile_get_env_prefix "$_active_profile"
        return
    fi
    local env
    env=$(get_current_env)
    case "$env" in
        production) echo "prod" ;;
        staging) echo "staging" ;;
        demo) echo "demo" ;;
        development) echo "dev" ;;
        *) echo "dev" ;;
    esac
}

# Set globals used by backend files
CONTAINER_PREFIX=$(get_container_prefix)
CURRENT_ENV=$(get_current_env)
export CONTAINER_PREFIX CURRENT_ENV
export BUSIBOX_ENV="$CURRENT_ENV"

# Load the appropriate backend
_CURRENT_BACKEND=$(get_backend_type)
load_backend "$_CURRENT_BACKEND"

# ============================================================================
# Service Groups (delegated to backend)
# ============================================================================

# Get services for a group, using the current backend
get_services_for_group() {
    local group="$1"
    backend_get_services_for_group "$group" "$_CURRENT_BACKEND"
}

# Get group order for the current backend
_get_group_order() {
    local order_str
    order_str=$(backend_get_group_order "$_CURRENT_BACKEND")
    echo "$order_str"
}

# ============================================================================
# Service Display
# ============================================================================

show_services_status() {
    echo ""

    # Get group order
    local groups_str
    groups_str=$(_get_group_order)

    # Collect all services
    local all_services=()
    for group in $groups_str; do
        # Convert underscored group names back for display
        local display_group="${group//_/ }"
        local services
        services=$(get_services_for_group "$group")
        for service in $services; do
            all_services+=("$service")
        done
    done

    # Run health checks in parallel
    local tmpdir=$(mktemp -d)

    for service in "${all_services[@]}"; do
        (
            status=$(backend_get_service_status "$service")
            echo "$status" > "$tmpdir/$service.status"
        ) &
    done
    wait

    get_cached_status() {
        local service="$1"
        if [[ -f "$tmpdir/$service.status" ]]; then
            cat "$tmpdir/$service.status"
        else
            echo "unknown"
        fi
    }

    # Count statuses
    local running_count=0
    local stopped_count=0
    local stopped_services=()

    for service in "${all_services[@]}"; do
        local status=$(get_cached_status "$service")
        case "$status" in
            healthy|running)
                running_count=$((running_count + 1))
                ;;
            stopped|missing|unreachable)
                stopped_count=$((stopped_count + 1))
                stopped_services+=("$service")
                ;;
        esac
    done

    # Show summary
    printf "  ${CYAN}Status:${NC} ${GREEN}%d running${NC}" "$running_count"
    if [[ $stopped_count -gt 0 ]]; then
        printf ", ${RED}%d stopped${NC} ${DIM}(%s)${NC}" "$stopped_count" "$(IFS=', '; echo "${stopped_services[*]}")"
    fi
    echo ""

    # K8s: show tunnel status
    if [[ "$_CURRENT_BACKEND" == "k8s" ]] && type backend_get_tunnel_status_string &>/dev/null; then
        local tunnel_status
        tunnel_status=$(backend_get_tunnel_status_string)
        printf "  ${CYAN}Tunnel:${NC}  %b\n" "$tunnel_status"
    fi

    echo ""

    # Display services by group
    for group in $groups_str; do
        local display_group="${group//_/ }"

        local services
        services=$(get_services_for_group "$group")

        # Skip empty groups
        [[ -z "$services" ]] && continue

        printf "  ${BOLD}${display_group}${NC}\n"

        local services_arr=()
        for service in $services; do
            services_arr+=("$service")
        done

        local i=0
        local count=${#services_arr[@]}
        while [[ $i -lt $count ]]; do
            local service1="${services_arr[$i]}"
            local status1=$(get_cached_status "$service1")
            local status_icon1 status_color1
            case "$status1" in
                healthy)     status_icon1="●" ; status_color1="${GREEN}" ;;
                running)     status_icon1="●" ; status_color1="${GREEN}" ;;
                stopped)     status_icon1="○" ; status_color1="${RED}" ;;
                unhealthy)   status_icon1="●" ; status_color1="${YELLOW}" ;;
                missing)     status_icon1="○" ; status_color1="${DIM}" ;;
                unreachable) status_icon1="○" ; status_color1="${RED}" ;;
                pending)     status_icon1="◐" ; status_color1="${YELLOW}" ;;
                failed)      status_icon1="●" ; status_color1="${RED}" ;;
                *)           status_icon1="?" ; status_color1="${DIM}" ;;
            esac

            if [[ $((i+1)) -lt $count ]]; then
                local service2="${services_arr[$((i+1))]}"
                local status2=$(get_cached_status "$service2")
                local status_icon2 status_color2
                case "$status2" in
                    healthy)     status_icon2="●" ; status_color2="${GREEN}" ;;
                    running)     status_icon2="●" ; status_color2="${GREEN}" ;;
                    stopped)     status_icon2="○" ; status_color2="${RED}" ;;
                    unhealthy)   status_icon2="●" ; status_color2="${YELLOW}" ;;
                    missing)     status_icon2="○" ; status_color2="${DIM}" ;;
                    unreachable) status_icon2="○" ; status_color2="${RED}" ;;
                    pending)     status_icon2="◐" ; status_color2="${YELLOW}" ;;
                    failed)      status_icon2="●" ; status_color2="${RED}" ;;
                    *)           status_icon2="?" ; status_color2="${DIM}" ;;
                esac
                local display1 display2
                display1=$(get_service_display_name_for_env "$service1" "$(get_current_env)" 2>/dev/null || echo "$service1")
                display2=$(get_service_display_name_for_env "$service2" "$(get_current_env)" 2>/dev/null || echo "$service2")
                printf "    ${status_color1}${status_icon1}${NC} %-22s ${DIM}%-10s${NC}  ${status_color2}${status_icon2}${NC} %-22s ${DIM}%s${NC}\n" \
                    "$display1" "$status1" "$display2" "$status2"
            else
                local display1
                display1=$(get_service_display_name_for_env "$service1" "$(get_current_env)" 2>/dev/null || echo "$service1")
                printf "    ${status_color1}${status_icon1}${NC} %-22s ${DIM}%s${NC}\n" "$display1" "$status1"
            fi

            i=$((i+2))
        done

        echo ""
    done

    rm -rf "$tmpdir"
}

# ============================================================================
# Service Actions (delegate to backend)
# ============================================================================

start_all_services() {
    echo ""
    backend_start_all
    read -n 1 -s -r -p "Press any key to continue..."
}

stop_all_services() {
    echo ""
    backend_stop_all
    read -n 1 -s -r -p "Press any key to continue..."
}

restart_all_services() {
    echo ""
    backend_restart_all
    read -n 1 -s -r -p "Press any key to continue..."
}

# ============================================================================
# Manage Individual Service
# ============================================================================

manage_host_native_service() {
    local service="$1"

    while true; do
        clear
        box_start 70 double "$CYAN"
        box_header "MANAGE: $service (host-native)"
        box_empty

        local status
        status=$(backend_get_service_status "$service" 2>/dev/null || echo "unknown")
        box_line "  ${CYAN}Status:${NC} $status"
        box_empty

        box_line "  ${BOLD}1)${NC} Start"
        box_line "  ${BOLD}2)${NC} Stop"
        box_line "  ${BOLD}3)${NC} Restart"
        box_line "  ${BOLD}4)${NC} Status (detailed)"

        box_empty
        box_line "  ${DIM}This service runs on the host machine, not in a container.${NC}"
        box_line "  ${DIM}Managed via: make ${service}-start/stop/restart/status${NC}"
        box_empty
        box_line "  ${DIM}b = back to service list    m = main menu${NC}"
        box_empty
        box_footer
        echo ""

        read -n 1 -s -r -p "Select option: " choice
        echo ""

        case "$choice" in
            1) host_native_action "$service" "start"; read -n 1 -s -r -p "Press any key to continue..." ;;
            2) host_native_action "$service" "stop"; read -n 1 -s -r -p "Press any key to continue..." ;;
            3) host_native_action "$service" "restart"; read -n 1 -s -r -p "Press any key to continue..." ;;
            4) host_native_action "$service" "status"; read -n 1 -s -r -p "Press any key to continue..." ;;
            b|B) return ;;
            m|M) return 1 ;;
        esac
    done
}

manage_service() {
    local service="$1"

    # Host-native services get their own management flow
    if is_host_native_service "$service"; then
        manage_host_native_service "$service"
        return $?
    fi

    local prefix="${CONTAINER_PREFIX:-dev}"
    local env
    env=$(get_current_env)

    while true; do
        clear
        box_start 70 double "$CYAN"
        box_header "MANAGE: $service"
        box_empty

        local status
        status=$(backend_get_service_status "$service")
        box_line "  ${CYAN}Status:${NC} $status"

        local companions
        companions=$(get_companion_services "$service")
        if [[ -n "$companions" ]]; then
            box_line "  ${DIM}Includes: ${companions}${NC}"
        fi
        box_empty

        box_line "  ${BOLD}1)${NC} Start"
        box_line "  ${BOLD}2)${NC} Stop"
        box_line "  ${BOLD}3)${NC} Restart"
        box_line "  ${BOLD}4)${NC} View Logs"
        box_line "  ${BOLD}5)${NC} Redeploy (rebuild)"

        # Backend-specific extras for Docker core-apps
        if [[ "$_CURRENT_BACKEND" == "docker" && "$service" == "core-apps" ]]; then
            box_line "  ${BOLD}6)${NC} Rebuild Container (full Docker rebuild)"
            box_line "  ${BOLD}7)${NC} Rebuild App (from source, no container restart)"

            local current_mode
            current_mode=$(docker inspect --format='{{join .Args " "}}' "${prefix}-core-apps" 2>/dev/null || echo "unknown")
            if [[ "$current_mode" == "prod" ]]; then
                box_line "  ${BOLD}8)${NC} Switch to Dev mode (Turbopack hot-reload)"
                box_empty
                box_line "  ${DIM}Currently running in ${BOLD}prod${NC}${DIM} mode (built, no hot-reload)${NC}"
            else
                box_line "  ${BOLD}8)${NC} Switch to Prod mode (build + serve, lower CPU)"
                box_empty
                box_line "  ${DIM}Currently running in ${BOLD}dev${NC}${DIM} mode (Turbopack hot-reload)${NC}"
            fi
        fi

        # Add dev mode note for Docker Python API services
        if [[ "$_CURRENT_BACKEND" == "docker" ]]; then
            case "$service" in
                authz-api|data-api|search-api|agent-api|deploy-api|docs-api|embedding-api)
                    box_empty
                    box_line "  ${DIM}Note: In dev mode, Python APIs have hot-reload.${NC}"
                    box_line "  ${DIM}Use Restart for code changes, Redeploy only for${NC}"
                    box_line "  ${DIM}requirements.txt or Dockerfile changes.${NC}"
                    ;;
            esac
        fi

        # K8s note
        if [[ "$_CURRENT_BACKEND" == "k8s" ]]; then
            box_empty
            box_line "  ${DIM}Note: Stop scales to 0 replicas. Start scales to 1.${NC}"
            box_line "  ${DIM}Redeploy syncs code, rebuilds image, and restarts.${NC}"
        fi

        box_empty
        box_line "  ${DIM}b = back to service list    m = main menu${NC}"
        box_empty
        box_footer
        echo ""

        read -n 1 -s -r -p "Select option: " choice
        echo ""

        case "$choice" in
            1) # Start
                backend_service_action "$service" "start" "$env" "$prefix"
                for companion in $(get_companion_services "$service"); do
                    info "Also starting companion: ${companion}"
                    backend_service_action "$companion" "start" "$env" "$prefix"
                done
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            2) # Stop
                backend_service_action "$service" "stop" "$env" "$prefix"
                for companion in $(get_companion_services "$service"); do
                    info "Also stopping companion: ${companion}"
                    backend_service_action "$companion" "stop" "$env" "$prefix"
                done
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            3) # Restart
                backend_service_action "$service" "restart" "$env" "$prefix"
                for companion in $(get_companion_services "$service"); do
                    info "Also restarting companion: ${companion}"
                    backend_service_action "$companion" "restart" "$env" "$prefix"
                done
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            4) # Logs
                clear
                backend_service_action "$service" "logs" "$env" "$prefix"
                ;;
            5) # Redeploy
                echo ""
                backend_service_action "$service" "redeploy" "$env" "$prefix"
                for companion in $(get_companion_services "$service"); do
                    echo ""
                    info "Also redeploying companion: ${companion}"
                    backend_service_action "$companion" "redeploy" "$env" "$prefix"
                done
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            6) # Rebuild Container (Docker core-apps only)
                if [[ "$service" != "core-apps" ]] || [[ "$_CURRENT_BACKEND" != "docker" ]]; then
                    continue
                fi
                echo ""
                info "Rebuilding core-apps container (full Docker rebuild)..."
                cd "$REPO_ROOT"
                make docker-build SERVICE=core-apps ENV="$env" && make docker-up SERVICE=core-apps ENV="$env"
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            7) # Rebuild App (core-apps only)
                if [[ "$service" != "core-apps" ]]; then
                    continue
                fi
                _rebuild_app_submenu "$env" "$prefix"
                ;;
            8) # Switch mode (Docker core-apps only)
                if [[ "$service" != "core-apps" ]] || [[ "$_CURRENT_BACKEND" != "docker" ]]; then
                    continue
                fi
                echo ""
                local current_mode
                current_mode=$(docker inspect --format='{{join .Args " "}}' "${prefix}-core-apps" 2>/dev/null || echo "dev")
                local new_mode
                if [[ "$current_mode" == "prod" ]]; then
                    new_mode="dev"
                    info "Switching core-apps to dev mode..."
                else
                    new_mode="prod"
                    info "Switching core-apps to prod mode..."
                    warn "This will build both apps before starting."
                fi
                cd "$REPO_ROOT"
                export CORE_APPS_MODE="$new_mode"
                make docker-up SERVICE="core-apps"
                echo ""
                success "core-apps now running in ${new_mode} mode"
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            b|B) return ;;
            m|M) return 1 ;;
        esac
    done
}

# Rebuild app submenu (core-apps only)
_rebuild_app_submenu() {
    local env="$1"
    local prefix="$2"

    clear
    box_start 70 double "$CYAN"
    box_header "REBUILD APP"
    box_empty
    box_line "  ${BOLD}Select app to rebuild:${NC}"
    box_empty
    box_line "    ${BOLD}1)${NC} ai-portal"
    box_line "    ${BOLD}2)${NC} agent-manager"
    box_line "    ${BOLD}3)${NC} both"
    box_empty
    box_line "  ${DIM}b = back${NC}"
    box_empty
    box_footer
    echo ""

    read -n 1 -s -r -p "Select app: " app_choice
    echo ""

    case "$app_choice" in
        1)
            echo ""
            info "Rebuilding ai-portal from source..."
            if [[ "$_CURRENT_BACKEND" == "docker" ]]; then
                docker exec "${prefix}-core-apps" bash -c "cd /srv/ai-portal && npm install"
                docker restart "${prefix}-core-apps"
            else
                cd "${REPO_ROOT}/provision/ansible"
                make deploy-ai-portal INV="inventory/${env}"
            fi
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        2)
            echo ""
            info "Rebuilding agent-manager from source..."
            if [[ "$_CURRENT_BACKEND" == "docker" ]]; then
                docker exec "${prefix}-core-apps" bash -c "cd /srv/agent-manager && npm install"
                docker restart "${prefix}-core-apps"
            else
                cd "${REPO_ROOT}/provision/ansible"
                make deploy-agent-manager INV="inventory/${env}"
            fi
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        3)
            echo ""
            info "Rebuilding ai-portal from source..."
            if [[ "$_CURRENT_BACKEND" == "docker" ]]; then
                docker exec "${prefix}-core-apps" bash -c "cd /srv/ai-portal && npm install"
                echo ""
                info "Rebuilding agent-manager from source..."
                docker exec "${prefix}-core-apps" bash -c "cd /srv/agent-manager && npm install"
                docker restart "${prefix}-core-apps"
            else
                cd "${REPO_ROOT}/provision/ansible"
                make deploy-ai-portal INV="inventory/${env}"
                echo ""
                info "Rebuilding agent-manager from source..."
                make deploy-agent-manager INV="inventory/${env}"
            fi
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        b|B) ;;
    esac
}

# ============================================================================
# Service Selector
# ============================================================================

select_service() {
    while true; do
        clear
        box_header "SELECT SERVICE"
        echo ""

        local services=()
        local idx=1
        local groups_str
        groups_str=$(_get_group_order)

        for group in $groups_str; do
            local display_group="${group//_/ }"

            local group_services_list
            group_services_list=$(get_services_for_group "$group")
            [[ -z "$group_services_list" ]] && continue

            echo -e "  ${BOLD}${display_group}${NC}"

            local group_services=()
            for service in $group_services_list; do
                services+=("$service")
                group_services+=("$service:$idx")
                ((idx++))
            done

            local i=0
            local count=${#group_services[@]}
            while [[ $i -lt $count ]]; do
                local entry1="${group_services[$i]}"
                local service1="${entry1%%:*}"
                local num1="${entry1##*:}"

                if [[ $((i+1)) -lt $count ]]; then
                    local entry2="${group_services[$((i+1))]}"
                    local service2="${entry2%%:*}"
                    local num2="${entry2##*:}"
                    printf "    ${BOLD}%2d)${NC} %-20s  ${BOLD}%2d)${NC} %s\n" "$num1" "$service1" "$num2" "$service2"
                else
                    printf "    ${BOLD}%2d)${NC} %s\n" "$num1" "$service1"
                fi

                i=$((i+2))
            done
            echo ""
        done

        echo -e "  ${DIM}b = back to main menu${NC}"
        echo ""
        box_footer
        echo ""

        read -p "Enter service number: " choice

        if [[ "$choice" == "b" ]] || [[ "$choice" == "B" ]]; then
            return
        fi

        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#services[@]} )); then
            manage_service "${services[$((choice-1))]}"
            local ret=$?
            if [[ $ret -eq 1 ]]; then
                return
            fi
        fi
    done
}

# ============================================================================
# Main Menu
# ============================================================================

show_manage_menu() {
    local env
    env=$(get_current_env)

    clear
    box_header "BUSIBOX - SERVICE MANAGEMENT"
    echo ""
    
    # Show profile info if available
    if [[ -n "$_active_profile" ]]; then
        local display
        display=$(profile_get_display "$_active_profile")
        printf "  ${CYAN}Profile:${NC}     %s (%s)\n" "$_active_profile" "$display"
    else
        printf "  ${CYAN}Environment:${NC} %s (%s)\n" "$env" "$_CURRENT_BACKEND"
    fi

    show_services_status

    printf "  ${BOLD}Actions${NC}\n"
    printf "    ${BOLD}1)${NC} Start All\n"
    printf "    ${BOLD}2)${NC} Stop All\n"
    printf "    ${BOLD}3)${NC} Restart All\n"
    printf "    ${BOLD}4)${NC} Manage Service\n"
    printf "    ${BOLD}5)${NC} View Logs (all)\n"
    printf "    ${BOLD}6)${NC} Refresh Status\n"

    # K8s-specific menu items
    if [[ "$_CURRENT_BACKEND" == "k8s" ]]; then
        echo ""
        printf "  ${BOLD}Tunnel${NC}\n"
        printf "    ${BOLD}7)${NC} Connect (start tunnel)\n"
        printf "    ${BOLD}8)${NC} Disconnect (stop tunnel)\n"
    fi

    echo ""
    printf "  ${BOLD}Utilities${NC}\n"
    printf "    ${BOLD}r)${NC} Rotate Secrets\n"
    if [[ "$_CURRENT_BACKEND" != "k8s" ]]; then
        printf "    ${BOLD}d)${NC} Update Internal DNS (/etc/hosts on all containers)\n"
    fi
    echo ""
    printf "  ${DIM}b = back to main menu    q = quit${NC}\n"
    echo ""
    box_footer
}

main() {
    if [[ -z "$_active_profile" && -z "${BUSIBOX_ENV:-}" ]]; then
        echo ""
        echo "No profile or environment configured."
        echo "Run 'make' to create a profile first."
        exit 1
    fi

    while true; do
        show_manage_menu
        echo ""

        read -n 1 -s -r -p "Select option: " choice
        echo ""

        case "$choice" in
            1)
                start_all_services
                ;;
            2)
                stop_all_services
                ;;
            3)
                restart_all_services
                ;;
            4)
                select_service
                ;;
            5)
                clear
                echo "Showing all logs (Ctrl+C to exit)..."
                echo ""
                if [[ "$_CURRENT_BACKEND" == "docker" ]]; then
                    cd "$REPO_ROOT"
                    make docker-logs
                elif [[ "$_CURRENT_BACKEND" == "k8s" ]]; then
                    # Get kubeconfig from profile or fallback
                    local kc=""
                    if [[ -n "$_active_profile" ]]; then
                        kc=$(profile_get_kubeconfig "$_active_profile" 2>/dev/null)
                    fi
                    kc="${kc:-$REPO_ROOT/k8s/kubeconfig-rackspace-spot.yaml}"
                    # Show combined logs from all pods
                    info "Streaming logs from all K8s pods (Ctrl+C to exit)..."
                    echo ""
                    KUBECONFIG="$kc" \
                        kubectl logs -n busibox --all-containers --max-log-requests=20 -f --tail=50 2>/dev/null || \
                        echo "Could not stream logs. Try 'Manage Service' -> 'View Logs' for individual services."
                    read -n 1 -s -r -p "Press any key to continue..."
                else
                    echo "Proxmox log viewing not implemented yet"
                    read -n 1 -s -r -p "Press any key to continue..."
                fi
                ;;
            6)
                # Refresh by continuing loop
                ;;
            7)
                # Connect (K8s only)
                if [[ "$_CURRENT_BACKEND" == "k8s" ]]; then
                    echo ""
                    backend_connect
                    read -n 1 -s -r -p "Press any key to continue..."
                fi
                ;;
            8)
                # Disconnect (K8s only)
                if [[ "$_CURRENT_BACKEND" == "k8s" ]]; then
                    echo ""
                    backend_disconnect
                    read -n 1 -s -r -p "Press any key to continue..."
                fi
                ;;
            r|R)
                echo ""
                bash "${SCRIPT_DIR}/rotate-secrets.sh"
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            d|D)
                if [[ "$_CURRENT_BACKEND" != "k8s" ]]; then
                    echo ""
                    info "Updating internal DNS (/etc/hosts) on all containers..."
                    local env
                    env=$(get_current_env)
                    cd "${REPO_ROOT}/provision/ansible"
                    make internal-dns INV="inventory/${env}"
                    success "Internal DNS updated"
                    read -n 1 -s -r -p "Press any key to continue..."
                fi
                ;;
            b|B)
                exec bash "${SCRIPT_DIR}/launcher.sh"
                ;;
            q|Q)
                echo ""
                echo "Goodbye!"
                exit 0
                ;;
        esac
    done
}

main "$@"
