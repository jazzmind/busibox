#!/usr/bin/env bash
#
# Busibox Service Management
# ==========================
#
# Interactive menu for managing deployed services.
# Supports both Docker and Proxmox backends.
#
# Usage:
#   make manage              # Interactive management menu
#   bash scripts/make/manage.sh
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"
source "${REPO_ROOT}/scripts/lib/status.sh"
source "${REPO_ROOT}/scripts/lib/services.sh"

# ============================================================================
# Configuration
# ============================================================================

# Service group order
SERVICE_GROUP_ORDER=("Infrastructure" "APIs" "LLM" "Frontend" "User Apps")

# Get services for a group (replaces associative array for bash 3.2 compatibility)
get_services_for_group() {
    local group="$1"
    case "$group" in
        "Infrastructure")
            echo "postgres redis minio milvus"
            ;;
        "APIs")
            echo "authz-api agent-api data-api search-api deploy-api docs-api embedding-api"
            ;;
        "LLM")
            echo "litellm ollama vllm"
            ;;
        "Frontend")
            echo "core-apps nginx"
            ;;
        "User Apps")
            echo "user-apps"
            ;;
        *)
            echo ""
            ;;
    esac
}

# ============================================================================
# Backend Detection
# ============================================================================

get_backend_type() {
    local env
    env=$(get_state "ENVIRONMENT" || echo "development")
    get_backend "$env" 2>/dev/null || echo "docker"
}

# Get container prefix based on environment
get_container_prefix() {
    local env
    env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "development")
    
    case "$env" in
        production) echo "prod" ;;
        staging) echo "staging" ;;
        demo) echo "demo" ;;
        development) echo "dev" ;;
        *) echo "dev" ;;
    esac
}

# Set CONTAINER_PREFIX for use by functions
CONTAINER_PREFIX=$(get_container_prefix)

# ============================================================================
# Service Status
# ============================================================================

# Get status of a single service (Docker)
get_docker_service_status() {
    local service="$1"
    local prefix="${CONTAINER_PREFIX:-dev}"
    local container_name="${prefix}-${service}"
    
    # Check if container exists
    if ! docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${container_name}$"; then
        echo "missing"
        return
    fi
    
    # Check if running
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container_name}$"; then
        # Check health if available
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

# Get status of a single service (Proxmox LXC)
get_proxmox_service_status() {
    local service="$1"
    local env
    env=$(get_state "ENVIRONMENT" || echo "staging")
    
    # Map service to LXC container name
    local lxc_prefix
    case "$env" in
        staging) lxc_prefix="STAGE" ;;
        production) lxc_prefix="PROD" ;;
        *) lxc_prefix="STAGE" ;;
    esac
    
    local lxc_name="${lxc_prefix}-${service}-lxc"
    local network_base
    case "$env" in
        staging) network_base="10.96.201" ;;
        production) network_base="10.96.200" ;;
        *) network_base="10.96.201" ;;
    esac
    
    # Quick ping check
    local ip
    case "$service" in
        postgres|pg) ip="${network_base}.203" ;;
        minio|files) ip="${network_base}.205" ;;
        milvus) ip="${network_base}.204" ;;
        agent|agent-api) ip="${network_base}.202" ;;
        ingest|data-api) ip="${network_base}.206" ;;
        authz|authz-api) ip="${network_base}.210" ;;
        core-apps|apps) ip="${network_base}.201" ;;
        proxy|nginx) ip="${network_base}.200" ;;
        litellm) ip="${network_base}.207" ;;
        ollama) ip="${network_base}.209" ;;
        *) echo "unknown"; return ;;
    esac
    
    if ping -c 1 -W 1 "$ip" &>/dev/null 2>&1; then
        echo "running"
    else
        echo "unreachable"
    fi
}

# Get service status based on backend
get_service_status() {
    local service="$1"
    local backend
    backend=$(get_backend_type)
    
    if [[ "$backend" == "docker" ]]; then
        get_docker_service_status "$service"
    else
        get_proxmox_service_status "$service"
    fi
}

# ============================================================================
# Service Display
# ============================================================================

# Display all services with status
show_services_status() {
    local backend
    backend=$(get_backend_type)
    
    echo ""
    
    for group in "${SERVICE_GROUP_ORDER[@]}"; do
        local services
        services=$(get_services_for_group "$group")
        
        printf "  ${BOLD}${group}${NC}\n"
        
        for service in $services; do
            local status
            status=$(get_service_status "$service")
            
            local status_icon status_color
            case "$status" in
                healthy)    status_icon="●" ; status_color="${GREEN}" ;;
                running)    status_icon="●" ; status_color="${GREEN}" ;;
                stopped)    status_icon="○" ; status_color="${RED}" ;;
                unhealthy)  status_icon="●" ; status_color="${YELLOW}" ;;
                missing)    status_icon="○" ; status_color="${DIM}" ;;
                unreachable) status_icon="○" ; status_color="${RED}" ;;
                *)          status_icon="?" ; status_color="${DIM}" ;;
            esac
            
            printf "    ${status_color}${status_icon}${NC} %-20s ${DIM}%s${NC}\n" "$service" "$status"
        done
        
        echo ""
    done
}

