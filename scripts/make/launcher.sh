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
#   make install            # Fresh installation / add services
#   make manage             # Service management (start/stop/logs/secrets)
#   make test               # Testing system
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

# Source backend common library (for tunnel status, detection helpers)
source "${REPO_ROOT}/scripts/lib/backends/common.sh"

# Initialize profiles (migrates from legacy if needed)
profile_init

# Legacy compat: set BUSIBOX_ENV from active profile
_active_profile=$(profile_get_active)
if [[ -n "$_active_profile" ]]; then
    export BUSIBOX_ENV=$(profile_get "$_active_profile" "environment")
fi

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
        k8s)
            if ! command -v kubectl &>/dev/null; then
                echo "not_installed"
                return
            fi
            
            # Get kubeconfig from active profile or fallback
            local kubeconfig=""
            if [[ -n "${_active_profile:-}" ]]; then
                kubeconfig=$(profile_get_kubeconfig "$_active_profile" 2>/dev/null)
            fi
            if [[ -z "$kubeconfig" || ! -f "$kubeconfig" ]]; then
                kubeconfig="${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml"
            fi
            if [[ ! -f "$kubeconfig" ]]; then
                echo "not_installed"
                return
            fi
            
            local running_pods
            running_pods=$(KUBECONFIG="$kubeconfig" kubectl get pods -n busibox \
                --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d ' ')
            
            if [[ "$running_pods" -ge 3 ]]; then
                echo "installed"
            elif [[ "$running_pods" -gt 0 ]]; then
                echo "partial"
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

