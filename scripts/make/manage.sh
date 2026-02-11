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

# Simple state file for storing last used environment
BUSIBOX_SIMPLE_STATE="${REPO_ROOT}/.busibox-state"

# Read last environment from simple state file
_read_last_env() {
    if [[ -f "$BUSIBOX_SIMPLE_STATE" ]]; then
        local last_env
        last_env=$(grep "^LAST_ENV=" "$BUSIBOX_SIMPLE_STATE" 2>/dev/null | cut -d'=' -f2 | tr -d '"' | tr -d "'")
        if [[ -n "$last_env" ]]; then
            echo "$last_env"
            return
        fi
    fi
    echo ""
}

# Auto-detect deployed environment BEFORE sourcing state library
# This allows the state library to use the correct state file
_auto_detect_env() {
    # If BUSIBOX_ENV is already set, use it
    if [[ -n "${BUSIBOX_ENV:-}" ]]; then
        echo "$BUSIBOX_ENV"
        return
    fi
    
    # Check for last used environment in simple state file
    local last_env
    last_env=$(_read_last_env)
    if [[ -n "$last_env" ]]; then
        echo "$last_env"
        return
    fi
    
    # No saved environment - return empty to trigger environment selector
    echo ""
}

# Set environment before sourcing state library
export BUSIBOX_ENV="${BUSIBOX_ENV:-$(_auto_detect_env)}"

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

# Detect if running on Apple Silicon
is_apple_silicon() {
    local os arch
    os=$(uname -s)
    arch=$(uname -m)
    [[ "$os" == "Darwin" && ("$arch" == "arm64" || "$arch" == "aarch64") ]]
}

# Get services for a group (replaces associative array for bash 3.2 compatibility)
get_services_for_group() {
    local group="$1"
    local backend
    backend=$(get_backend_type)
    
    case "$group" in
        "Infrastructure")
            echo "postgres redis minio milvus"
            ;;
        "APIs")
            echo "authz-api agent-api data-api search-api deploy-api docs-api embedding-api"
            ;;
        "LLM")
            # Show MLX on Apple Silicon, vLLM otherwise
            if is_apple_silicon; then
                echo "litellm mlx"
            else
                echo "litellm vllm"
            fi
            ;;
        "Frontend")
            # On Proxmox, nginx is a separate container; on Docker, it's bundled
            if [[ "$backend" == "docker" ]]; then
                echo "core-apps"  # nginx is part of core-apps container in Docker
            else
                echo "nginx core-apps"  # nginx is separate on Proxmox
            fi
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

# Get current environment with fallback to BUSIBOX_ENV
# This handles the case where ENVIRONMENT isn't set in the state file yet
get_current_env() {
    local env
    env=$(get_state "ENVIRONMENT")
    if [[ -z "$env" ]]; then
        env="${BUSIBOX_ENV:-staging}"
    fi
    echo "$env"
}

get_backend_type() {
    local env
    env=$(get_current_env)
    local backend
    backend=$(get_backend "$env" 2>/dev/null)
    if [[ -z "$backend" ]]; then
        backend="docker"
    fi
    echo "$backend"
}

