#!/usr/bin/env bash
#
# Busibox Interactive Menu System
#
# Main entry point for the enhanced interactive Makefile.
# Provides state-aware menus, health checks, and quick actions.
#
# Usage:
#   make                    # Interactive menu
#   make ENV=test           # Start with test environment
#   bash scripts/make/menu.sh [environment]
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"
source "${REPO_ROOT}/scripts/lib/health.sh"

# Parse command line arguments
ENV_ARG="${1:-}"

# ============================================================================
# Main Menu Functions
# ============================================================================

# Auto-detect backend for an environment by checking if services are reachable
# Returns: "docker" or "proxmox" if detected, empty string if not
auto_detect_backend() {
    local env="$1"
    
    # Get network base for this environment
    local network_base
    case "$env" in
        production) network_base="10.96.200" ;;
        staging) network_base="10.96.201" ;;
        *) return 1 ;;
    esac
    
    # Check if Proxmox network is reachable (quick ping to gateway)
    if ping -c 1 -W 1 "${network_base}.200" &>/dev/null 2>&1; then
        echo "proxmox"
        return 0
    fi
    
    # Check if Docker containers for this env are running
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qE "(local|${env})" ; then
        echo "docker"
        return 0
    fi
    
    # Not detected
    echo ""
    return 1
}

# Initialize or load environment
initialize_environment() {
    local env backend
    
    # Check if environment was passed as argument
    if [[ -n "$ENV_ARG" ]]; then
        # Normalize environment name
        case "$ENV_ARG" in
            local|docker)
                env="local"
                backend="docker"
                ;;
            staging)
                env="staging"
                # Check if backend is saved
                backend=$(get_backend "staging")
                if [[ -z "$backend" ]]; then
                    # Try to auto-detect
                    backend=$(auto_detect_backend "staging")
                    if [[ -z "$backend" ]]; then
                        # Ask user
                        backend=$(select_backend)
                    else
                        info "Auto-detected backend: $backend"
                    fi
                fi
                ;;
            prod|production)
                env="production"
                backend=$(get_backend "production")
                if [[ -z "$backend" ]]; then
                    # Try to auto-detect
                    backend=$(auto_detect_backend "production")
                    if [[ -z "$backend" ]]; then
                        # Ask user
                        backend=$(select_backend)
                    else
                        info "Auto-detected backend: $backend"
                    fi
                fi
                ;;
            *)
                error "Unknown environment: $ENV_ARG"
                error "Valid options: local, staging, production"
                exit 1
                ;;
        esac
        
        set_environment "$env"
        set_backend "$env" "$backend"
        return 0
    fi
    
    # Check if we have saved state
    env=$(get_environment)
    
    if [[ -n "$env" ]]; then
        backend=$(get_backend "$env")
        info "Using saved environment: $env ($backend)"
        echo ""
        
        # Offer to change
        echo -e "  ${DIM}Press Enter to continue, or 'c' to change environment${NC}"
        local change_choice=""
        read -t 3 -n 1 change_choice 2>/dev/null || true
        
        if [[ "${change_choice:-}" == "c" ]]; then
            select_and_save_environment
        fi
    else
        # First run - need to select environment
        select_and_save_environment
    fi
}

# Select environment and backend, save to state
select_and_save_environment() {
    local result env backend detected_backend
    
    result=$(select_environment_with_backend_autodetect)
    env="${result%%:*}"
    backend="${result#*:}"
    
    set_environment "$env"
    set_backend "$env" "$backend"
    
    success "Environment set to: $env ($backend)"
}

# Run health check and display results
run_and_display_health_check() {
    local env backend
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    echo ""
    info "Running health check..."
    run_health_check "$env" "$backend"
    display_health_check "$env" "$backend"
    
    pause
}

# Show the main menu and get user selection
# Returns: menu choice via stdout
show_main_menu() {
    local env backend status last_cmd last_ago choice
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    status=$(get_install_status)
    last_cmd=$(get_last_command)
    last_ago=$(get_last_command_ago)
    
    # Display everything to stderr so it shows on screen
    {
        clear
        box "Busibox Control Panel" 70
        
        # Status bar
        status_bar "$env" "$backend" "$status" 70
        
        # Quick actions (if we have a last command)
        if [[ -n "$last_cmd" ]]; then
            quick_menu "$last_cmd" "$last_ago"
        fi
    } >&2
    
    # Dynamic menu handles its own stderr output and returns choice to stdout
    choice=$(dynamic_menu "$status" "$last_cmd" "$last_ago")
    
    echo "$choice"
}