# Get environment display info (profile-aware)
get_env_display() {
    if [[ -n "$_active_profile" ]]; then
        local display
        display=$(profile_get_display "$_active_profile")
        echo "$display"
        return
    fi
    
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

# Get K8s pod status string
get_k8s_status_string() {
    local repo_root
    repo_root=$(_get_repo_root "$(pwd)" 2>/dev/null || echo "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)")
    
    # Try to get kubeconfig from active profile first
    local kubeconfig=""
    if [[ -n "${_active_profile:-}" ]]; then
        kubeconfig=$(profile_get_kubeconfig "$_active_profile" 2>/dev/null)
    fi
    # Fallback to legacy path
    if [[ -z "$kubeconfig" || ! -f "$kubeconfig" ]]; then
        kubeconfig="${repo_root}/k8s/kubeconfig-rackspace-spot.yaml"
    fi
    
    if [[ ! -f "$kubeconfig" ]]; then
        echo "${YELLOW}No kubeconfig${NC}"
        return
    fi
    
    if ! command -v kubectl &>/dev/null; then
        echo "${YELLOW}kubectl not installed${NC}"
        return
    fi
    
    local total running pending failed
    total=$(KUBECONFIG="$kubeconfig" kubectl get pods -n busibox --no-headers 2>/dev/null | wc -l | tr -d ' ')
    running=$(KUBECONFIG="$kubeconfig" kubectl get pods -n busibox --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d ' ')
    pending=$((total - running))
    
    if [[ "$total" -eq 0 ]]; then
        echo "${YELLOW}No pods found${NC}"
    elif [[ "$pending" -eq 0 ]]; then
        echo "${GREEN}$running running${NC}"
    else
        echo "${GREEN}$running running${NC}, ${YELLOW}$pending pending${NC}"
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
        elif [[ "$backend" == "k8s" ]]; then
            local pod_status
            pod_status=$(get_k8s_status_string)
            box_line "  ${CYAN}Pods:${NC}        $pod_status"
            
            # Show tunnel status for K8s
            local pid_file="${REPO_ROOT}/.k8s-connect.pid"
            if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
                local domain="${DOMAIN:-busibox.local}"
                box_line "  ${CYAN}Tunnel:${NC}      ${GREEN}ACTIVE${NC} - https://${domain}/portal"
            else
                box_line "  ${CYAN}Tunnel:${NC}      ${DIM}inactive${NC} ${DIM}(run 'make connect')${NC}"
            fi
        fi
        
        # MCP server status (independent of Busibox install)
        if [[ -f "${REPO_ROOT}/tools/mcp-app-builder/dist/index.js" ]]; then
            box_line "  ${CYAN}MCP server:${NC}   ${GREEN}installed${NC}"
        else
            box_line "  ${CYAN}MCP server:${NC}   ${DIM}uninstalled${NC}"
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
        if [[ -f "${REPO_ROOT}/tools/mcp-app-builder/dist/index.js" ]]; then
            box_line "  ${CYAN}MCP server:${NC}   ${GREEN}installed${NC}"
        else
            box_line "  ${CYAN}MCP server:${NC}   ${DIM}uninstalled${NC}"
        fi
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
    
    local profile_count
    profile_count=$(profile_count)
    
    # Menu options based on installation status
    if [[ "$install_status" == "not_installed" ]] || [[ "$install_status" == "partial" ]]; then
        box_line "  ${BOLD}1)${NC} Install"
        box_line "  ${DIM}2) Manage (requires installation)${NC}"
        box_line "  ${DIM}3) Test (requires installation)${NC}"
        box_line "  ${BOLD}4)${NC} Build App"
        box_line "  ${BOLD}5)${NC} Install MCP locally"
    else
        box_line "  ${BOLD}1)${NC} Uninstall / Reinstall"
        box_line "  ${BOLD}2)${NC} Manage"
        box_line "  ${BOLD}3)${NC} Test"
        
        # K8s: Show Connect option when installed
        local active_backend=""
        if [[ -n "$_active_profile" ]]; then
            active_backend=$(profile_get "$_active_profile" "backend")
        fi
        if [[ "$active_backend" == "k8s" ]]; then
            box_empty
            local pid_file="${REPO_ROOT}/.k8s-connect.pid"
            if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
                box_line "  ${BOLD}4)${NC} Disconnect (tunnel active)"
            else
                box_line "  ${BOLD}4)${NC} Connect (start tunnel)"
            fi
            box_line "  ${BOLD}5)${NC} Build App"
            box_line "  ${BOLD}6)${NC} Install MCP locally"
        else
            box_line "  ${BOLD}4)${NC} Build App"
            box_line "  ${BOLD}5)${NC} Install MCP locally"
        fi
    fi
    
    box_empty
    if [[ "$profile_count" -gt 1 ]]; then
        box_line "  ${DIM}p = switch profile [${profile_count}]    h = help    q = quit${NC}"
    else
        box_line "  ${DIM}p = profiles    h = help    q = quit${NC}"
    fi
}

# ============================================================================
# Profile Selection
# ============================================================================

select_profile() {
    clear
    box_start 70 double "$CYAN"
    box_header "DEPLOYMENT PROFILES"
    box_empty
    
    local active
    active=$(profile_get_active)
    
    local count
    count=$(profile_count)
    
    if [[ "$count" -eq 0 ]]; then
        box_line "  ${DIM}No profiles configured${NC}"
    else
        profile_list
    fi
    
    box_empty
    box_separator
    box_empty
    box_line "  ${BOLD}n)${NC} New profile"
    if [[ "$count" -gt 0 ]]; then
        box_line "  ${BOLD}d)${NC} Delete profile"
    fi
    box_line "  ${DIM}b = back${NC}"
    box_empty
    box_footer
    echo ""
    
    read -r -p "Select profile number or action: " choice
    
    case "$choice" in
        n|N)
            _create_profile_interactive
            ;;
        d|D)
            _delete_profile_interactive
            ;;
        b|B)
            return
            ;;
        [0-9]*)
            local target_id
            target_id=$(profile_get_by_index "$choice" 2>/dev/null)
            if [[ -n "$target_id" ]]; then
                profile_set_active "$target_id"
                _active_profile="$target_id"
                export BUSIBOX_ENV=$(profile_get "$target_id" "environment")
                # Re-source state to pick up new profile's state file
                BUSIBOX_STATE_FILE=""
                source "${REPO_ROOT}/scripts/lib/state.sh"
                info "Switched to profile: $target_id ($(profile_get_display "$target_id"))"
                sleep 1
            else
                warn "Invalid selection"
                sleep 1
            fi
            ;;
    esac
}