# Get container prefix based on environment
get_container_prefix() {
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
    
    # Special case: MLX runs on host, not in Docker container
    # Check via direct HTTP call to the MLX server on localhost
    if [[ "$service" == "mlx" ]]; then
        # Try host-agent status endpoint first
        local host_agent_port
        host_agent_port=$(get_state "HOST_AGENT_PORT" "8089")
        local host_agent_token
        host_agent_token=$(get_state "HOST_AGENT_TOKEN" "")
        
        if [[ -n "$host_agent_token" ]]; then
            local response
            response=$(curl -s -w "%{http_code}" -o /dev/null \
                -H "Authorization: Bearer $host_agent_token" \
                "http://localhost:${host_agent_port}/mlx/status" 2>/dev/null || echo "000")
            if [[ "$response" == "200" ]]; then
                echo "running"
                return
            fi
        fi
        
        # Fallback: direct MLX health check on localhost:8080
        local mlx_response
        mlx_response=$(curl -s -w "%{http_code}" -o /dev/null --max-time 2 \
            "http://localhost:8080/v1/models" 2>/dev/null || echo "000")
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
# Uses actual HTTP health endpoint checks for APIs, not just container ping
get_proxmox_service_status() {
    local service="$1"
    local env
    env=$(get_current_env)
    
    # Normalize service name for lookup in service registry
    # The registry uses underscores (e.g., agent_api, ai_portal)
    # The menu uses hyphens (e.g., agent-api, ai-portal)
    # First convert hyphens to underscores, then handle special cases
    local lookup_service="${service//-/_}"
    case "$service" in
        pg) lookup_service="postgres" ;;
        files) lookup_service="minio" ;;
        # Short names that need expansion
        agent) lookup_service="agent_api" ;;
        ingest|data) lookup_service="data_api" ;;
        search) lookup_service="search_api" ;;
        deploy) lookup_service="deploy_api" ;;
        docs) lookup_service="docs_api" ;;
        # App services
        apps) lookup_service="ai_portal" ;;
        proxy) lookup_service="nginx" ;;
        core-apps) lookup_service="ai_portal" ;;
        user-apps) lookup_service="user_apps" ;;
    esac
    
    # Get health URL from service registry
    local health_url
    health_url=$(get_service_health_url "$lookup_service" "$env" "proxmox" 2>/dev/null)
    
    if [[ -z "$health_url" ]]; then
        # Fallback to container ping for services without health endpoints
        local network_base
        case "$env" in
            staging) network_base="10.96.201" ;;
            production) network_base="10.96.200" ;;
            *) network_base="10.96.201" ;;
        esac
        
        local ip
        case "$service" in
            postgres|pg) ip="${network_base}.203" ;;
            minio|files) ip="${network_base}.205" ;;
            milvus) ip="${network_base}.204" ;;
            agent|agent-api) ip="${network_base}.202" ;;
            ingest|data-api|data) ip="${network_base}.206" ;;
            authz|authz-api) ip="${network_base}.210" ;;
            core-apps|apps) ip="${network_base}.201" ;;
            proxy|nginx) ip="${network_base}.200" ;;
            litellm) ip="${network_base}.207" ;;
            vllm) ip="${network_base}.208" ;;
            embedding) ip="${network_base}.208" ;;
            redis) ip="${network_base}.206" ;;
            user-apps) ip="${network_base}.212" ;;
            *) echo "unknown"; return ;;
        esac
        
        if ping -c 1 -W 1 "$ip" &>/dev/null 2>&1; then
            echo "running"
        else
            echo "unreachable"
        fi
        return
    fi
    
    # Check actual health endpoint with short timeout
    local http_code
    http_code=$(curl -s -w "%{http_code}" --max-time 3 --connect-timeout 2 -o /dev/null "$health_url" 2>/dev/null || echo "000")
    
    case "$http_code" in
        200|301|302)
            echo "healthy"
            ;;
        401|403)
            # Auth required but service is up
            echo "healthy"
            ;;
        000)
            # Connection failed - check if container is at least pingable
            # Use get_service_ip for correct resolution (e.g. vllm uses prod when staging+use_production_vllm)
            local ip
            ip=$(get_service_ip "$lookup_service" "$env" "proxmox" 2>/dev/null || echo "")
            if [[ -n "$ip" ]]; then
                if ping -c 1 -W 1 "$ip" &>/dev/null 2>&1; then
                    echo "stopped"  # Container up but service not responding
                else
                    echo "unreachable"  # Container not reachable
                fi
            else
                echo "unreachable"
            fi
            ;;
        5*)
            # Server error - service unhealthy
            echo "unhealthy"
            ;;
        *)
            # Other codes
            echo "unknown"
            ;;
    esac
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