# Handle menu selection
handle_menu_selection() {
    local selection="$1"
    local env backend
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    case "$selection" in
        install)
            handle_install
            ;;
        configure)
            handle_configure
            ;;
        services)
            handle_services
            ;;
        deploy)
            handle_deploy
            ;;
        test)
            handle_test
            ;;
        change_env)
            select_and_save_environment
            # Re-run health check for new environment
            env=$(get_environment)
            backend=$(get_backend "$env")
            run_quick_health_check "$env" "$backend"
            ;;
        status)
            run_and_display_health_check
            ;;
        rerun)
            handle_rerun
            ;;
        help)
            show_help
            ;;
        quit)
            info "Exiting..."
            exit 0
            ;;
        *)
            error "Unknown selection: $selection"
            pause
            ;;
    esac
}

# ============================================================================
# Action Handlers
# ============================================================================

# Show detailed requirements checklist
# Displays to stderr, returns "issues:warnings" to stdout
show_requirements_checklist() {
    local env="$1"
    local backend="$2"
    
    local issues=0
    local warnings=0
    
    # All display goes to stderr
    {
    echo ""
    box "System Requirements Check" 70
    echo ""
    
    # ---- Dependencies ----
    echo -e "  ${BOLD}Dependencies:${NC}"
    
    # Python
    if command -v python3 &>/dev/null; then
        local py_version=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
        local py_major=$(echo "$py_version" | cut -d. -f1)
        local py_minor=$(echo "$py_version" | cut -d. -f2)
        if [[ "$py_major" -ge 3 ]] && [[ "$py_minor" -ge 11 ]]; then
            echo -e "    ${GREEN}✓${NC} Python ${py_version} ${DIM}(3.11+ required)${NC}"
        else
            echo -e "    ${YELLOW}○${NC} Python ${py_version} ${DIM}(3.11+ recommended)${NC}"
            ((warnings++))
        fi
    else
        echo -e "    ${RED}✗${NC} Python not found ${DIM}(3.11+ required)${NC}"
        ((issues++))
    fi
    
    # Backend-specific
    case "$backend" in
        docker)
            # Docker
            if command -v docker &>/dev/null; then
                local docker_version=$(docker --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
                if docker info &>/dev/null; then
                    echo -e "    ${GREEN}✓${NC} Docker ${docker_version} ${DIM}(running)${NC}"
                else
                    echo -e "    ${YELLOW}○${NC} Docker ${docker_version} ${DIM}(not running)${NC}"
                    ((warnings++))
                fi
            else
                echo -e "    ${RED}✗${NC} Docker not found"
                ((issues++))
            fi
            
            # Docker Compose
            if docker compose version &>/dev/null; then
                local compose_version=$(docker compose version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
                echo -e "    ${GREEN}✓${NC} Docker Compose ${compose_version}"
            else
                echo -e "    ${RED}✗${NC} Docker Compose not found"
                ((issues++))
            fi
            ;;
            
        proxmox)
            # Ansible
            if command -v ansible &>/dev/null; then
                local ansible_version=$(ansible --version 2>&1 | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
                echo -e "    ${GREEN}✓${NC} Ansible ${ansible_version}"
            else
                echo -e "    ${RED}✗${NC} Ansible not found"
                ((issues++))
            fi
            
            # SSH
            if command -v ssh &>/dev/null; then
                echo -e "    ${GREEN}✓${NC} SSH client"
            else
                echo -e "    ${RED}✗${NC} SSH client not found"
                ((issues++))
            fi
            ;;
    esac
    
    # Optional tools
    if command -v jq &>/dev/null; then
        echo -e "    ${GREEN}✓${NC} jq ${DIM}(optional)${NC}"
    else
        echo -e "    ${DIM}○${NC} jq ${DIM}(optional, not installed)${NC}"
    fi
    
    if command -v curl &>/dev/null; then
        echo -e "    ${GREEN}✓${NC} curl"
    else
        echo -e "    ${RED}✗${NC} curl not found"
        ((issues++))
    fi
    
    echo ""
    
    # ---- Configuration ----
    echo -e "  ${BOLD}Configuration:${NC}"
    
    case "$backend" in
        docker)
            # .env.local
            if [[ -f "${REPO_ROOT}/.env.local" ]]; then
                # Check for required vars
                local missing_vars=0
                for var in OPENAI_API_KEY; do
                    if ! grep -q "^${var}=.\+" "${REPO_ROOT}/.env.local" 2>/dev/null; then
                        ((missing_vars++))
                    fi
                done
                if [[ $missing_vars -eq 0 ]]; then
                    echo -e "    ${GREEN}✓${NC} .env.local ${DIM}(configured)${NC}"
                else
                    echo -e "    ${YELLOW}○${NC} .env.local ${DIM}(missing API keys)${NC}"
                    ((warnings++))
                fi
            elif [[ -f "${REPO_ROOT}/env.local.example" ]]; then
                echo -e "    ${YELLOW}○${NC} .env.local ${DIM}(not created, template available)${NC}"
                ((warnings++))
            else
                echo -e "    ${RED}✗${NC} .env.local missing"
                ((issues++))
            fi
            
            # docker-compose.local.yml
            if [[ -f "${REPO_ROOT}/docker-compose.local.yml" ]]; then
                echo -e "    ${GREEN}✓${NC} docker-compose.local.yml"
            else
                echo -e "    ${RED}✗${NC} docker-compose.local.yml missing"
                ((issues++))
            fi
            
            # SSL certificates
            if [[ -f "${REPO_ROOT}/ssl/localhost.crt" ]] && [[ -f "${REPO_ROOT}/ssl/localhost.key" ]]; then
                # Check expiry
                local expiry=$(openssl x509 -enddate -noout -in "${REPO_ROOT}/ssl/localhost.crt" 2>/dev/null | cut -d= -f2)
                if [[ -n "$expiry" ]]; then
                    echo -e "    ${GREEN}✓${NC} SSL certificates ${DIM}(expires: $expiry)${NC}"
                else
                    echo -e "    ${GREEN}✓${NC} SSL certificates"
                fi
            else
                echo -e "    ${YELLOW}○${NC} SSL certificates ${DIM}(not generated)${NC}"
                ((warnings++))
            fi
            ;;
            
        proxmox)
            # Ansible inventory
            local inv_dir="${REPO_ROOT}/provision/ansible/inventory/${env}"
            if [[ -d "$inv_dir" ]]; then
                echo -e "    ${GREEN}✓${NC} Ansible inventory (${env})"
            else
                echo -e "    ${RED}✗${NC} Ansible inventory missing for ${env}"
                ((issues++))
            fi
            
            # Vault - check inventory-level vault first, then secrets role vault
            local vault_found=0
            local vault_encrypted=0
            
            # Check inventory-level vault (preferred location)
            local inv_vault_file="${REPO_ROOT}/provision/ansible/inventory/${env}/group_vars/all/vault.yml"
            if [[ -f "$inv_vault_file" ]]; then
                vault_found=1
                if head -1 "$inv_vault_file" | grep -q '^\$ANSIBLE_VAULT'; then
                    vault_encrypted=1
                fi
            fi
            
            # Fallback: check secrets role vault (shared between environments)
            local role_vault_file="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.yml"
            if [[ $vault_found -eq 0 ]] && [[ -f "$role_vault_file" ]]; then
                vault_found=1
                if head -1 "$role_vault_file" | grep -q '^\$ANSIBLE_VAULT'; then
                    vault_encrypted=1
                fi
            fi
            
            if [[ $vault_found -eq 1 ]]; then
                if [[ $vault_encrypted -eq 1 ]]; then
                    echo -e "    ${GREEN}✓${NC} Ansible vault ${DIM}(encrypted)${NC}"
                else
                    echo -e "    ${YELLOW}○${NC} Ansible vault ${DIM}(not encrypted)${NC}"
                    ((warnings++))
                fi
            else
                echo -e "    ${RED}✗${NC} Ansible vault missing"
                ((issues++))
            fi
            
            # Vault password
            if [[ -f "$HOME/.vault_pass" ]]; then
                echo -e "    ${GREEN}✓${NC} Vault password file"
            else
                echo -e "    ${YELLOW}○${NC} Vault password file ${DIM}(~/.vault_pass)${NC}"
                ((warnings++))
            fi
            ;;
    esac
    
    echo ""
    separator 70
    echo ""
    
    # Summary
    if [[ $issues -eq 0 ]] && [[ $warnings -eq 0 ]]; then
        echo -e "  ${GREEN}✓ All requirements met${NC}"
    elif [[ $issues -eq 0 ]]; then
        echo -e "  ${YELLOW}○ $warnings warning(s) - system functional but may need attention${NC}"
    else
        echo -e "  ${RED}✗ $issues issue(s) need to be resolved${NC}"
    fi
    echo ""
    } >&2
    
    # Return counts for menu logic (to stdout)
    echo "$issues:$warnings"
}