# ============================================================================
# Service Actions
# ============================================================================

# Start all services
start_all_services() {
    local backend
    backend=$(get_backend_type)
    
    echo ""
    info "Starting all services..."
    
    if [[ "$backend" == "docker" ]]; then
        cd "$REPO_ROOT"
        make docker-up
    else
        local env
        env=$(get_state "ENVIRONMENT" || echo "staging")
        cd "${REPO_ROOT}/provision/ansible"
        make start-all INV="inventory/${env}"
    fi
    
    success "Services started"
    read -n 1 -s -r -p "Press any key to continue..."
}

# Stop all services
stop_all_services() {
    local backend
    backend=$(get_backend_type)
    
    echo ""
    info "Stopping all services..."
    
    if [[ "$backend" == "docker" ]]; then
        cd "$REPO_ROOT"
        make docker-down
    else
        local env
        env=$(get_state "ENVIRONMENT" || echo "staging")
        cd "${REPO_ROOT}/provision/ansible"
        make stop-all INV="inventory/${env}"
    fi
    
    success "Services stopped"
    read -n 1 -s -r -p "Press any key to continue..."
}

# Restart all services
restart_all_services() {
    local backend
    backend=$(get_backend_type)
    
    echo ""
    info "Restarting all services..."
    
    if [[ "$backend" == "docker" ]]; then
        cd "$REPO_ROOT"
        make docker-restart
    else
        local env
        env=$(get_state "ENVIRONMENT" || echo "staging")
        cd "${REPO_ROOT}/provision/ansible"
        make restart-all INV="inventory/${env}"
    fi
    
    success "Services restarted"
    read -n 1 -s -r -p "Press any key to continue..."
}

# Manage individual service
manage_service() {
    local service="$1"
    local backend
    backend=$(get_backend_type)
    local prefix="${CONTAINER_PREFIX:-dev}"
    
    while true; do
        clear
        box_start 70 double "$CYAN"
        box_header "MANAGE: $service"
        box_empty
        
        local status
        status=$(get_service_status "$service")
        box_line "  ${CYAN}Status:${NC} $status"
        box_empty
        
        box_line "  ${BOLD}1)${NC} Start"
        box_line "  ${BOLD}2)${NC} Stop"
        box_line "  ${BOLD}3)${NC} Restart"
        box_line "  ${BOLD}4)${NC} View Logs"
        box_line "  ${BOLD}5)${NC} Redeploy"
        
        # Add "Rebuild App" option for core-apps service
        if [[ "$service" == "core-apps" ]]; then
            box_line "  ${BOLD}6)${NC} Rebuild App (no container restart)"
        fi
        
        box_empty
        box_line "  ${DIM}b = back${NC}"
        box_empty
        box_footer
        echo ""
        
        read -n 1 -s -r -p "Select option: " choice
        echo ""
        
        case "$choice" in
            1) # Start
                if [[ "$backend" == "docker" ]]; then
                    docker start "${prefix}-${service}" 2>/dev/null || echo "Failed to start"
                else
                    local env
                    env=$(get_state "ENVIRONMENT" || echo "staging")
                    cd "${REPO_ROOT}/provision/ansible"
                    make "start-${service}" INV="inventory/${env}" 2>/dev/null || echo "Failed to start"
                fi
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            2) # Stop
                if [[ "$backend" == "docker" ]]; then
                    docker stop "${prefix}-${service}" 2>/dev/null || echo "Failed to stop"
                else
                    local env
                    env=$(get_state "ENVIRONMENT" || echo "staging")
                    cd "${REPO_ROOT}/provision/ansible"
                    make "stop-${service}" INV="inventory/${env}" 2>/dev/null || echo "Failed to stop"
                fi
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            3) # Restart
                if [[ "$backend" == "docker" ]]; then
                    docker restart "${prefix}-${service}" 2>/dev/null || echo "Failed to restart"
                else
                    local env
                    env=$(get_state "ENVIRONMENT" || echo "staging")
                    cd "${REPO_ROOT}/provision/ansible"
                    make "restart-${service}" INV="inventory/${env}" 2>/dev/null || echo "Failed to restart"
                fi
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            4) # Logs
                clear
                echo "Showing logs for ${service} (Ctrl+C to exit)..."
                echo ""
                if [[ "$backend" == "docker" ]]; then
                    docker logs -f "${prefix}-${service}" 2>/dev/null || echo "No logs available"
                else
                    local env
                    env=$(get_state "ENVIRONMENT" || echo "staging")
                    ssh "root@${service}" "journalctl -u ${service} -f" 2>/dev/null || echo "No logs available"
                fi
                ;;
            5) # Redeploy
                echo ""
                info "Redeploying ${service}..."
                if [[ "$backend" == "docker" ]]; then
                    cd "$REPO_ROOT"
                    make docker-build SERVICE="$service" && make docker-up SERVICE="$service"
                else
                    local env
                    env=$(get_state "ENVIRONMENT" || echo "staging")
                    cd "${REPO_ROOT}/provision/ansible"
                    make "deploy-${service}" INV="inventory/${env}"
                fi
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            6) # Rebuild App (only for core-apps)
                if [[ "$service" != "core-apps" ]]; then
                    continue
                fi
                
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
                    1) # ai-portal
                        echo ""
                        info "Rebuilding ai-portal..."
                        local env
                        env=$(get_state "ENVIRONMENT" || echo "staging")
                        cd "${REPO_ROOT}/provision/ansible"
                        make deploy-ai-portal INV="inventory/${env}"
                        read -n 1 -s -r -p "Press any key to continue..."
                        ;;
                    2) # agent-manager
                        echo ""
                        info "Rebuilding agent-manager..."
                        local env
                        env=$(get_state "ENVIRONMENT" || echo "staging")
                        cd "${REPO_ROOT}/provision/ansible"
                        make deploy-agent-manager INV="inventory/${env}"
                        read -n 1 -s -r -p "Press any key to continue..."
                        ;;
                    3) # both
                        echo ""
                        info "Rebuilding ai-portal..."
                        local env
                        env=$(get_state "ENVIRONMENT" || echo "staging")
                        cd "${REPO_ROOT}/provision/ansible"
                        make deploy-ai-portal INV="inventory/${env}"
                        echo ""
                        info "Rebuilding agent-manager..."
                        make deploy-agent-manager INV="inventory/${env}"
                        read -n 1 -s -r -p "Press any key to continue..."
                        ;;
                    b|B)
                        # Go back to service menu
                        ;;
                esac
                ;;
            b|B)
                return
                ;;
        esac
    done
}

