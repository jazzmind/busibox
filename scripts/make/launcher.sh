#!/usr/bin/env bash
#
# Busibox Launcher Menu
# =====================
#
# A simple launcher that provides the main entry point to all Busibox systems.
# Designed to be clean and minimal, delegating to specialized scripts.
#
# Usage:
#   make                    # Interactive launcher
#   make install            # Fresh installation
#   make update             # Update existing installation
#   make manage             # Service management
#   make test               # Testing system
#
set -euo pipefail

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
source "${REPO_ROOT}/scripts/lib/status.sh"

# ============================================================================
# Constants
# ============================================================================
VERSION="1.0.0"

# ============================================================================
# Installation Status Detection
# ============================================================================

# Check if Docker is available and running
check_docker_available() {
    if ! command -v docker &>/dev/null; then
        return 1
    fi
    if ! docker info &>/dev/null; then
        return 1
    fi
    return 0
}

# Check if Proxmox tools are available (pct command)
check_proxmox_available() {
    if ! command -v pct &>/dev/null; then
        return 1
    fi
    return 0
}

# Check if Docker stack exists for environment
# Args: env_name (development, staging, production)
check_docker_stack_exists() {
    local env="$1"
    local prefix
    
    case "$env" in
        development) prefix="dev" ;;
        staging) prefix="staging" ;;
        production) prefix="prod" ;;
        *) prefix="dev" ;;
    esac
    
    # Check if any containers with this prefix exist
    local count
    count=$(docker ps -a --filter "name=${prefix}-" --format '{{.Names}}' 2>/dev/null | wc -l | tr -d ' ')
    [[ "$count" -gt 0 ]]
}

# Check if Proxmox containers exist for environment
# Args: env_name (staging, production)
check_proxmox_containers_exist() {
    local env="$1"
    local base_ctid
    
    case "$env" in
        production) base_ctid=200 ;;
        staging) base_ctid=300 ;;
        *) return 1 ;;
    esac
    
    # Check if proxy container (200 or 300) exists
    pct status "$base_ctid" &>/dev/null
}

# Detect installation status for current environment
# Returns: not_installed, partial, installed
# Strategy: Check if core services are running. If yes, system is installed.
detect_installation_status() {
    local env backend
    env=$(get_state "ENVIRONMENT" || echo "")
    
    # No environment configured
    if [[ -z "$env" ]]; then
        echo "not_installed"
        return
    fi
    
    # Determine backend
    local env_upper
    env_upper=$(echo "$env" | tr '[:lower:]' '[:upper:]')
    backend=$(get_state "BACKEND_${env_upper}" || echo "")
    
    # Development always uses docker
    if [[ "$env" == "development" ]]; then
        backend="docker"
    fi
    
    if [[ -z "$backend" ]]; then
        echo "not_installed"
        return
    fi
    
    # Check if core services are running
    case "$backend" in
        docker)
            if ! check_docker_available 2>/dev/null; then
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
            
            # Check for core services that must be running
            # postgres, authz-api, core-apps are essential
            local core_services=("postgres" "authz-api" "core-apps")
            local running=0
            local total=${#core_services[@]}
            
            for service in "${core_services[@]}"; do
                if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${prefix}-${service}$"; then
                    ((running++))
                fi
            done
            
            # If all core services are running, system is installed
            if [[ $running -eq $total ]]; then
                echo "installed"
            elif [[ $running -gt 0 ]]; then
                echo "partial"  # Some services running
            else
                echo "not_installed"
            fi
            ;;
        proxmox)
            if ! check_proxmox_available 2>/dev/null; then
                echo "not_installed"
                return
            fi
            
            local base_ctid
            case "$env" in
                production) base_ctid=200 ;;
                staging) base_ctid=300 ;;
                *) base_ctid=300 ;;
            esac
            
            # Check if proxy container is running
            local status
            status=$(pct status "$base_ctid" 2>/dev/null | awk '{print $2}')
            if [[ "$status" == "running" ]]; then
                echo "installed"
            else
                echo "not_installed"
            fi
            ;;
        *)
            echo "not_installed"
            ;;
    esac
}

# Get backend for current environment
# Args: env_name
get_current_backend() {
    local env="$1"
    local env_upper
    env_upper=$(echo "$env" | tr '[:lower:]' '[:upper:]')
    
    # Development always uses docker
    if [[ "$env" == "development" ]]; then
        echo "docker"
        return
    fi
    
    get_state "BACKEND_${env_upper}" || echo ""
}