handle_install() {
    local env backend
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    # Save current status to restore if no changes made
    local original_status=$(get_install_status)
    
    while true; do
        clear
        
        # Show requirements checklist and capture issue counts
        local result
        result=$(show_requirements_checklist "$env" "$backend")
        local issues="${result%%:*}"
        local warnings="${result#*:}"
        
        # Show appropriate menu based on status
        echo ""
        if [[ "$issues" -eq 0 ]]; then
            # All good - show maintenance options
            menu "System Setup - All Requirements Met" \
                "Refresh SSL Certificates" \
                "Rebuild Docker Images" \
                "Update .env.local" \
                "Back to Main Menu"
            
            local choice=""
            read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
            
            case "$choice" in
                1)
                    echo ""
                    info "Regenerating SSL certificates..."
                    bash "${REPO_ROOT}/scripts/setup/generate-local-ssl.sh" || {
                        error "SSL certificate generation failed"
                    }
                    pause
                    ;;
                2)
                    echo ""
                    if confirm "Rebuild all Docker images (this may take a while)?"; then
                        save_last_command "make docker-build"
                        (cd "$REPO_ROOT" && make docker-build)
                    fi
                    pause
                    ;;
                3)
                    echo ""
                    info "Opening .env.local for editing..."
                    if [[ -n "${EDITOR:-}" ]]; then
                        "$EDITOR" "${REPO_ROOT}/.env.local"
                    elif command -v nano &>/dev/null; then
                        nano "${REPO_ROOT}/.env.local"
                    elif command -v vim &>/dev/null; then
                        vim "${REPO_ROOT}/.env.local"
                    else
                        warn "No editor found. Edit manually: ${REPO_ROOT}/.env.local"
                        pause
                    fi
                    ;;
                4|b|B|"")
                    return 0
                    ;;
            esac
        else
            # Issues exist - show fix options
            menu "System Setup - Action Required" \
                "Install/Fix Missing Requirements" \
                "Back to Main Menu"
            
            local choice=""
            read -p "$(echo -e "${BOLD}Select option [1-2]:${NC} ")" choice
            
            case "$choice" in
                1)
                    echo ""
                    run_install_fixes "$env" "$backend"
                    pause
                    ;;
                2|b|B|"")
                    return 0
                    ;;
            esac
        fi
    done
}