# Interactive profile creation
_create_profile_interactive() {
    echo ""
    box_start 70 double "$GREEN"
    box_header "NEW PROFILE"
    box_empty
    box_line "  ${BOLD}Environment:${NC}"
    box_line "    ${BOLD}1)${NC} development"
    box_line "    ${BOLD}2)${NC} staging"
    box_line "    ${BOLD}3)${NC} production"
    box_empty
    box_footer
    echo ""
    
    read -n 1 -s -r -p "Select environment: " env_choice
    echo ""
    
    local environment
    case "$env_choice" in
        1) environment="development" ;;
        2) environment="staging" ;;
        3) environment="production" ;;
        *) warn "Invalid choice"; sleep 1; return ;;
    esac
    
    echo ""
    box_start 70 double "$GREEN"
    box_header "SELECT BACKEND"
    box_empty
    box_line "  ${BOLD}1)${NC} Docker    ${DIM}Docker containers (local or remote)${NC}"
    box_line "  ${BOLD}2)${NC} Proxmox   ${DIM}LXC containers on Proxmox server${NC}"
    box_line "  ${BOLD}3)${NC} K8s       ${DIM}Kubernetes cluster${NC}"
    box_empty
    box_footer
    echo ""
    
    read -n 1 -s -r -p "Select backend: " backend_choice
    echo ""
    
    local backend
    case "$backend_choice" in
        1) backend="docker" ;;
        2) backend="proxmox" ;;
        3) backend="k8s" ;;
        *) warn "Invalid choice"; sleep 1; return ;;
    esac
    
    # Get label
    local default_label
    if [[ "$environment" == "development" && "$backend" == "docker" ]]; then
        default_label="local"
    else
        default_label="${environment}"
    fi
    
    echo ""
    read -r -p "Profile label [${default_label}]: " label
    label="${label:-$default_label}"
    
    # K8s: ask for kubeconfig
    local kubeconfig=""
    if [[ "$backend" == "k8s" ]]; then
        echo ""
        # Auto-detect kubeconfig files
        local kc_files
        kc_files=$(ls "${REPO_ROOT}"/k8s/kubeconfig-*.yaml 2>/dev/null || true)
        if [[ -n "$kc_files" ]]; then
            echo "Available kubeconfigs:"
            local kc_idx=1
            while IFS= read -r kc; do
                echo "  ${kc_idx}) $(basename "$kc")"
                ((kc_idx++))
            done <<< "$kc_files"
            echo ""
        fi
        read -r -p "Kubeconfig path (relative to repo root): " kubeconfig
    fi
    
    # Create the profile
    local profile_id
    profile_id=$(profile_create "$environment" "$backend" "$label" "" "$kubeconfig")
    
    if [[ -n "$profile_id" ]]; then
        profile_set_active "$profile_id"
        _active_profile="$profile_id"
        export BUSIBOX_ENV="$environment"
        BUSIBOX_STATE_FILE=""
        source "${REPO_ROOT}/scripts/lib/state.sh"
        success "Created and activated profile: ${profile_id} (${environment}/${backend}/${label})"
    else
        error "Failed to create profile"
    fi
    sleep 2
}

# Interactive profile deletion
_delete_profile_interactive() {
    echo ""
    read -r -p "Enter profile number to delete: " del_choice
    
    local target_id
    target_id=$(profile_get_by_index "$del_choice" 2>/dev/null)
    if [[ -z "$target_id" ]]; then
        warn "Invalid selection"
        sleep 1
        return
    fi
    
    local display
    display=$(profile_get_display "$target_id")
    echo ""
    read -r -p "Delete profile '${target_id}' (${display})? [y/N]: " confirm
    if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
        profile_delete "$target_id"
        # If we deleted the active profile, clear it
        if [[ "$_active_profile" == "$target_id" ]]; then
            _active_profile=""
            export BUSIBOX_ENV=""
        fi
        success "Deleted profile: ${target_id}"
    else
        echo "Cancelled."
    fi
    sleep 1
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
    box_line "                    Also used to add/update individual services"
    box_empty
    box_line "  ${BOLD}Manage${NC}"
    box_line "    ${CYAN}make manage${NC}   Service management"
    box_line "                    Start/stop, logs, rotate secrets, redeploy"
    box_empty
    box_line "  ${BOLD}Test${NC}"
    box_line "    ${CYAN}make test${NC}     Testing system"
    box_line "                    Integration tests, health checks"
    box_empty
    box_line "  ${BOLD}Build App${NC}"
    box_line "    ${CYAN}Build App${NC}    Clone busibox-template, install MCP app-builder"
    box_empty
    box_line "  ${BOLD}MCP (Cursor/Claude)${NC}"
    box_line "    ${CYAN}make mcp${NC}      Build MCP servers and write .cursor config"
    box_empty
    box_line "  ${BOLD}Docker Commands${NC}"
    box_line "    ${DIM}make docker-up${NC}       Start Docker services"
    box_line "    ${DIM}make docker-down${NC}     Stop Docker services"
    box_line "    ${DIM}make docker-logs${NC}     View Docker logs"
    box_line "    ${DIM}make docker-ps${NC}       List containers"
    box_empty
    box_line "  ${BOLD}K8s Commands${NC}"
    box_line "    ${DIM}make k8s-deploy${NC}      Full deploy (sync+build+apply)"
    box_line "    ${DIM}make k8s-sync${NC}        Sync code to build server"
    box_line "    ${DIM}make k8s-build${NC}       Build images on build server"
    box_line "    ${DIM}make k8s-status${NC}      Show K8s pod status"
    box_line "    ${DIM}make k8s-logs${NC}        View K8s pod logs"
    box_line "    ${DIM}make connect${NC}         HTTPS tunnel to K8s portal"
    box_line "    ${DIM}make disconnect${NC}      Stop K8s tunnel"
    box_empty
    box_footer
    echo ""
    read -n 1 -s -r -p "Press any key to continue..."
}

# ============================================================================
# Action Handlers
# ============================================================================