# Get environment display info
get_env_display() {
    local env backend
    env=$(get_state "ENVIRONMENT" || echo "not set")
    
    if [[ "$env" == "not set" ]] || [[ -z "$env" ]]; then
        echo "Not configured"
        return
    fi
    
    backend=$(get_current_backend "$env")
    
    if [[ -n "$backend" ]]; then
        echo "$env ($backend)"
    else
        echo "$env"
    fi
}

# ============================================================================
# Status Display
# ============================================================================

# Get prefix for Docker containers based on environment
get_docker_prefix() {
    local env="$1"
    case "$env" in
        development) echo "dev" ;;
        staging) echo "staging" ;;
        production) echo "prod" ;;
        *) echo "dev" ;;
    esac
}

# Get Docker container status string
get_docker_status_string() {
    local env
    env=$(get_state "ENVIRONMENT" || echo "development")
    local prefix
    prefix=$(get_docker_prefix "$env")
    
    local total running stopped
    
    total=$(docker ps -a --filter "name=${prefix}-" --format '{{.Names}}' 2>/dev/null | wc -l | tr -d ' ')
    running=$(docker ps --filter "name=${prefix}-" --format '{{.Names}}' 2>/dev/null | wc -l | tr -d ' ')
    stopped=$((total - running))
    
    if [[ "$total" -eq 0 ]]; then
        echo "${YELLOW}None found${NC}"
    elif [[ "$stopped" -eq 0 ]]; then
        echo "${GREEN}$running running${NC}"
    else
        echo "${GREEN}$running running${NC}, ${RED}$stopped stopped${NC}"
    fi
}

# Get Proxmox container status string
get_proxmox_container_status_string() {
    local env="$1"
    local base_ctid
    
    case "$env" in
        production) base_ctid=200 ;;
        staging) base_ctid=300 ;;
        *) echo "${YELLOW}Unknown environment${NC}"; return ;;
    esac
    
    # Count running and stopped containers in range
    local running=0
    local stopped=0
    local total=0
    
    # Check containers in range (base to base+19)
    for ctid in $(seq "$base_ctid" $((base_ctid + 19))); do
        if pct status "$ctid" &>/dev/null; then
            ((total++))
            local status
            status=$(pct status "$ctid" 2>/dev/null | awk '{print $2}')
            if [[ "$status" == "running" ]]; then
                ((running++))
            else
                ((stopped++))
            fi
        fi
    done
    
    if [[ "$total" -eq 0 ]]; then
        echo "${YELLOW}None found${NC}"
    elif [[ "$stopped" -eq 0 ]]; then
        echo "${GREEN}$running running${NC}"
    else
        echo "${GREEN}$running running${NC}, ${RED}$stopped stopped${NC}"
    fi
}

show_status_view() {
    local install_status
    install_status=$(detect_installation_status)
    
    local env_display
    env_display=$(get_env_display)
    
    local env backend
    env=$(get_state "ENVIRONMENT" || echo "")
    backend=$(get_current_backend "$env")
    
    if [[ "$install_status" == "installed" ]]; then
        # Show full status view for installed systems
        box_header "STATUS"
        box_empty
        box_line "  ${CYAN}Environment:${NC} $env_display"
        
        # Quick service status (non-blocking)
        if [[ "$backend" == "docker" ]]; then
            local container_status
            container_status=$(get_docker_status_string)
            box_line "  ${CYAN}Containers:${NC}  $container_status"
        elif [[ "$backend" == "proxmox" ]]; then
            local container_status
            container_status=$(get_proxmox_container_status_string "$env")
            box_line "  ${CYAN}Containers:${NC}  $container_status"
        fi
        
        box_empty
        box_footer
        echo ""
    elif [[ "$install_status" == "partial" ]]; then
        box_start 70 double "$YELLOW"
        box_header "INCOMPLETE INSTALLATION"
        box_empty
        box_line "  ${YELLOW}Status:${NC} Partial installation detected"
        box_line "  ${CYAN}Environment:${NC} $env_display"
        box_empty
        box_line "  ${DIM}Services may not be running. Use Install to complete setup.${NC}"
        box_empty
        box_footer
        echo ""
    fi
}

# ============================================================================
# Main Menu
# ============================================================================