# Run installation fixes for missing requirements
run_install_fixes() {
    local env="$1"
    local backend="$2"
    
    header "Installing Requirements" 70
    
    case "$backend" in
        docker)
            # Create .env.local if missing
            if [[ ! -f "${REPO_ROOT}/.env.local" ]]; then
                if [[ -f "${REPO_ROOT}/env.local.example" ]]; then
                    info "Creating .env.local from template..."
                    cp "${REPO_ROOT}/env.local.example" "${REPO_ROOT}/.env.local"
                    success "Created .env.local"
                    warn "Remember to edit .env.local and add your API keys"
                fi
            fi
            
            # Generate SSL certificates if missing
            if [[ ! -f "${REPO_ROOT}/ssl/localhost.crt" ]] || [[ ! -f "${REPO_ROOT}/ssl/localhost.key" ]]; then
                info "Generating SSL certificates..."
                bash "${REPO_ROOT}/scripts/setup/generate-local-ssl.sh" || {
                    error "SSL certificate generation failed"
                }
            fi
            
            # Check Docker
            if ! command -v docker &>/dev/null; then
                echo ""
                warn "Docker is not installed. Please install Docker:"
                echo "  macOS:   brew install --cask docker"
                echo "  Ubuntu:  sudo apt install docker.io docker-compose-plugin"
                echo "  Windows: https://docs.docker.com/desktop/windows/install/"
            elif ! docker info &>/dev/null; then
                echo ""
                warn "Docker is installed but not running. Please start Docker."
            fi
            ;;
            
        proxmox)
            # Check Ansible
            if ! command -v ansible &>/dev/null; then
                echo ""
                warn "Ansible is not installed. Please install:"
                echo "  macOS:   brew install ansible"
                echo "  Ubuntu:  sudo apt install ansible"
            fi
            
            # Check vault password file
            if [[ ! -f "$HOME/.vault_pass" ]]; then
                echo ""
                warn "Vault password file not found."
                echo "Create it with: echo 'your-vault-password' > ~/.vault_pass && chmod 600 ~/.vault_pass"
            fi
            ;;
    esac
    
    echo ""
    success "Installation check complete"
}