# Display all services with status (in 2 columns, with parallel health checks)
show_services_status() {
    local backend
    backend=$(get_backend_type)
    
    echo ""
    
    # Collect all services first
    local all_services=()
    for group in "${SERVICE_GROUP_ORDER[@]}"; do
        local services
        services=$(get_services_for_group "$group")
        for service in $services; do
            all_services+=("$service")
        done
    done
    
    # Run health checks in parallel and store results in temp files
    local tmpdir=$(mktemp -d)
    
    for service in "${all_services[@]}"; do
        (
            status=$(get_service_status "$service")
            echo "$status" > "$tmpdir/$service.status"
        ) &
    done
    
    # Wait for all background jobs to complete
    wait
    
    # Helper function to get status from temp file (Bash 3.2 compatible)
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
    echo ""
    
    # Display services by group
    for group in "${SERVICE_GROUP_ORDER[@]}"; do
        local services
        services=$(get_services_for_group "$group")
        
        printf "  ${BOLD}${group}${NC}\n"
        
        # Collect services into an array
        local services_arr=()
        for service in $services; do
            services_arr+=("$service")
        done
        
        # Display in 2 columns
        local i=0
        local count=${#services_arr[@]}
        while [[ $i -lt $count ]]; do
            local service1="${services_arr[$i]}"
            local status1=$(get_cached_status "$service1")
            local status_icon1 status_color1
            case "$status1" in
                healthy)    status_icon1="●" ; status_color1="${GREEN}" ;;
                running)    status_icon1="●" ; status_color1="${GREEN}" ;;
                stopped)    status_icon1="○" ; status_color1="${RED}" ;;
                unhealthy)  status_icon1="●" ; status_color1="${YELLOW}" ;;
                missing)    status_icon1="○" ; status_color1="${DIM}" ;;
                unreachable) status_icon1="○" ; status_color1="${RED}" ;;
                *)          status_icon1="?" ; status_color1="${DIM}" ;;
            esac
            
            # Second column (if exists)
            if [[ $((i+1)) -lt $count ]]; then
                local service2="${services_arr[$((i+1))]}"
                local status2=$(get_cached_status "$service2")
                local status_icon2 status_color2
                case "$status2" in
                    healthy)    status_icon2="●" ; status_color2="${GREEN}" ;;
                    running)    status_icon2="●" ; status_color2="${GREEN}" ;;
                    stopped)    status_icon2="○" ; status_color2="${RED}" ;;
                    unhealthy)  status_icon2="●" ; status_color2="${YELLOW}" ;;
                    missing)    status_icon2="○" ; status_color2="${DIM}" ;;
                    unreachable) status_icon2="○" ; status_color2="${RED}" ;;
                    *)          status_icon2="?" ; status_color2="${DIM}" ;;
                esac
                local display1 display2
                display1=$(get_service_display_name_for_env "$service1" "$(get_current_env)")
                display2=$(get_service_display_name_for_env "$service2" "$(get_current_env)")
                printf "    ${status_color1}${status_icon1}${NC} %-22s ${DIM}%-10s${NC}  ${status_color2}${status_icon2}${NC} %-22s ${DIM}%s${NC}\n" \
                    "$display1" "$status1" "$display2" "$status2"
            else
                # Single item on last row
                local display1
                display1=$(get_service_display_name_for_env "$service1" "$(get_current_env)")
                printf "    ${status_color1}${status_icon1}${NC} %-22s ${DIM}%s${NC}\n" "$display1" "$status1"
            fi
            
            i=$((i+2))
        done
        
        echo ""
    done
    
    # Clean up temp files
    rm -rf "$tmpdir"
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
        env=$(get_current_env)
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
        env=$(get_current_env)
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
        env=$(get_current_env)
        cd "${REPO_ROOT}/provision/ansible"
        make restart-all INV="inventory/${env}"
    fi
    
    success "Services restarted"
    read -n 1 -s -r -p "Press any key to continue..."
}

# Check if a service runs on the host (not in Docker/Proxmox)
is_host_native_service() {
    local service="$1"
    case "$service" in
        mlx|host-agent) return 0 ;;
        *) return 1 ;;
    esac
}

# Manage a host-native service (MLX, host-agent)
# These run directly on the host machine, not in Docker or Proxmox containers.
# Actions are routed to Makefile targets (e.g., make mlx-start, make mlx-stop).
manage_host_native_service() {
    local service="$1"
    
    while true; do
        clear
        box_start 70 double "$CYAN"
        box_header "MANAGE: $service (host-native)"
        box_empty
        
        local status
        status=$(get_service_status "$service")
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
            1) # Start
                echo ""
                cd "$REPO_ROOT"
                make "${service}-start" || echo "Failed to start"
                echo ""
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            2) # Stop
                echo ""
                cd "$REPO_ROOT"
                make "${service}-stop" || echo "Failed to stop"
                echo ""
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            3) # Restart
                echo ""
                cd "$REPO_ROOT"
                make "${service}-restart" || echo "Failed to restart"
                echo ""
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            4) # Status (detailed)
                echo ""
                cd "$REPO_ROOT"
                make "${service}-status" || echo "Failed to get status"
                echo ""
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            b|B)
                return
                ;;
            m|M)
                return 1
                ;;
        esac
    done
}