show_main_menu() {
    local install_status
    install_status=$(detect_installation_status)
    
    # Menu options based on installation status
    if [[ "$install_status" == "not_installed" ]] || [[ "$install_status" == "partial" ]]; then
        box_line "  ${BOLD}1)${NC} Install"
        box_line "  ${DIM}2) Update (requires installation)${NC}"
        box_line "  ${DIM}3) Manage (requires installation)${NC}"
        box_line "  ${DIM}4) Test (requires installation)${NC}"
    else
        box_line "  ${BOLD}1)${NC} Uninstall / Reinstall"
        box_line "  ${BOLD}2)${NC} Update"
        box_line "  ${BOLD}3)${NC} Manage"
        box_line "  ${BOLD}4)${NC} Test"
    fi
    
    box_empty
    box_line "  ${DIM}e = environment    h = help    q = quit${NC}"
}

# ============================================================================
# Environment Selection
# ============================================================================

select_environment() {
    clear
    box_start 70 double "$CYAN"
    box_header "SELECT ENVIRONMENT"
    box_empty
    
    local current_env current_backend
    current_env=$(get_state "ENVIRONMENT" || echo "none")
    current_backend=$(get_current_backend "$current_env")
    
    if [[ "$current_env" == "none" ]] || [[ -z "$current_env" ]]; then
        box_line "  Current: ${DIM}Not configured${NC}"
    elif [[ -n "$current_backend" ]]; then
        box_line "  Current: ${CYAN}${current_env}${NC} (${current_backend})"
    else
        box_line "  Current: ${CYAN}${current_env}${NC}"
    fi
    box_empty
    box_separator
    box_empty
    
    # Show current backend for staging/production if set
    local staging_backend production_backend
    staging_backend=$(get_current_backend "staging")
    production_backend=$(get_current_backend "production")
    
    box_line "  ${BOLD}1)${NC} development  ${DIM}Docker on this machine${NC}"
    if [[ -n "$staging_backend" ]]; then
        box_line "  ${BOLD}2)${NC} staging      ${DIM}${staging_backend}${NC}"
    else
        box_line "  ${BOLD}2)${NC} staging      ${DIM}(select backend)${NC}"
    fi
    if [[ -n "$production_backend" ]]; then
        box_line "  ${BOLD}3)${NC} production   ${DIM}${production_backend}${NC}"
    else
        box_line "  ${BOLD}3)${NC} production   ${DIM}(select backend)${NC}"
    fi
    box_empty
    box_line "  ${DIM}b = back${NC}"
    box_empty
    box_footer
    echo ""
    
    read -n 1 -s -r -p "Select environment: " choice
    echo ""
    
    case "$choice" in
        1)
            set_state "ENVIRONMENT" "development"
            set_state "BACKEND_DEVELOPMENT" "docker"
            export BUSIBOX_ENV="development"
            export CONTAINER_PREFIX="dev"
            info "Switched to development environment"
            sleep 1
            ;;
        2)
            set_state "ENVIRONMENT" "staging"
            export BUSIBOX_ENV="staging"
            export CONTAINER_PREFIX="staging"
            # Always ask for backend to allow changing it
            select_backend_for_env "staging"
            info "Switched to staging environment"
            sleep 1
            ;;
        3)
            set_state "ENVIRONMENT" "production"
            export BUSIBOX_ENV="production"
            export CONTAINER_PREFIX="prod"
            # Always ask for backend to allow changing it
            select_backend_for_env "production"
            info "Switched to production environment"
            sleep 1
            ;;
        b|B)
            return
            ;;
    esac
}

# Select backend (docker or proxmox) for an environment
select_backend_for_env() {
    local env="$1"
    local env_upper
    env_upper=$(echo "$env" | tr '[:lower:]' '[:upper:]')
    
    echo ""
    box_start 70 double "$CYAN"
    box_header "SELECT BACKEND FOR ${env_upper}"
    box_empty
    box_line "  ${BOLD}1)${NC} Docker    ${DIM}Docker containers on this machine${NC}"
    box_line "  ${BOLD}2)${NC} Proxmox   ${DIM}LXC containers on Proxmox server${NC}"
    box_empty
    box_footer
    echo ""
    
    read -n 1 -s -r -p "Select backend: " choice
    echo ""
    
    case "$choice" in
        1)
            set_state "BACKEND_${env_upper}" "docker"
            ;;
        2)
            set_state "BACKEND_${env_upper}" "proxmox"
            ;;
    esac
}

# ============================================================================
# Help
# ============================================================================