handle_configure() {
    # Run the configure script - it handles its own menu
    # Don't change status - configure doesn't change deployment state
    bash "${SCRIPT_DIR}/configure.sh"
}

handle_deploy() {
    local env backend
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    case "$backend" in
        docker)
            while true; do
                clear
                box "Build/Deploy" 70
                echo ""
                
                menu "Docker Build Options" \
                    "Build All Images" \
                    "Build Specific Service" \
                    "Back to Main Menu"
                
                local choice=""
                read -p "$(echo -e "${BOLD}Select option [1-3]:${NC} ")" choice
                
                case "${choice:-}" in
                    1)
                        echo ""
                        if confirm "Build all Docker images (this may take a while)?"; then
                            save_last_command "make docker-build"
                            (cd "$REPO_ROOT" && make docker-build)
                            set_install_status "configured"
                        fi
                        pause
                        ;;
                    2)
                        deploy_select_service
                        ;;
                    3|b|B|"")
                        return 0
                        ;;
                esac
            done
            ;;
            
        proxmox)
            # Use existing deploy script for Ansible deployments
            bash "${SCRIPT_DIR}/deploy.sh"
            ;;
    esac
}

# Select a specific service to build
deploy_select_service() {
    echo ""
    menu "Select Service to Build" \
        "authz-api" \
        "ingest-api" \
        "ingest-worker" \
        "search-api" \
        "agent-api" \
        "litellm" \
        "nginx" \
        "Back"
    
    local choice=""
    read -p "$(echo -e "${BOLD}Select service [1-8]:${NC} ")" choice
    
    local svc=""
    case "${choice:-}" in
        1) svc="authz-api" ;;
        2) svc="ingest-api" ;;
        3) svc="ingest-worker" ;;
        4) svc="search-api" ;;
        5) svc="agent-api" ;;
        6) svc="litellm" ;;
        7) svc="nginx" ;;
        8|b|B|"") return 0 ;;
    esac
    
    if [[ -n "$svc" ]]; then
        echo ""
        if confirm "Build $svc?"; then
            save_last_command "make docker-build SERVICE=$svc"
            (cd "$REPO_ROOT" && make docker-build SERVICE="$svc")
        fi
        pause
    fi
}