handle_install() {
    # Delegate to install-menu.sh which handles:
    # - Fresh install if no existing install
    # - Continue/Full/Clean options if existing install detected
    # Using --from-launcher so it knows to use 'b = back' and clear screen
    bash "${SCRIPT_DIR}/install-menu.sh" --from-launcher
    # If install-menu.sh returns (user pressed 'b'), we return to main menu
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
        
    elif [[ "$backend" == "k8s" ]]; then
        # Get kubeconfig from active profile or fallback
        local kubeconfig=""
        if [[ -n "${_active_profile:-}" ]]; then
            kubeconfig=$(profile_get_kubeconfig "$_active_profile" 2>/dev/null)
        fi
        if [[ -z "$kubeconfig" || ! -f "$kubeconfig" ]]; then
            kubeconfig="${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml"
        fi
        if [[ ! -f "$kubeconfig" ]]; then
            error "Kubeconfig not found"
            return 1
        fi
        
        warn "K8s uninstall will delete all Busibox resources from the cluster"
        read -p "Are you sure? Type 'YES' to confirm: " confirm
        if [[ "$confirm" != "YES" ]]; then
            echo "Cancelled."
            return
        fi
        
        # Disconnect tunnel first
        if [[ -f "${REPO_ROOT}/.k8s-connect.pid" ]]; then
            info "Disconnecting tunnel..."
            cd "$REPO_ROOT"
            make disconnect 2>/dev/null || true
        fi
        
        info "Deleting K8s resources..."
        
        local overlay_dir="${REPO_ROOT}/k8s/overlays/rackspace-spot"
        if [[ -d "$overlay_dir" ]]; then
            KUBECONFIG="$kubeconfig" kubectl delete -k "$overlay_dir" --ignore-not-found 2>/dev/null || true
        fi
        
        KUBECONFIG="$kubeconfig" kubectl delete secret busibox-secrets -n busibox --ignore-not-found 2>/dev/null || true
        KUBECONFIG="$kubeconfig" kubectl delete namespace busibox --timeout=120s 2>/dev/null || true
        
        success "K8s uninstall complete"
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

handle_mcp_install() {
    echo ""
    MCP_BUILD=1 bash "${SCRIPT_DIR}/mcp.sh" build
    echo ""
    read -n 1 -s -r -p "Press any key to continue..."
}

handle_build_app() {
    echo ""
    bash "${SCRIPT_DIR}/build-app.sh"
    echo ""
    read -n 1 -s -r -p "Press any key to continue..."
}

# ============================================================================
# Main Loop
# ============================================================================

main() {
    # Initialize state if needed
    init_state
    
    # If no active profile, prompt to create or select one
    if [[ -z "$_active_profile" ]]; then
        local count
        count=$(profile_count)
        if [[ "$count" -eq 0 ]]; then
            echo ""
            info "No deployment profiles found. Let's create one."
            _create_profile_interactive
        else
            select_profile
        fi
        _active_profile=$(profile_get_active)
        if [[ -z "$_active_profile" ]]; then
            echo ""
            echo "No profile selected. Exiting."
            exit 0
        fi
        export BUSIBOX_ENV=$(profile_get "$_active_profile" "environment")
        BUSIBOX_STATE_FILE=""
        source "${REPO_ROOT}/scripts/lib/state.sh"
    fi
    
    while true; do
        clear
        
        # Get current environment info for header
        local env_display
        env_display=$(get_env_display)
        
        # Reset box state and start fresh
        box_start 70 double "$CYAN"
        
        # Show header with profile
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
                handle_manage
                ;;
            3)
                handle_test
                ;;
            4)
                local active_backend=""
                if [[ -n "$_active_profile" ]]; then
                    active_backend=$(profile_get "$_active_profile" "backend")
                fi
                if [[ "$active_backend" == "k8s" ]]; then
                    # K8s Connect/Disconnect toggle
                    local pid_file="${REPO_ROOT}/.k8s-connect.pid"
                    echo ""
                    if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
                        cd "$REPO_ROOT"
                        make disconnect
                    else
                        cd "$REPO_ROOT"
                        make connect
                    fi
                    read -n 1 -s -r -p "Press any key to continue..."
                else
                    handle_build_app
                fi
                ;;
            5)
                local active_backend=""
                if [[ -n "$_active_profile" ]]; then
                    active_backend=$(profile_get "$_active_profile" "backend")
                fi
                if [[ "$active_backend" == "k8s" ]]; then
                    handle_build_app
                else
                    handle_mcp_install
                fi
                ;;
            6)
                # K8s only: Install MCP locally
                local active_backend=""
                if [[ -n "$_active_profile" ]]; then
                    active_backend=$(profile_get "$_active_profile" "backend")
                fi
                if [[ "$active_backend" == "k8s" ]]; then
                    handle_mcp_install
                fi
                ;;
            p|P)
                select_profile
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