show_help() {
    clear
    box_start 70 double "$CYAN"
    box_header "HELP"
    box_empty
    box_line "  ${BOLD}Installation${NC}"
    box_line "    ${CYAN}make install${NC}  Fresh installation wizard"
    box_empty
    box_line "  ${BOLD}Update${NC}"
    box_line "    ${CYAN}make update${NC}   Update existing installation"
    box_line "                    Preserves data (postgres, minio, etc)"
    box_empty
    box_line "  ${BOLD}Manage${NC}"
    box_line "    ${CYAN}make manage${NC}   Service management"
    box_line "                    Start/stop services, view logs"
    box_empty
    box_line "  ${BOLD}Test${NC}"
    box_line "    ${CYAN}make test${NC}     Testing system"
    box_line "                    Integration tests, health checks"
    box_empty
    box_line "  ${BOLD}Direct Commands${NC}"
    box_line "    ${DIM}make docker-up${NC}       Start Docker services"
    box_line "    ${DIM}make docker-down${NC}     Stop Docker services"
    box_line "    ${DIM}make docker-logs${NC}     View Docker logs"
    box_line "    ${DIM}make docker-ps${NC}       List containers"
    box_empty
    box_footer
    echo ""
    read -n 1 -s -r -p "Press any key to continue..."
}

# ============================================================================
# Action Handlers
# ============================================================================

handle_install() {
    local install_status
    install_status=$(detect_installation_status)
    
    # Get current environment info
    local env backend
    env=$(get_state "ENVIRONMENT" || echo "")
    backend=$(get_current_backend "$env")
    
    if [[ "$install_status" == "installed" ]]; then
        # Show uninstall/reinstall submenu
        clear
        box_start 70 double "$CYAN"
        box_header "UNINSTALL / REINSTALL"
        box_empty
        if [[ -n "$env" ]]; then
            box_line "  ${CYAN}Environment:${NC} $env ($backend)"
            box_empty
            box_separator
            box_empty
        fi
        box_line "  ${BOLD}1)${NC} Reinstall (preserve data)"
        box_line "  ${BOLD}2)${NC} Full uninstall (remove all data)"
        box_empty
        box_line "  ${DIM}b = back${NC}"
        box_empty
        box_footer
        echo ""
        
        read -n 1 -s -r -p "Select option: " choice
        echo ""
        
        case "$choice" in
            1)
                # Reinstall - use install script with env
                if [[ -n "$env" ]]; then
                    exec bash "${SCRIPT_DIR}/install.sh" --env "$env" --backend "$backend" --reinstall
                else
                    exec bash "${SCRIPT_DIR}/install.sh" --reinstall
                fi
                ;;
            2)
                # Full uninstall
                echo ""
                printf "${YELLOW}Warning: This will remove ALL data including databases.${NC}\n"
                read -p "Type 'UNINSTALL' to confirm: " confirm
                if [[ "$confirm" == "UNINSTALL" ]]; then
                    perform_uninstall "$env" "$backend"
                else
                    echo "Cancelled."
                    sleep 1
                fi
                ;;
            b|B)
                return
                ;;
        esac
    else
        # Fresh install
        if [[ -z "$env" ]]; then
            # No environment configured, let install.sh handle it
            exec bash "${SCRIPT_DIR}/install.sh"
        else
            # Pass env to install script
            exec bash "${SCRIPT_DIR}/install.sh" --env "$env" --backend "$backend"
        fi
    fi
}