handle_services() {
    local env backend
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    case "$backend" in
        docker)
            while true; do
                clear
                box "Service Control" 70
                
                # Show current status
                echo ""
                info "Current service status:"
                (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null) || echo "  (no services running)"
                echo ""
                
                menu "Select Service Group" \
                    "All Services" \
                    "Specific Service (with logs)" \
                    "Data Services (postgres, redis, milvus, minio)" \
                    "API Services (authz, ingest, search, agent, worker)" \
                    "Docker: Start All (docker-up)" \
                    "Docker: Stop All (docker-down)" \
                    "Docker: Restart All (down + up)" \
                    "Docker: Rebuild All (docker-build)" \
                    "Refresh Status" \
                    "Back to Main Menu"
                
                local choice=""
                read -p "$(echo -e "${BOLD}Select option [1-11]:${NC} ")" choice
                
                case "${choice:-}" in
                    1)
                        service_action_menu "all" ""
                        ;;
                    2)
                        service_select_specific
                        ;;
                    3)
                        service_action_menu "data" "postgres redis milvus minio"
                        ;;
                    4)
                        service_action_menu "api" "authz-api ingest-api ingest-worker search-api agent-api"
                        ;;
                    5)
                        echo ""
                        info "Starting all Docker services..."
                        save_last_command "make docker-up"
                        (cd "$REPO_ROOT" && make docker-up)
                        set_install_status "deployed"
                        pause
                        ;;
                    6)
                        echo ""
                        info "Stopping all Docker services..."
                        save_last_command "make docker-down"
                        (cd "$REPO_ROOT" && make docker-down)
                        pause
                        ;;
                    7)
                        echo ""
                        info "Restarting all Docker services..."
                        save_last_command "make docker-restart"
                        (cd "$REPO_ROOT" && make docker-down && make docker-up)
                        set_install_status "deployed"
                        pause
                        ;;
                    8)
                        echo ""
                        if confirm "Rebuild all Docker images? (this may take a while)"; then
                            save_last_command "make docker-build"
                            (cd "$REPO_ROOT" && make docker-build)
                        fi
                        pause
                        ;;
                    9)
                        # Just continue loop to refresh
                        continue
                        ;;
                    10|11|b|B|"")
                        return 0
                        ;;
                esac
            done
            ;;
            
        proxmox)
            echo ""
            info "For Proxmox, use the Deploy menu to manage services via Ansible"
            pause
            ;;
    esac
}

# Show action menu for a service group
# Usage: service_action_menu "group_name" "service1 service2 ..."
service_action_menu() {
    local group_name="$1"
    local services="$2"
    
    # Check if services are running
    local running_count=0
    local total_count=0
    
    if [[ -z "$services" ]]; then
        # All services
        running_count=$(cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml ps -q --status running 2>/dev/null | wc -l | tr -d ' ')
        total_count=$(cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml ps -q 2>/dev/null | wc -l | tr -d ' ')
    else
        # Specific services
        for svc in $services; do
            ((total_count++))
            if (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml ps -q --status running "$svc" 2>/dev/null | grep -q .); then
                ((running_count++))
            fi
        done
    fi
    
    # Capitalize first letter (compatible with older bash)
    local display_name
    display_name="$(echo "${group_name:0:1}" | tr '[:lower:]' '[:upper:]')${group_name:1}"
    
    echo ""
    if [[ $running_count -gt 0 ]]; then
        info "$display_name services: $running_count running"
    else
        info "$display_name services: stopped"
    fi
    echo ""
    
    # Build menu based on state
    local options=()
    local option_keys=()
    
    if [[ $running_count -eq 0 ]]; then
        # Services are stopped - show Start
        options+=("Start"); option_keys+=("start")
    else
        # Services are running - show Restart and Stop
        options+=("Restart"); option_keys+=("restart")
        options+=("Stop"); option_keys+=("stop")
    fi
    options+=("Back"); option_keys+=("back")
    
    # Display menu
    local i=1
    for option in "${options[@]}"; do
        echo -e "    ${CYAN}$i)${NC} $option"
        ((i++))
    done
    echo ""
    
    local action=""
    read -p "$(echo -e "  ${BOLD}Select action [1-${#options[@]}]:${NC} ")" action
    
    local selected_key="${option_keys[$((${action:-1}-1))]}"
    
    case "$selected_key" in
        start)
            echo ""
            if [[ -z "$services" ]]; then
                save_last_command "make docker-up"
                (cd "$REPO_ROOT" && make docker-up)
            else
                (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml --env-file .env.local up -d $services)
            fi
            set_install_status "deployed"
            pause
            ;;
        restart)
            echo ""
            if [[ -z "$services" ]]; then
                save_last_command "make docker-restart"
                (cd "$REPO_ROOT" && make docker-restart)
            else
                (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml --env-file .env.local restart $services)
            fi
            pause
            ;;
        stop)
            echo ""
            if [[ -z "$services" ]]; then
                save_last_command "make docker-down"
                (cd "$REPO_ROOT" && make docker-down)
            else
                (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml --env-file .env.local stop $services)
            fi
            pause
            ;;
        back|"")
            return 0
            ;;
    esac
}