# Manage individual service
manage_service() {
    local service="$1"
    
    # Host-native services get their own management flow
    if is_host_native_service "$service"; then
        manage_host_native_service "$service"
        return $?
    fi
    
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
        box_line "  ${BOLD}5)${NC} Redeploy (rebuild container)"
        
        # Add dev mode note for Python API services
        if [[ "$backend" == "docker" ]]; then
            case "$service" in
                authz-api|data-api|search-api|agent-api|deploy-api|docs-api|embedding-api)
                    box_empty
                    box_line "  ${DIM}Note: In dev mode, Python APIs have hot-reload.${NC}"
                    box_line "  ${DIM}Use Restart for code changes, Redeploy only for${NC}"
                    box_line "  ${DIM}requirements.txt or Dockerfile changes.${NC}"
                    ;;
            esac
        fi
        
        # Add options for core-apps service
        if [[ "$service" == "core-apps" ]]; then
            # Option 6: Rebuild Container (Docker only)
            if [[ "$backend" == "docker" ]]; then
                box_line "  ${BOLD}6)${NC} Rebuild Container (full Docker rebuild)"
            fi
            # Option 7: Rebuild App (always available)
            box_line "  ${BOLD}7)${NC} Rebuild App (from source, no container restart)"
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
                if [[ "$backend" == "docker" ]]; then
                    docker start "${prefix}-${service}" 2>/dev/null || echo "Failed to start"
                else
                    local env
                    env=$(get_current_env)
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
                    env=$(get_current_env)
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
                    env=$(get_current_env)
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
                    env=$(get_current_env)
                    ssh "root@${service}" "journalctl -u ${service} -f" 2>/dev/null || echo "No logs available"
                fi
                ;;
            5) # Redeploy
                echo ""
                info "Redeploying ${service}..."
                if [[ "$backend" == "docker" ]]; then
                    local env
                    env=$(get_current_env)
                    cd "$REPO_ROOT"
                    make docker-build SERVICE="$service" ENV="$env" && make docker-up SERVICE="$service" ENV="$env"
                else
                    local env
                    env=$(get_current_env)
                    cd "${REPO_ROOT}/provision/ansible"
                    # Map service to correct make target
                    local make_target
                    case "$service" in
                        core-apps|apps) make_target="apps" ;;
                        nginx|proxy) make_target="nginx" ;;
                        postgres|pg) make_target="pg" ;;
                        minio|files) make_target="files" ;;
                        authz*) make_target="authz" ;;
                        agent*) make_target="agent" ;;
                        data*|ingest*) make_target="data" ;;
                        search*) make_target="search-api" ;;
                        docs*) make_target="docs" ;;
                        *) make_target="$service" ;;
                    esac
                    make "$make_target" INV="inventory/${env}"
                fi
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            6) # Rebuild Container (only for core-apps + Docker)
                if [[ "$service" != "core-apps" ]] || [[ "$backend" != "docker" ]]; then
                    continue
                fi
                
                echo ""
                info "Rebuilding core-apps container (full Docker rebuild)..."
                local env
                env=$(get_current_env)
                cd "$REPO_ROOT"
                make docker-build SERVICE=core-apps ENV="$env" && make docker-up SERVICE=core-apps ENV="$env"
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            7) # Rebuild App (only for core-apps)
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
                        info "Rebuilding ai-portal from source..."
                        if [[ "$backend" == "docker" ]]; then
                            # Docker: use entrypoint.sh deploy command
                            docker exec "${prefix}-core-apps" /usr/local/bin/entrypoint.sh deploy ai-portal main
                        else
                            # Proxmox: use Ansible
                            local env
                            env=$(get_current_env)
                            cd "${REPO_ROOT}/provision/ansible"
                            make deploy-ai-portal INV="inventory/${env}"
                        fi
                        read -n 1 -s -r -p "Press any key to continue..."
                        ;;
                    2) # agent-manager
                        echo ""
                        info "Rebuilding agent-manager from source..."
                        if [[ "$backend" == "docker" ]]; then
                            # Docker: use entrypoint.sh deploy command
                            docker exec "${prefix}-core-apps" /usr/local/bin/entrypoint.sh deploy agent-manager main
                        else
                            # Proxmox: use Ansible
                            local env
                            env=$(get_current_env)
                            cd "${REPO_ROOT}/provision/ansible"
                            make deploy-agent-manager INV="inventory/${env}"
                        fi
                        read -n 1 -s -r -p "Press any key to continue..."
                        ;;
                    3) # both
                        echo ""
                        info "Rebuilding ai-portal from source..."
                        if [[ "$backend" == "docker" ]]; then
                            # Docker: use entrypoint.sh deploy command
                            docker exec "${prefix}-core-apps" /usr/local/bin/entrypoint.sh deploy ai-portal main
                            echo ""
                            info "Rebuilding agent-manager from source..."
                            docker exec "${prefix}-core-apps" /usr/local/bin/entrypoint.sh deploy agent-manager main
                        else
                            # Proxmox: use Ansible
                            local env
                            env=$(get_current_env)
                            cd "${REPO_ROOT}/provision/ansible"
                            make deploy-ai-portal INV="inventory/${env}"
                            echo ""
                            info "Rebuilding agent-manager from source..."
                            make deploy-agent-manager INV="inventory/${env}"
                        fi
                        read -n 1 -s -r -p "Press any key to continue..."
                        ;;
                    b|B)
                        # Go back to service menu
                        ;;
                esac
                ;;
            b|B)
                # Return to select_service (the calling function loops back)
                return
                ;;
            m|M)
                # Return to main menu (return 1 to signal main menu)
                return 1
                ;;
        esac
    done
}