# Perform uninstall based on environment and backend
perform_uninstall() {
    local env="$1"
    local backend="$2"
    
    info "Uninstalling Busibox..."
    
    if [[ "$backend" == "docker" ]]; then
        # Get container prefix
        local prefix
        case "$env" in
            development) prefix="dev" ;;
            staging) prefix="staging" ;;
            production) prefix="prod" ;;
            *) prefix="dev" ;;
        esac
        
        info "Stopping and removing Docker containers (prefix: ${prefix})..."
        
        # Stop and remove containers
        docker ps -a --filter "name=${prefix}-" --format '{{.Names}}' | while read -r container; do
            info "Removing container: $container"
            docker stop "$container" 2>/dev/null || true
            docker rm "$container" 2>/dev/null || true
        done
        
        # Remove volumes
        info "Removing Docker volumes..."
        docker volume ls --filter "name=${prefix}" --format '{{.Name}}' | while read -r volume; do
            info "Removing volume: $volume"
            docker volume rm "$volume" 2>/dev/null || true
        done
        
        # Remove network
        info "Removing Docker network..."
        docker network rm "${prefix}-busibox" 2>/dev/null || true
        
        # Remove env file
        local env_file="${REPO_ROOT}/.env.${prefix}"
        if [[ -f "$env_file" ]]; then
            info "Removing env file: $env_file"
            rm -f "$env_file"
        fi
        
        # Remove state file
        local state_file="${REPO_ROOT}/.busibox-state-${prefix}"
        if [[ -f "$state_file" ]]; then
            info "Removing state file: $state_file"
            rm -f "$state_file"
        fi
        
        success "Docker uninstall complete"
        
    elif [[ "$backend" == "proxmox" ]]; then
        # Proxmox uninstall requires more care
        local base_ctid
        case "$env" in
            production) base_ctid=200 ;;
            staging) base_ctid=300 ;;
            *)
                error "Unknown environment for Proxmox: $env"
                return 1
                ;;
        esac
        
        warn "Proxmox uninstall will stop and destroy containers ${base_ctid}-$((base_ctid+19))"
        read -p "Are you sure? Type 'YES' to confirm: " confirm
        if [[ "$confirm" != "YES" ]]; then
            echo "Cancelled."
            return
        fi
        
        info "Stopping and destroying Proxmox containers..."
        for ctid in $(seq "$base_ctid" $((base_ctid + 19))); do
            if pct status "$ctid" &>/dev/null; then
                info "Destroying container $ctid..."
                pct stop "$ctid" 2>/dev/null || true
                pct destroy "$ctid" 2>/dev/null || true
            fi
        done
        
        success "Proxmox uninstall complete"
    else
        error "Unknown backend: $backend"
        return 1
    fi
    
    # Clear state
    set_state "INSTALL_STATUS" "not_installed"
    set_state "ENVIRONMENT" ""
    
    success "Uninstall complete!"
    sleep 2
}

handle_update() {
    local install_status
    install_status=$(detect_installation_status)
    
    if [[ "$install_status" == "not_installed" ]]; then
        echo ""
        printf "${YELLOW}Installation required. Please run Install first.${NC}\n"
        sleep 2
        return
    fi
    
    local env backend
    env=$(get_state "ENVIRONMENT" || echo "")
    backend=$(get_current_backend "$env")
    
    exec bash "${SCRIPT_DIR}/update.sh" --env "$env" --backend "$backend"
}

handle_manage() {
    local install_status
    install_status=$(detect_installation_status)
    
    if [[ "$install_status" == "not_installed" ]]; then
        echo ""
        printf "${YELLOW}Installation required. Please run Install first.${NC}\n"
        sleep 2
        return
    fi
    
    local env backend
    env=$(get_state "ENVIRONMENT" || echo "")
    backend=$(get_current_backend "$env")
    
    exec bash "${SCRIPT_DIR}/manage.sh" --env "$env" --backend "$backend"
}

handle_test() {
    local install_status
    install_status=$(detect_installation_status)
    
    if [[ "$install_status" == "not_installed" ]]; then
        echo ""
        printf "${YELLOW}Installation required. Please run Install first.${NC}\n"
        sleep 2
        return
    fi
    
    local env backend
    env=$(get_state "ENVIRONMENT" || echo "")
    backend=$(get_current_backend "$env")
    
    exec bash "${SCRIPT_DIR}/test-menu.sh" --env "$env" --backend "$backend"
}

# ============================================================================
# Main Loop
# ============================================================================

main() {
    # Initialize state if needed
    init_state
    
    while true; do
        clear
        
        # Get current environment info for header
        local env_display
        env_display=$(get_env_display)
        
        # Reset box state and start fresh
        box_start 70 double "$CYAN"
        
        # Show header with environment
        if [[ "$env_display" == "Not configured" ]]; then
            box_header "BUSIBOX"
        else
            box_header "BUSIBOX" "$env_display"
        fi
        
        # Show status if installed (this may close and open its own box)
        local install_status
        install_status=$(detect_installation_status)
        
        if [[ "$install_status" == "installed" ]] || [[ "$install_status" == "partial" ]]; then
            box_footer
            echo ""
            show_status_view
            box_start 70 double "$CYAN"
            box_header "MENU"
        else
            box_empty
        fi
        
        # Show menu
        show_main_menu
        
        box_footer
        echo ""
        
        # Get user input
        read -n 1 -s -r -p "Select option: " choice
        echo ""
        
        case "$choice" in
            1)
                handle_install
                ;;
            2)
                handle_update
                ;;
            3)
                handle_manage
                ;;
            4)
                handle_test
                ;;
            e|E)
                select_environment
                ;;
            h|H)
                show_help
                ;;
            q|Q)
                echo ""
                echo "Goodbye!"
                exit 0
                ;;
            *)
                # Invalid option, just redraw
                ;;
        esac
    done
}

# Run main
main "$@"