# Select and manage a specific service
service_select_specific() {
    local services=(
        "postgres"
        "redis"
        "milvus"
        "minio"
        "authz-api"
        "ingest-api"
        "ingest-worker"
        "search-api"
        "agent-api"
        "litellm"
        "nginx"
    )
    
    echo ""
    menu "Select Service" \
        "postgres" \
        "redis" \
        "milvus" \
        "minio" \
        "authz-api" \
        "ingest-api" \
        "ingest-worker" \
        "search-api" \
        "agent-api" \
        "litellm" \
        "nginx" \
        "Back"
    
    local choice=""
    read -p "$(echo -e "${BOLD}Select service [1-12]:${NC} ")" choice
    
    case "${choice:-}" in
        1) service_action_menu_with_logs "postgres" "postgres" ;;
        2) service_action_menu_with_logs "redis" "redis" ;;
        3) service_action_menu_with_logs "milvus" "milvus" ;;
        4) service_action_menu_with_logs "minio" "minio" ;;
        5) service_action_menu_with_logs "authz-api" "authz-api" ;;
        6) service_action_menu_with_logs "ingest-api" "ingest-api" ;;
        7) service_action_menu_with_logs "ingest-worker" "ingest-worker" ;;
        8) service_action_menu_with_logs "search-api" "search-api" ;;
        9) service_action_menu_with_logs "agent-api" "agent-api" ;;
        10) service_action_menu_with_logs "litellm" "litellm" ;;
        11) service_action_menu_with_logs "nginx" "nginx" ;;
        12|b|B|"") return 0 ;;
    esac
}

# Show action menu for a specific service with logs option
service_action_menu_with_logs() {
    local group_name="$1"
    local services="$2"
    
    while true; do
        # Check if service is running
        local is_running=0
        if (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml ps -q --status running "$services" 2>/dev/null | grep -q .); then
            is_running=1
        fi
        
        echo ""
        if [[ $is_running -eq 1 ]]; then
            echo -e "  ${GREEN}●${NC} ${BOLD}$group_name${NC} is running"
        else
            echo -e "  ${RED}○${NC} ${BOLD}$group_name${NC} is stopped"
        fi
        echo ""
        
        # Build menu based on state
        if [[ $is_running -eq 0 ]]; then
            menu "$group_name Actions" \
                "Start" \
                "View Logs" \
                "Back"
            
            local action=""
            read -p "$(echo -e "${BOLD}Select action [1-3]:${NC} ")" action
            
            case "${action:-}" in
                1)
                    echo ""
                    (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml --env-file .env.local up -d $services)
                    set_install_status "deployed"
                    pause
                    ;;
                2)
                    view_service_logs "$services"
                    ;;
                3|b|B|"")
                    return 0
                    ;;
            esac
        else
            menu "$group_name Actions" \
                "Restart" \
                "Stop" \
                "View Logs" \
                "Back"
            
            local action=""
            read -p "$(echo -e "${BOLD}Select action [1-4]:${NC} ")" action
            
            case "${action:-}" in
                1)
                    echo ""
                    (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml --env-file .env.local restart $services)
                    pause
                    ;;
                2)
                    echo ""
                    (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml --env-file .env.local stop $services)
                    pause
                    ;;
                3)
                    view_service_logs "$services"
                    ;;
                4|b|B|"")
                    return 0
                    ;;
            esac
        fi
    done
}

# View logs for a specific service
view_service_logs() {
    local service="$1"
    
    echo ""
    info "Showing last 50 lines of logs for $service"
    info "Press 'q' to exit, arrow keys to scroll"
    echo ""
    sleep 1
    
    # Use less for scrollable output, or tail if less isn't available
    if command -v less &>/dev/null; then
        (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml logs --tail=100 "$service" 2>&1) | less -R +G
    else
        (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml logs --tail=50 "$service" 2>&1)
        pause
    fi
}