# Select a service to manage (in 2 columns, no status check)
select_service() {
    while true; do
        clear
        box_header "SELECT SERVICE"
        echo ""
        
        local services=()
        local idx=1
        
        for group in "${SERVICE_GROUP_ORDER[@]}"; do
            echo -e "  ${BOLD}${group}${NC}"
            
            # Collect services for this group
            local group_services=()
            for service in $(get_services_for_group "$group"); do
                services+=("$service")
                group_services+=("$service:$idx")
                ((idx++))
            done
            
            # Display in 2 columns
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
            # If manage_service returned 1, user wants to go to main menu
            if [[ $ret -eq 1 ]]; then
                return
            fi
            # Otherwise loop back to select_service (don't return to main)
        fi
    done
}

# ============================================================================
# Main Menu
# ============================================================================

show_manage_menu() {
    local backend
    backend=$(get_backend_type)
    local env
    env=$(get_current_env)
    
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
    printf "  ${BOLD}Utilities${NC}\n"
    printf "    ${BOLD}d)${NC} Update Internal DNS (/etc/hosts on all containers)\n"
    echo ""
    printf "  ${DIM}b = back to main menu    q = quit${NC}\n"
    echo ""
    box_footer
}

main() {
    # If no environment is set, show error and exit
    if [[ -z "$BUSIBOX_ENV" ]]; then
        echo ""
        echo "No environment configured."
        echo "Run 'make' to select an environment first."
        exit 1
    fi
    
    # Ensure ENVIRONMENT is set in state file if BUSIBOX_ENV is known
    local current_env
    current_env=$(get_state "ENVIRONMENT")
    if [[ -z "$current_env" ]] && [[ -n "$BUSIBOX_ENV" ]]; then
        set_state "ENVIRONMENT" "$BUSIBOX_ENV"
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
            d|D)
                # Update internal DNS
                echo ""
                info "Updating internal DNS (/etc/hosts) on all containers..."
                local env
                env=$(get_current_env)
                cd "${REPO_ROOT}/provision/ansible"
                make internal-dns INV="inventory/${env}"
                success "Internal DNS updated"
                read -n 1 -s -r -p "Press any key to continue..."
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