# Select a service to manage
select_service() {
    clear
    box_header "SELECT SERVICE"
    echo ""
    
    local services=()
    local idx=1
    
    for group in "${SERVICE_GROUP_ORDER[@]}"; do
        echo -e "  ${BOLD}${group}${NC}"
        for service in $(get_services_for_group "$group"); do
            services+=("$service")
            local status
            status=$(get_service_status "$service")
            local status_indicator
            case "$status" in
                healthy|running) status_indicator="${GREEN}●${NC}" ;;
                stopped|missing|unreachable) status_indicator="${RED}○${NC}" ;;
                *) status_indicator="${YELLOW}●${NC}" ;;
            esac
            echo -e "    ${BOLD}$(printf "%2d" "$idx")${NC}) ${status_indicator} ${service}"
            ((idx++))
        done
        echo ""
    done
    
    echo -e "  ${DIM}b = back${NC}"
    echo ""
    box_footer
    echo ""
    
    read -p "Enter service number: " choice
    
    if [[ "$choice" == "b" ]] || [[ "$choice" == "B" ]]; then
        return
    fi
    
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#services[@]} )); then
        manage_service "${services[$((choice-1))]}"
    fi
}

# ============================================================================
# Main Menu
# ============================================================================

show_manage_menu() {
    local backend
    backend=$(get_backend_type)
    local env
    env=$(get_state "ENVIRONMENT" || echo "development")
    
    clear
    box_header "BUSIBOX - SERVICE MANAGEMENT"
    echo ""
    printf "  ${CYAN}Environment:${NC} %s (%s)\n" "$env" "$backend"
    
    show_services_status
    
    printf "  ${BOLD}Actions${NC}\n"
    printf "    ${BOLD}1)${NC} Start All\n"
    printf "    ${BOLD}2)${NC} Stop All\n"
    printf "    ${BOLD}3)${NC} Restart All\n"
    printf "    ${BOLD}4)${NC} Manage Service\n"
    printf "    ${BOLD}5)${NC} View Logs (all)\n"
    printf "    ${BOLD}6)${NC} Refresh Status\n"
    echo ""
    printf "  ${DIM}b = back to main menu    q = quit${NC}\n"
    echo ""
    box_footer
}

main() {
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
                local backend
                backend=$(get_backend_type)
                if [[ "$backend" == "docker" ]]; then
                    cd "$REPO_ROOT"
                    make docker-logs
                else
                    echo "Proxmox log viewing not implemented yet"
                    read -n 1 -s -r -p "Press any key to continue..."
                fi
                ;;
            6)
                # Just refresh by continuing loop
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

# Run main
main "$@"