handle_test() {
    local env backend
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    echo ""
    header "Test" 70
    
    case "$backend" in
        docker)
            echo ""
            menu "Docker Test Options" \
                "Run All Tests" \
                "Test AuthZ Service" \
                "Test Ingest Service" \
                "Test Search Service" \
                "Test Agent Service" \
                "Back to Main Menu"
            
            read -p "$(echo -e "${BOLD}Select option [1-6]:${NC} ")" test_choice
            
            local cmd=""
            case "$test_choice" in
                1)
                    cmd="make test-docker SERVICE=all"
                    save_last_command "$cmd"
                    (cd "$REPO_ROOT" && make test-docker SERVICE=all)
                    ;;
                2)
                    cmd="make test-docker SERVICE=authz"
                    save_last_command "$cmd"
                    (cd "$REPO_ROOT" && make test-docker SERVICE=authz)
                    ;;
                3)
                    cmd="make test-docker SERVICE=ingest"
                    save_last_command "$cmd"
                    (cd "$REPO_ROOT" && make test-docker SERVICE=ingest)
                    ;;
                4)
                    cmd="make test-docker SERVICE=search"
                    save_last_command "$cmd"
                    (cd "$REPO_ROOT" && make test-docker SERVICE=search)
                    ;;
                5)
                    cmd="make test-docker SERVICE=agent"
                    save_last_command "$cmd"
                    (cd "$REPO_ROOT" && make test-docker SERVICE=agent)
                    ;;
                6)
                    return 0
                    ;;
            esac
            ;;
            
        proxmox)
            # Map environment to inventory name
            local inv="$env"
            if [[ "$env" == "local" ]]; then
                inv="docker"
            fi
            
            # Use existing test script
            bash "${SCRIPT_DIR}/test.sh"
            ;;
    esac
    
    pause
}

handle_rerun() {
    local last_cmd
    last_cmd=$(get_last_command)
    
    if [[ -z "$last_cmd" ]]; then
        warn "No previous command to re-run"
        pause
        return 0
    fi
    
    echo ""
    info "Re-running: $last_cmd"
    echo ""
    
    # Execute in repo root
    (cd "$REPO_ROOT" && eval "$last_cmd")
    
    pause
}

show_help() {
    clear
    box "Busibox Help" 70
    
    echo ""
    echo -e "${BOLD}Overview${NC}"
    echo "  Busibox is a multi-service AI platform with document ingestion,"
    echo "  vector search, and agent capabilities."
    echo ""
    
    echo -e "${BOLD}Environments${NC}"
    echo "  - Local:      Docker on your machine (development)"
    echo "  - Staging:    10.96.201.x network (pre-production)"
    echo "  - Production: 10.96.200.x network (live)"
    echo ""
    
    echo -e "${BOLD}Backends${NC}"
    echo "  - Docker:  Runs services in Docker containers"
    echo "  - Proxmox: Runs services in LXC containers with GPU support"
    echo ""
    
    separator 70
    echo ""
    echo -e "${BOLD}Docker Commands (make targets)${NC}"
    echo ""
    echo "  ${CYAN}Build:${NC}"
    echo "    make docker-build              # Build all images"
    echo "    make docker-build SERVICE=X   # Build specific service"
    echo ""
    echo "  ${CYAN}Services:${NC}"
    echo "    make docker-up                 # Start all services"
    echo "    make docker-down               # Stop all services"
    echo "    make docker-restart            # Restart all services"
    echo "    make docker-ps                 # Show service status"
    echo "    make docker-logs               # View all logs"
    echo "    make docker-logs SERVICE=X    # View specific logs"
    echo ""
    echo "  ${CYAN}Testing:${NC}"
    echo "    make test-docker SERVICE=all   # Run all tests"
    echo "    make test-docker SERVICE=agent # Run agent tests"
    echo ""
    echo "  ${CYAN}Cleanup:${NC}"
    echo "    make docker-clean              # Remove containers & volumes"
    echo ""
    
    separator 70
    echo ""
    echo -e "${BOLD}State File${NC}"
    echo "  Preferences saved to: .busibox-state"
    echo "  Tracks: environment, backend, last command"
    echo ""
    
    pause
}

# ============================================================================
# Main Loop
# ============================================================================

main() {
    # Initialize state file
    init_state
    
    # Initialize or load environment
    initialize_environment
    
    # Get current environment for health check
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    # Run quick initial health check with progress indicator
    echo -ne "  ${DIM}Checking system status...${NC} "
    run_quick_health_check "$env" "$backend"
    echo -e "${GREEN}done${NC}"
    sleep 0.3
    
    # Main menu loop
    while true; do
        local selection
        selection=$(show_main_menu)
        
        handle_menu_selection "$selection"
    done
}

# Run main function
main "$@"
