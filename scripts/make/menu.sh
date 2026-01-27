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

# Export host path for deploy-api to use when spawning containers
export BUSIBOX_HOST_PATH="${REPO_ROOT}"

# Return code that signals "return to main status dashboard"
# Used by 's' command from any submenu
export RETURN_TO_STATUS=42

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"
source "${REPO_ROOT}/scripts/lib/health.sh"
source "${REPO_ROOT}/scripts/lib/services.sh"
source "${REPO_ROOT}/scripts/lib/status.sh"
source "${REPO_ROOT}/scripts/lib/github.sh"

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
            dev|development|local|docker)
                env="development"
                backend="docker"
                ;;
            demo)
                env="demo"
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
                error "Valid options: development, demo, staging, production"
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

# Handle status refresh (triggered by 's' key)
handle_status_refresh() {
    local env backend
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    # Clear old cache
    rm -rf ~/.busibox/status-cache/* 2>/dev/null
    
    # Kick off background refresh (synchronously for immediate feedback)
    refresh_all_services_async "$env" "$backend"
    
    # Wait for checks to complete
    sleep 3
    
    # Menu will automatically redisplay with fresh data
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
        
        # NEW: Render status dashboard (reads from cache, never blocks)
        render_status_dashboard "$env" "$backend"
        
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
# Returns: exit code from submenu (including $RETURN_TO_STATUS for 's' key)
handle_menu_selection() {
    local selection="$1"
    local env backend result=0
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    case "$selection" in
        start_docker)
            handle_start_docker
            ;;
        start_busibox)
            handle_start_busibox
            ;;
        install)
            handle_install
            ;;
        configure)
            handle_configure
            result=$?
            ;;
        services)
            handle_services
            result=$?
            ;;
        test)
            handle_test
            result=$?
            ;;
        migration|databases)
            handle_databases
            result=$?
            ;;
        change_env)
            select_and_save_environment
            # Re-run health check for new environment
            env=$(get_environment)
            backend=$(get_backend "$env")
            run_quick_health_check "$env" "$backend"
            ;;
        status)
            handle_status_refresh
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
    
    return $result
}

# ============================================================================
# Helper Functions
# ============================================================================

# Get Docker container prefix
# Priority: 1) Environment variable 2) Detect from running containers 3) .env files 4) Default
# Usage: prefix=$(get_container_prefix)
get_container_prefix() {
    local prefix
    
    # 1. Check environment variable (set by Makefile)
    if [[ -n "${CONTAINER_PREFIX:-}" ]]; then
        echo "$CONTAINER_PREFIX"
        return 0
    fi
    
    # 2. Detect from running containers (look for *-authz-api pattern)
    prefix=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '.-authz-api$' | sed 's/-authz-api$//' | head -1)
    if [[ -n "$prefix" ]]; then
        echo "$prefix"
        return 0
    fi
    
    # 3. Check .env files based on current environment
    local env_name
    env_name=$(get_environment 2>/dev/null || echo "")
    
    case "$env_name" in
        development|local|docker)
            # Check .env.dev first, then .env.local
            prefix=$(grep -E "^CONTAINER_PREFIX=" "${REPO_ROOT}/.env.dev" 2>/dev/null | cut -d'=' -f2 || echo "")
            if [[ -z "$prefix" ]]; then
                prefix=$(grep -E "^CONTAINER_PREFIX=" "${REPO_ROOT}/.env.local" 2>/dev/null | cut -d'=' -f2 || echo "")
            fi
            # Default for development is 'dev'
            echo "${prefix:-dev}"
            ;;
        demo)
            prefix=$(grep -E "^CONTAINER_PREFIX=" "${REPO_ROOT}/.env.demo" 2>/dev/null | cut -d'=' -f2 || echo "")
            echo "${prefix:-demo}"
            ;;
        staging)
            echo "staging"
            ;;
        production)
            echo "prod"
            ;;
        *)
            # Fallback: try to detect from any running container
            prefix=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '.-api$' | sed 's/-[^-]*-api$//' | head -1)
            echo "${prefix:-local}"
            ;;
    esac
}

# Get current embedding model from .env.local or default
# Usage: model=$(get_current_embedding_model)
get_current_embedding_model() {
    local model
    model=$(grep -E "^FASTEMBED_MODEL=" "${REPO_ROOT}/.env.local" 2>/dev/null | cut -d'=' -f2 || echo "")
    echo "${model:-BAAI/bge-large-en-v1.5}"
}

# Get embedding dimension for a model
# Usage: dim=$(get_embedding_dimension "BAAI/bge-small-en-v1.5")
get_embedding_dimension() {
    local model="$1"
    case "$model" in
        "BAAI/bge-small-en-v1.5"|"bge-small-en-v1.5")
            echo "384"
            ;;
        "BAAI/bge-base-en-v1.5"|"bge-base-en-v1.5")
            echo "768"
            ;;
        "BAAI/bge-large-en-v1.5"|"bge-large-en-v1.5")
            echo "1024"
            ;;
        *)
            echo "1024"  # Default
            ;;
    esac
}

# ============================================================================
# Action Handlers
# ============================================================================

# Handle starting Docker daemon
handle_start_docker() {
    echo ""
    info "Docker is not running."
    echo ""
    
    # Detect OS and provide appropriate instructions
    if [[ "$(uname)" == "Darwin" ]]; then
        # macOS
        echo -e "  To start Docker on macOS:"
        echo -e "    1. Open Docker Desktop from Applications"
        echo -e "    2. Or run: ${CYAN}open -a Docker${NC}"
        echo ""
        
        if confirm "Try to open Docker Desktop now?"; then
            open -a Docker 2>/dev/null || {
                error "Could not open Docker Desktop"
                echo "Please install Docker Desktop from: https://docs.docker.com/desktop/install/mac-install/"
            }
            echo ""
            info "Waiting for Docker to start..."
            echo -e "  ${DIM}(this may take 30-60 seconds)${NC}"
            echo ""
            
            # Wait for Docker to be ready (max 90 seconds)
            local waited=0
            while [[ $waited -lt 90 ]]; do
                if docker info &>/dev/null 2>&1; then
                    success "Docker is now running!"
                    # Re-run health check
                    local env backend
                    env=$(get_environment)
                    backend=$(get_backend "$env")
                    run_quick_health_check "$env" "$backend"
                    pause
                    return 0
                fi
                sleep 3
                ((waited+=3))
                echo -ne "\r  ${DIM}Waiting... ${waited}s${NC}   "
            done
            echo ""
            warn "Docker did not start within 90 seconds."
            echo "Please wait for Docker Desktop to fully start, then try again."
        fi
    elif [[ "$(uname)" == "Linux" ]]; then
        # Linux
        echo -e "  To start Docker on Linux:"
        echo -e "    ${CYAN}sudo systemctl start docker${NC}"
        echo ""
        
        if confirm "Try to start Docker service now? (requires sudo)"; then
            if sudo systemctl start docker 2>/dev/null; then
                success "Docker service started!"
                # Re-run health check
                local env backend
                env=$(get_environment)
                backend=$(get_backend "$env")
                run_quick_health_check "$env" "$backend"
            else
                error "Failed to start Docker. Please check your Docker installation."
            fi
        fi
    else
        echo -e "  Please start Docker Desktop or the Docker service manually."
    fi
    
    pause
}

# Handle starting Busibox containers
handle_start_busibox() {
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    echo ""
    info "Starting Busibox services (ENV=$env)..."
    echo ""
    
    # Use docker-start (--no-build) to start existing containers quickly
    # If images don't exist, use docker-up which will build them
    # Pass ENV to select the correct compose overlay
    save_last_command "make docker-start ENV=$env"
    (cd "$REPO_ROOT" && make docker-start ENV="$env")
    
    # Re-run health check to update status
    run_quick_health_check "$env" "$backend"
    
    if [[ "$HEALTH_STATUS" == "deployed" || "$HEALTH_STATUS" == "healthy" ]]; then
        set_install_status "deployed"
        success "Busibox services started!"
    else
        warn "Some services may not have started correctly."
        echo "Run a full status check to see details."
    fi
    
    pause
}

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
            
            # docker-compose.yml
            if [[ -f "${REPO_ROOT}/docker-compose.yml" ]]; then
                echo -e "    ${GREEN}✓${NC} docker-compose.yml"
            else
                echo -e "    ${RED}✗${NC} docker-compose.yml missing"
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
                "Edit .env.local" \
                "Regenerate .env.local from Vault" \
                "Back to Main Menu"
            
            local choice=""
            read -p "$(echo -e "${BOLD}Select option [1-5]:${NC} ")" choice
            
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
                4)
                    echo ""
                    info "Regenerating .env.local from Ansible vault..."
                    save_last_command "make vault-generate-env"
                    (cd "$REPO_ROOT" && make vault-generate-env)
                    pause
                    ;;
                5|b|B|"")
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

# DEPRECATED: Old deploy function - functionality moved to handle_services
# This function is kept for backward compatibility but redirects to services
handle_deploy() {
    warn "The 'deploy' option has been merged into 'Services'"
    info "Redirecting to Services menu..."
    sleep 2
    handle_services
}

# Host-native service menu (for MLX, host-agent - services that run on host, not in Docker)
# Usage: host_service_menu "mlx" or host_service_menu "host-agent"
host_service_menu() {
    local service="$1"
    local env_file="${REPO_ROOT}/.env.dev"
    
    while true; do
        clear
        box "$service - Host Service Actions" 70
        
        # Show service status
        echo ""
        info "Service status:"
        case "$service" in
            mlx)
                if curl -sf http://localhost:8080/health >/dev/null 2>&1; then
                    echo -e "  ${GREEN}●${NC} MLX Server: Running (port 8080)"
                    local models
                    models=$(curl -sf http://localhost:8080/v1/models 2>/dev/null | jq -r '.data[].id' 2>/dev/null | head -3)
                    if [[ -n "$models" ]]; then
                        echo -e "    ${DIM}Models: $(echo "$models" | tr '\n' ', ' | sed 's/, $//')${NC}"
                    fi
                else
                    echo -e "  ${DIM}○${NC} MLX Server: Not running"
                fi
                ;;
            host-agent)
                if curl -sf http://localhost:8089/health >/dev/null 2>&1; then
                    echo -e "  ${GREEN}●${NC} Host Agent: Running (port 8089)"
                    # Check MLX status via host-agent
                    local mlx_status
                    mlx_status=$(curl -sf http://localhost:8089/mlx/status 2>/dev/null)
                    if [[ -n "$mlx_status" ]]; then
                        local mlx_running
                        mlx_running=$(echo "$mlx_status" | jq -r '.running // false' 2>/dev/null)
                        if [[ "$mlx_running" == "true" ]]; then
                            echo -e "    ${DIM}MLX Status: Running${NC}"
                        else
                            echo -e "    ${DIM}MLX Status: Not running${NC}"
                        fi
                    fi
                else
                    echo -e "  ${DIM}○${NC} Host Agent: Not running"
                fi
                ;;
        esac
        
        echo ""
        menu "Actions" \
            "Start" \
            "Stop" \
            "Restart" \
            "View Logs" \
            "Check Status" \
            "Back"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select action [1-6]:${NC} ")" choice
        
        # Get token for host-agent authentication
        local token=""
        if [[ -f "$env_file" ]]; then
            token=$(grep -s '^HOST_AGENT_TOKEN=' "$env_file" 2>/dev/null | cut -d= -f2)
        fi
        
        case "${choice:-}" in
            1)  # Start
                info "Starting $service..."
                case "$service" in
                    mlx)
                        if curl -sf http://localhost:8089/health >/dev/null 2>&1; then
                            # Use host-agent to start MLX
                            if [[ -n "$token" ]]; then
                                curl -sf -X POST http://localhost:8089/mlx/start \
                                    -H "Content-Type: application/json" \
                                    -H "Authorization: Bearer $token" \
                                    -d '{"model_type": "agent"}' && success "MLX started" || error "Failed to start MLX"
                            else
                                curl -sf -X POST http://localhost:8089/mlx/start \
                                    -H "Content-Type: application/json" \
                                    -d '{"model_type": "agent"}' || error "Failed to start MLX (may need auth)"
                            fi
                        else
                            warn "Host-agent not running. Starting MLX directly..."
                            bash "${REPO_ROOT}/scripts/llm/start-mlx-server.sh" || error "Failed to start MLX"
                        fi
                        ;;
                    host-agent)
                        if curl -sf http://localhost:8089/health >/dev/null 2>&1; then
                            warn "Host-agent is already running"
                        else
                            bash "${REPO_ROOT}/scripts/host-agent/install-host-agent.sh"
                            sleep 2
                            if curl -sf http://localhost:8089/health >/dev/null 2>&1; then
                                success "Host-agent started"
                            else
                                error "Failed to start host-agent"
                            fi
                        fi
                        ;;
                esac
                pause
                ;;
            2)  # Stop
                info "Stopping $service..."
                case "$service" in
                    mlx)
                        if curl -sf http://localhost:8089/health >/dev/null 2>&1; then
                            if [[ -n "$token" ]]; then
                                curl -sf -X POST http://localhost:8089/mlx/stop \
                                    -H "Authorization: Bearer $token" && success "MLX stopped" || error "Failed to stop MLX"
                            else
                                curl -sf -X POST http://localhost:8089/mlx/stop || error "Failed to stop MLX (may need auth)"
                            fi
                        else
                            pkill -f "mlx_lm.server" 2>/dev/null && success "MLX stopped" || warn "MLX not running"
                        fi
                        ;;
                    host-agent)
                        pkill -f "host-agent.py" 2>/dev/null && success "Host-agent stopped" || warn "Host-agent not running"
                        ;;
                esac
                pause
                ;;
            3)  # Restart
                info "Restarting $service..."
                case "$service" in
                    mlx)
                        # Stop first
                        if curl -sf http://localhost:8089/health >/dev/null 2>&1; then
                            if [[ -n "$token" ]]; then
                                curl -sf -X POST http://localhost:8089/mlx/stop -H "Authorization: Bearer $token" || true
                            else
                                curl -sf -X POST http://localhost:8089/mlx/stop || true
                            fi
                        else
                            pkill -f "mlx_lm.server" 2>/dev/null || true
                        fi
                        sleep 2
                        # Start
                        if curl -sf http://localhost:8089/health >/dev/null 2>&1; then
                            if [[ -n "$token" ]]; then
                                curl -sf -X POST http://localhost:8089/mlx/start \
                                    -H "Content-Type: application/json" \
                                    -H "Authorization: Bearer $token" \
                                    -d '{"model_type": "agent"}' && success "MLX restarted" || error "Failed to restart MLX"
                            else
                                curl -sf -X POST http://localhost:8089/mlx/start \
                                    -H "Content-Type: application/json" \
                                    -d '{"model_type": "agent"}' || error "Failed to restart MLX"
                            fi
                        else
                            bash "${REPO_ROOT}/scripts/llm/start-mlx-server.sh" && success "MLX restarted" || error "Failed to restart MLX"
                        fi
                        ;;
                    host-agent)
                        pkill -f "host-agent.py" 2>/dev/null || true
                        sleep 1
                        bash "${REPO_ROOT}/scripts/host-agent/install-host-agent.sh"
                        sleep 2
                        if curl -sf http://localhost:8089/health >/dev/null 2>&1; then
                            success "Host-agent restarted"
                        else
                            error "Failed to restart host-agent"
                        fi
                        ;;
                esac
                pause
                ;;
            4)  # View Logs
                info "Logs for $service:"
                case "$service" in
                    mlx)
                        # MLX logs would be in the terminal where it was started or via host-agent
                        if [[ -f "$HOME/.busibox/mlx-server.log" ]]; then
                            tail -50 "$HOME/.busibox/mlx-server.log"
                        else
                            warn "No MLX log file found. MLX logs appear in the terminal where it was started."
                        fi
                        ;;
                    host-agent)
                        if [[ -f "$HOME/.busibox/host-agent.log" ]]; then
                            tail -50 "$HOME/.busibox/host-agent.log"
                        else
                            warn "No host-agent log file found."
                        fi
                        ;;
                esac
                pause
                ;;
            5)  # Check Status
                info "Detailed status for $service:"
                case "$service" in
                    mlx)
                        echo ""
                        if curl -sf http://localhost:8080/health >/dev/null 2>&1; then
                            echo -e "${GREEN}MLX Server: Running${NC}"
                            echo ""
                            echo "Available models:"
                            curl -sf http://localhost:8080/v1/models 2>/dev/null | jq '.data[].id' 2>/dev/null || echo "  (unable to fetch models)"
                        else
                            echo -e "${RED}MLX Server: Not running${NC}"
                        fi
                        ;;
                    host-agent)
                        echo ""
                        if curl -sf http://localhost:8089/health >/dev/null 2>&1; then
                            echo -e "${GREEN}Host Agent: Running${NC}"
                            echo ""
                            echo "Health check:"
                            curl -sf http://localhost:8089/health 2>/dev/null | jq . 2>/dev/null || curl -sf http://localhost:8089/health 2>/dev/null
                            echo ""
                            echo "MLX Status:"
                            curl -sf http://localhost:8089/mlx/status 2>/dev/null | jq . 2>/dev/null || echo "  (unavailable)"
                        else
                            echo -e "${RED}Host Agent: Not running${NC}"
                        fi
                        ;;
                esac
                pause
                ;;
            6|b|B|"")
                return 0
                ;;
        esac
    done
}

# Service group action menu with deployment/build options
# Usage: services_group_menu "Group Name" "service1 service2..."
services_group_menu() {
    local group_name="$1"
    local services="$2"
    local env
    env=$(get_environment)
    
    while true; do
        clear
        box "$group_name - Actions" 70
        
        # Show service status
        echo ""
        info "Service status:"
        if [[ -z "$services" ]]; then
            (cd "$REPO_ROOT" && docker compose -f docker-compose.yml ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null) || echo "  (no services running)"
        else
            for svc in $services; do
                local status_line
                # Special handling for ai-portal and agent-manager (run in core-apps container)
                case "$svc" in
                    ai-portal)
                        # Check if core-apps is running and port 3000 is listening
                        local core_status
                        core_status=$(cd "$REPO_ROOT" && docker compose -f docker-compose.yml ps --format "table {{.Name}}\t{{.Status}}" "core-apps" 2>/dev/null | tail -n +2)
                        if [[ -n "$core_status" ]] && lsof -i :3000 -sTCP:LISTEN -t >/dev/null 2>&1; then
                            echo "  ai-portal: running (in core-apps)"
                        else
                            echo "  ai-portal: not running"
                        fi
                        ;;
                    agent-manager)
                        # Check if core-apps is running and port 3001 is listening
                        local core_status
                        core_status=$(cd "$REPO_ROOT" && docker compose -f docker-compose.yml ps --format "table {{.Name}}\t{{.Status}}" "core-apps" 2>/dev/null | tail -n +2)
                        if [[ -n "$core_status" ]] && lsof -i :3001 -sTCP:LISTEN -t >/dev/null 2>&1; then
                            echo "  agent-manager: running (in core-apps)"
                        else
                            echo "  agent-manager: not running"
                        fi
                        ;;
                    *)
                        status_line=$(cd "$REPO_ROOT" && docker compose -f docker-compose.yml ps --format "table {{.Name}}\t{{.Status}}" "$svc" 2>/dev/null | tail -n +2)
                        if [[ -n "$status_line" ]]; then
                            echo "  $status_line"
                        else
                            echo "  $svc: not running"
                        fi
                        ;;
                esac
            done
        fi
        echo ""
        
        menu "Select Action" \
            "Start" \
            "Stop" \
            "Restart" \
            "Rebuild (docker build)" \
            "Rebuild (no cache)" \
            "View Logs" \
            "Back"
        echo -e "  ${DIM}Press 's' to refresh status and return to main menu${NC}"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select action [1-7]:${NC} ")" choice
        
        case "${choice:-}" in
            1)
                services_start_with_deps "$group_name" "$services"
                ;;
            2)
                services_stop_group "$group_name" "$services"
                ;;
            3)
                services_restart_with_deps "$group_name" "$services"
                ;;
            4)
                services_rebuild_group "$group_name" "$services" ""
                ;;
            5)
                services_rebuild_group "$group_name" "$services" "no-cache"
                ;;
            6)
                services_view_logs "$group_name" "$services"
                ;;
            7|b|B|"")
                return 0
                ;;
            s|S)
                return $RETURN_TO_STATUS
                ;;
        esac
    done
}

# Start services with dependency order
# Usage: services_start_with_deps "Group Name" "service1 service2..."
services_start_with_deps() {
    local group_name="$1"
    local services="$2"
    local env
    env=$(get_environment)
    
    echo ""
    if [[ -z "$services" ]]; then
        info "Starting all services with dependencies..."
        save_last_command "make docker-up ENV=$env"
        (cd "$REPO_ROOT" && make docker-up ENV="$env")
    else
        # Translate logical service names to docker-compose service names
        local docker_services
        docker_services=$(translate_service_names "$services")
        
        # Source service definitions to get categories
        source "${SCRIPT_DIR}/../lib/services.sh"
        
        # Determine dependency order
        local core_svcs=""
        local api_svcs=""
        local app_svcs=""
        
        for svc in $docker_services; do
            # Normalize service name (remove -api, -worker suffixes for matching)
            local svc_normalized="${svc%%-api}"
            svc_normalized="${svc_normalized%%-worker}"
            
            if echo "$_CORE_SERVICES" | grep -qw "$svc_normalized" || echo "$_CORE_SERVICES" | grep -qw "$svc"; then
                core_svcs="$core_svcs $svc"
            elif echo "$_API_SERVICES" | grep -qw "$svc_normalized" || echo "$_API_SERVICES" | grep -qw "$svc" || [[ "$svc" == "ingest-worker" ]] || [[ "$svc" == "embedding-api" ]]; then
                api_svcs="$api_svcs $svc"
            elif echo "$_APP_SERVICES" | grep -qw "$svc_normalized" || echo "$_APP_SERVICES" | grep -qw "$svc" || [[ "$svc" == "core-apps" ]]; then
                app_svcs="$app_svcs $svc"
            else
                # Default to API services if unknown
                api_svcs="$api_svcs $svc"
            fi
        done
        
        # Start in dependency order: core -> api -> apps
        info "Starting services in dependency order..."
        
        if [[ -n "$core_svcs" ]]; then
            info "Starting core services:$core_svcs"
            (cd "$REPO_ROOT" && BUSIBOX_HOST_PATH="$REPO_ROOT" docker compose -f docker-compose.yml up -d --no-deps $core_svcs)
            sleep 2
        fi
        
        if [[ -n "$api_svcs" ]]; then
            info "Starting API services:$api_svcs"
            (cd "$REPO_ROOT" && BUSIBOX_HOST_PATH="$REPO_ROOT" docker compose -f docker-compose.yml up -d --no-deps $api_svcs)
            sleep 2
        fi
        
        if [[ -n "$app_svcs" ]]; then
            info "Starting app services:$app_svcs"
            (cd "$REPO_ROOT" && BUSIBOX_HOST_PATH="$REPO_ROOT" docker compose -f docker-compose.yml up -d --no-deps $app_svcs)
        fi
        
        success "$group_name started"
    fi
    set_install_status "deployed"
    pause
}

# Stop services (reverse dependency order)
# Usage: services_stop_group "Group Name" "service1 service2..."
services_stop_group() {
    local group_name="$1"
    local services="$2"
    
    echo ""
    if ! confirm "Stop $group_name?"; then
        return 0
    fi
    
    if [[ -z "$services" ]]; then
        info "Stopping all services..."
        save_last_command "make docker-down"
        (cd "$REPO_ROOT" && make docker-down)
    else
        # Translate logical service names to docker-compose service names
        local docker_services
        docker_services=$(translate_service_names "$services")
        
        # Stop in reverse order: apps -> api -> core
        source "${SCRIPT_DIR}/../lib/services.sh"
        
        local core_svcs=""
        local api_svcs=""
        local app_svcs=""
        
        for svc in $docker_services; do
            # Normalize service name (remove -api, -worker suffixes for matching)
            local svc_normalized="${svc%%-api}"
            svc_normalized="${svc_normalized%%-worker}"
            
            if echo "$_CORE_SERVICES" | grep -qw "$svc_normalized" || echo "$_CORE_SERVICES" | grep -qw "$svc"; then
                core_svcs="$core_svcs $svc"
            elif echo "$_API_SERVICES" | grep -qw "$svc_normalized" || echo "$_API_SERVICES" | grep -qw "$svc" || [[ "$svc" == "ingest-worker" ]] || [[ "$svc" == "embedding-api" ]]; then
                api_svcs="$api_svcs $svc"
            elif echo "$_APP_SERVICES" | grep -qw "$svc_normalized" || echo "$_APP_SERVICES" | grep -qw "$svc" || [[ "$svc" == "core-apps" ]]; then
                app_svcs="$app_svcs $svc"
            else
                # Default to API services if unknown
                api_svcs="$api_svcs $svc"
            fi
        done
        
        # Stop in reverse order
        if [[ -n "$app_svcs" ]]; then
            info "Stopping app services:$app_svcs"
            (cd "$REPO_ROOT" && docker compose -f docker-compose.yml stop $app_svcs)
        fi
        
        if [[ -n "$api_svcs" ]]; then
            info "Stopping API services:$api_svcs"
            (cd "$REPO_ROOT" && docker compose -f docker-compose.yml stop $api_svcs)
        fi
        
        if [[ -n "$core_svcs" ]]; then
            info "Stopping core services:$core_svcs"
            (cd "$REPO_ROOT" && docker compose -f docker-compose.yml stop $core_svcs)
        fi
        
        success "$group_name stopped"
    fi
    pause
}

# Restart services with dependency order
# Usage: services_restart_with_deps "Group Name" "service1 service2..."
services_restart_with_deps() {
    local group_name="$1"
    local services="$2"
    local env
    env=$(get_environment)
    
    echo ""
    if [[ -z "$services" ]]; then
        info "Restarting all services..."
        save_last_command "make docker-restart ENV=$env"
        (cd "$REPO_ROOT" && make docker-down && make docker-up ENV="$env")
    else
        # Translate logical service names to docker-compose service names
        local docker_services
        docker_services=$(translate_service_names "$services")
        
        info "Restarting $group_name..."
        (cd "$REPO_ROOT" && docker compose -f docker-compose.yml restart $docker_services)
        success "$group_name restarted"
    fi
    set_install_status "deployed"
    pause
}

# Translate logical service names to docker-compose service names
# In local dev, ai-portal and agent-manager run in a single core-apps container
# Usage: translate_service_names "ai-portal agent-manager nginx"
# Returns: "core-apps nginx" (deduplicated)
translate_service_names() {
    local services="$1"
    local translated=""
    local seen_core_apps=false
    
    for svc in $services; do
        case "$svc" in
            ai-portal|agent-manager)
                # Both run in core-apps container in local dev
                if [[ "$seen_core_apps" == "false" ]]; then
                    translated="$translated core-apps"
                    seen_core_apps=true
                fi
                ;;
            *)
                translated="$translated $svc"
                ;;
        esac
    done
    
    # Trim leading/trailing whitespace
    echo "$translated" | xargs
}

# Rebuild service images
# Usage: services_rebuild_group "Group Name" "service1 service2..." ["no-cache"]
services_rebuild_group() {
    local group_name="$1"
    local services="$2"
    local no_cache="$3"
    local env
    env=$(get_environment)
    
    local cache_msg=""
    if [[ "$no_cache" == "no-cache" ]]; then
        cache_msg=" (NO CACHE)"
    fi
    
    echo ""
    if ! confirm "Rebuild $group_name$cache_msg?"; then
        return 0
    fi
    
    if [[ -z "$services" ]]; then
        # Build all images
        info "Building all Docker images..."
        if [[ "$no_cache" == "no-cache" ]]; then
            save_last_command "make docker-build ENV=$env NO_CACHE=1"
            (cd "$REPO_ROOT" && make docker-build ENV="$env" NO_CACHE=1)
        else
            save_last_command "make docker-build ENV=$env"
            (cd "$REPO_ROOT" && make docker-build ENV="$env")
        fi
    else
        # Translate logical service names to docker-compose service names
        local docker_services
        docker_services=$(translate_service_names "$services")
        
        # Build specific services
        info "Building services: $docker_services"
        for svc in $docker_services; do
            info "Building $svc..."
            if [[ "$no_cache" == "no-cache" ]]; then
                (cd "$REPO_ROOT" && make docker-build SERVICE="$svc" ENV="$env" NO_CACHE=1)
            else
                (cd "$REPO_ROOT" && make docker-build SERVICE="$svc" ENV="$env")
            fi
        done
        success "$group_name rebuilt"
    fi
    
    # Refresh status cache after build
    refresh_all_services_async "$env" "$backend" &
    pause
}

# View logs for service group
# Usage: services_view_logs "Group Name" "service1 service2..."
services_view_logs() {
    local group_name="$1"
    local services="$2"
    
    echo ""
    if [[ -z "$services" ]]; then
        info "Showing logs for all services (last 50 lines)"
        info "Press Ctrl+C to exit"
        echo ""
        sleep 1
        (cd "$REPO_ROOT" && docker compose -f docker-compose.yml logs --tail=50 --follow)
    else
        # Translate logical service names to docker-compose service names
        local docker_services
        docker_services=$(translate_service_names "$services")
        
        info "Showing logs for: $docker_services (last 50 lines)"
        info "Press 'q' to exit, arrow keys to scroll"
        echo ""
        sleep 1
        if command -v less &>/dev/null; then
            (cd "$REPO_ROOT" && docker compose -f docker-compose.yml logs --tail=100 $docker_services 2>&1) | less -R +G
        else
            (cd "$REPO_ROOT" && docker compose -f docker-compose.yml logs --tail=50 $docker_services 2>&1)
            pause
        fi
    fi
}

# Select specific service for management
# Usage: services_select_specific
services_select_specific() {
    # Detect platform for MLX/vLLM display
    local os arch llm_backend_label=""
    os=$(uname -s)
    arch=$(uname -m)
    if [[ "$os" == "Darwin" && ("$arch" == "arm64" || "$arch" == "aarch64") ]]; then
        llm_backend_label="mlx (host)"
    elif command -v nvidia-smi &>/dev/null && [[ $(nvidia-smi -L 2>/dev/null | wc -l) -gt 0 ]]; then
        llm_backend_label="vllm"
    fi
    
    echo ""
    if [[ -n "$llm_backend_label" ]]; then
        menu "Select Service" \
            "authz-api" \
            "postgres" \
            "redis" \
            "milvus" \
            "minio" \
            "nginx" \
            "litellm" \
            "$llm_backend_label" \
            "embedding-api" \
            "deploy-api" \
            "ingest-api" \
            "ingest-worker" \
            "search-api" \
            "agent-api" \
            "docs-api" \
            "ai-portal" \
            "agent-manager" \
            "host-agent (host)" \
            "Back"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select service [1-19]:${NC} ")" choice
        
        case "${choice:-}" in
            1) services_group_menu "authz-api" "authz-api" ;;
            2) services_group_menu "postgres" "postgres" ;;
            3) services_group_menu "redis" "redis" ;;
            4) services_group_menu "milvus" "milvus" ;;
            5) services_group_menu "minio" "minio" ;;
            6) services_group_menu "nginx" "nginx" ;;
            7) services_group_menu "litellm" "litellm" ;;
            8) 
                # MLX or vLLM based on platform
                if [[ "$llm_backend_label" == "mlx (host)" ]]; then
                    host_service_menu "mlx"
                else
                    services_group_menu "vllm" "vllm"
                fi
                ;;
            9) services_group_menu "embedding-api" "embedding-api" ;;
            10) services_group_menu "deploy-api" "deploy-api" ;;
            11) services_group_menu "ingest-api" "ingest-api" ;;
            12) services_group_menu "ingest-worker" "ingest-worker" ;;
            13) services_group_menu "search-api" "search-api" ;;
            14) services_group_menu "agent-api" "agent-api" ;;
            15) services_group_menu "docs-api" "docs-api" ;;
            16) services_group_menu "ai-portal" "ai-portal" ;;
            17) services_group_menu "agent-manager" "agent-manager" ;;
            18) host_service_menu "host-agent" ;;
            19|b|B|"") return 0 ;;
        esac
    else
        # No local LLM backend
        menu "Select Service" \
            "authz-api" \
            "postgres" \
            "redis" \
            "milvus" \
            "minio" \
            "nginx" \
            "litellm" \
            "embedding-api" \
            "deploy-api" \
            "ingest-api" \
            "ingest-worker" \
            "search-api" \
            "agent-api" \
            "docs-api" \
            "ai-portal" \
            "agent-manager" \
            "Back"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select service [1-17]:${NC} ")" choice
        
        case "${choice:-}" in
            1) services_group_menu "authz-api" "authz-api" ;;
            2) services_group_menu "postgres" "postgres" ;;
            3) services_group_menu "redis" "redis" ;;
            4) services_group_menu "milvus" "milvus" ;;
            5) services_group_menu "minio" "minio" ;;
            6) services_group_menu "nginx" "nginx" ;;
            7) services_group_menu "litellm" "litellm" ;;
            8) services_group_menu "embedding-api" "embedding-api" ;;
            9) services_group_menu "deploy-api" "deploy-api" ;;
            10) services_group_menu "ingest-api" "ingest-api" ;;
            11) services_group_menu "ingest-worker" "ingest-worker" ;;
            12) services_group_menu "search-api" "search-api" ;;
            13) services_group_menu "agent-api" "agent-api" ;;
            14) services_group_menu "docs-api" "docs-api" ;;
            15) services_group_menu "ai-portal" "ai-portal" ;;
            16) services_group_menu "agent-manager" "agent-manager" ;;
            17|b|B|"") return 0 ;;
        esac
    fi
}

handle_services() {
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    # Detect LLM backend for menu display
    local llm_backend_service=""
    local os arch
    os=$(uname -s)
    arch=$(uname -m)
    if [[ "$os" == "Darwin" && ("$arch" == "arm64" || "$arch" == "aarch64") ]]; then
        llm_backend_service="mlx"
    elif command -v nvidia-smi &>/dev/null && [[ $(nvidia-smi -L 2>/dev/null | wc -l) -gt 0 ]]; then
        llm_backend_service="vllm"
    fi
    
    case "$backend" in
        docker)
            # Main services menu for Docker
            while true; do
                clear
                box "Services Management ($env)" 70
                
                # Show current status
                echo ""
                info "Current service status:"
                (cd "$REPO_ROOT" && docker compose -f docker-compose.yml ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null) || echo "  (no services running)"
                echo ""
                
                # Build LLM services description based on detected platform
                local llm_desc="litellm"
                if [[ -n "$llm_backend_service" ]]; then
                    llm_desc="litellm, $llm_backend_service, embedding"
                else
                    llm_desc="litellm, embedding"
                fi
                
                menu "Select Service Group" \
                    "All Services" \
                    "Specific Service" \
                    "Core Services (authz, postgres, redis, milvus, minio)" \
                    "LLM Services ($llm_desc)" \
                    "API Services (deploy, ingest, search, agent, docs)" \
                    "App Services (nginx, ai-portal, agent-manager)" \
                    "Back to Main Menu"
                echo -e "  ${DIM}Press 's' to refresh status and return to main menu${NC}"
                
                local choice=""
                read -p "$(echo -e "${BOLD}Select option [1-7]:${NC} ")" choice
                
                # Build LLM services list based on platform
                local llm_services="litellm"
                if [[ "$llm_backend_service" == "mlx" ]]; then
                    llm_services="litellm mlx embedding-api"
                elif [[ "$llm_backend_service" == "vllm" ]]; then
                    llm_services="litellm vllm embedding-api"
                else
                    llm_services="litellm embedding-api"
                fi
                
                case "${choice:-}" in
                    1)
                        services_group_menu "All Services" ""
                        [[ $? -eq $RETURN_TO_STATUS ]] && return $RETURN_TO_STATUS
                        ;;
                    2)
                        services_select_specific
                        [[ $? -eq $RETURN_TO_STATUS ]] && return $RETURN_TO_STATUS
                        ;;
                    3)
                        services_group_menu "Core Services" "authz-api postgres redis milvus minio"
                        [[ $? -eq $RETURN_TO_STATUS ]] && return $RETURN_TO_STATUS
                        ;;
                    4)
                        services_group_menu "LLM Services" "$llm_services"
                        [[ $? -eq $RETURN_TO_STATUS ]] && return $RETURN_TO_STATUS
                        ;;
                    5)
                        services_group_menu "API Services" "deploy-api ingest-api ingest-worker search-api agent-api docs-api"
                        [[ $? -eq $RETURN_TO_STATUS ]] && return $RETURN_TO_STATUS
                        ;;
                    6)
                        services_group_menu "App Services" "nginx ai-portal agent-manager"
                        [[ $? -eq $RETURN_TO_STATUS ]] && return $RETURN_TO_STATUS
                        ;;
                    7|b|B|"")
                        return 0
                        ;;
                    s|S)
                        # Return to main status dashboard
                        return $RETURN_TO_STATUS
                        ;;
                esac
            done
            ;;
            
        proxmox)
            # Proxmox service management uses Ansible
            # Use existing deploy script for Proxmox deployments
            if BUSIBOX_ENV="$env" bash "${SCRIPT_DIR}/deploy.sh"; then
                set_install_status "deployed"
                echo ""
                success "Deployment completed successfully"
            else
                echo ""
                error "Deployment failed. Check errors above for details."
            fi
            echo ""
            pause
            ;;
    esac
}

# DEPRECATED: Old service functions - replaced by new unified services_* functions
# Kept for backward compatibility, but these redirect to new functions

# Run encryption migration (helper function for both Docker and Proxmox)
# Usage: _run_encryption_migration <env> <backend>
_run_encryption_migration() {
    local env="$1"
    local backend="$2"
    local container_prefix
    
    container_prefix=$(get_container_prefix)
    
    echo ""
    header "Encrypt Existing Files" 70
    echo ""
    info "This will encrypt all existing unencrypted files in MinIO storage"
    info "using the authz keystore envelope encryption system."
    echo ""
    echo "What this does:"
    echo "  1. Query files where is_encrypted = false"
    echo "  2. Download each file from MinIO"
    echo "  3. Encrypt via authz keystore API"
    echo "  4. Re-upload encrypted content to MinIO"
    echo "  5. Update is_encrypted = true in database"
    echo ""
    
    # Determine environment and container ID
    local ingest_host ctid
    
    if [[ "$backend" == "docker" ]] || [[ "$env" == "development" ]] || [[ "$env" == "local" ]] || [[ "$env" == "demo" ]]; then
        ingest_host="docker:${container_prefix}-ingest-api"
        info "Using Docker environment"
    elif [[ "$env" == "staging" ]] || [[ "$env" == "test" ]]; then
        ingest_host="10.96.201.206"
        ctid="306"  # TEST-ingest-lxc
        info "Using staging/test environment (container $ctid)"
    else
        ingest_host="10.96.200.206"
        ctid="206"  # ingest-lxc
        info "Using production environment (container $ctid)"
    fi
    echo ""
    
    local encryption_script="${REPO_ROOT}/scripts/migrations/encrypt-existing-files.py"
    
    if [[ ! -f "$encryption_script" ]]; then
        error "Encryption script not found: $encryption_script"
        error "Make sure you have pulled the latest code: git pull"
        return 1
    fi
    
    # Helper function to run encryption on Proxmox container
    _run_encryption_on_proxmox() {
        local dry_run_flag="${1:-}"
        local container_id="$ctid"
        
        # Create scripts directory if it doesn't exist
        pct exec "$container_id" -- mkdir -p /srv/ingest/scripts 2>/dev/null || true
        
        # Copy script to container
        info "Copying encryption script to container $container_id..."
        pct push "$container_id" "$encryption_script" "/srv/ingest/scripts/encrypt-existing-files.py" || {
            error "Failed to copy script to container"
            return 1
        }
        
        # Run the script
        info "Running encryption script${dry_run_flag:+ (dry-run)}..."
        pct exec "$container_id" -- bash -c "set -a && source /srv/ingest/.env && set +a && /srv/ingest/venv/bin/python /srv/ingest/scripts/encrypt-existing-files.py ${dry_run_flag} --verbose" || {
            error "Script execution failed"
            return 1
        }
        return 0
    }
    
    # Helper function to run encryption on Docker
    _run_encryption_on_docker() {
        local dry_run_flag="${1:-}"
        local container_name="${ingest_host#docker:}"
        
        # Copy script to container
        info "Copying encryption script to container..."
        docker cp "$encryption_script" "$container_name:/tmp/encrypt-existing-files.py" || {
            error "Failed to copy script to container"
            return 1
        }
        
        # Run in container with proper environment
        info "Running encryption script${dry_run_flag:+ (dry-run)}..."
        docker exec "$container_name" bash -c "set -a && source /app/.env 2>/dev/null || true && /app/venv/bin/python /tmp/encrypt-existing-files.py ${dry_run_flag} --verbose" || {
            error "Script execution failed"
            return 1
        }
        return 0
    }
    
    # First do a dry run
    if confirm "Run a dry-run first to see what would be encrypted?"; then
        echo ""
        
        if [[ "$ingest_host" == docker:* ]]; then
            _run_encryption_on_docker "--dry-run" || true
        else
            _run_encryption_on_proxmox "--dry-run" || true
        fi
        
        echo ""
    fi
    
    if confirm "Proceed with actual encryption?"; then
        echo ""
        warn "This will modify files in MinIO. Make sure you have a backup!"
        echo ""
        
        if confirm "Are you SURE you want to proceed?"; then
            echo ""
            
            if [[ "$ingest_host" == docker:* ]]; then
                _run_encryption_on_docker "" && success "Encryption migration complete!" || error "Encryption migration failed"
            else
                _run_encryption_on_proxmox "" && success "Encryption migration complete!" || error "Encryption migration failed"
            fi
            
            echo ""
        fi
    fi
}

# Reset chat insights collection (helper function)
# Usage: _run_reset_insights <env> <backend>
_run_reset_insights() {
    local env="$1"
    local backend="$2"
    local container_prefix agent_container
    
    container_prefix=$(get_container_prefix)
    agent_container="${container_prefix}-agent-api"
    
    echo ""
    header "Reset Chat Insights Collection" 70
    echo ""
    warn "This will DROP the 'chat_insights' Milvus collection!"
    warn "All existing insights will be deleted."
    warn "Insights will be regenerated when users click 'Generate' in the UI."
    echo ""
    
    # Determine environment
    local milvus_host
    
    if [[ "$backend" == "docker" ]] || [[ "$env" == "development" ]] || [[ "$env" == "local" ]] || [[ "$env" == "demo" ]]; then
        milvus_host="docker:${container_prefix}-milvus"
        info "Using Docker environment"
    elif [[ "$env" == "staging" ]] || [[ "$env" == "test" ]]; then
        milvus_host="10.96.201.204"
        info "Using staging/test environment"
    else
        milvus_host="10.96.200.204"
        info "Using production environment"
    fi
    echo ""
    
    if confirm "Are you sure you want to reset the chat_insights collection?"; then
        echo ""
        info "Dropping and recreating chat_insights collection..."
        
        if [[ "$milvus_host" == docker:* ]]; then
            # Docker environment - run Python in agent-api container
            info "Running in container: $agent_container"
            
            docker exec "$agent_container" python -c "
from pymilvus import connections, utility

# Connect to Milvus
connections.connect('default', host='milvus', port=19530)

# Drop chat_insights collection if it exists
if utility.has_collection('chat_insights'):
    utility.drop_collection('chat_insights')
    print('Dropped collection: chat_insights')
else:
    print('Collection chat_insights does not exist')

# Drop task_insights collection if it exists  
if utility.has_collection('task_insights'):
    utility.drop_collection('task_insights')
    print('Dropped collection: task_insights')
else:
    print('Collection task_insights does not exist')

print('Collections will be recreated on next agent-api restart')
connections.disconnect('default')
" || {
                error "Failed to drop collections"
                return 1
            }
            
            echo ""
            success "Collections dropped. Restarting agent-api..."
            docker restart "$agent_container" || true
            sleep 3
            success "Done! Collections will be recreated with new schema."
        else
            # Proxmox environment - SSH to milvus host
            info "Connecting to $milvus_host..."
            ssh "root@$milvus_host" "cd /root/busibox && python3 -c \"
from pymilvus import connections, utility

connections.connect('default', host='localhost', port=19530)

if utility.has_collection('chat_insights'):
    utility.drop_collection('chat_insights')
    print('Dropped collection: chat_insights')
else:
    print('Collection chat_insights does not exist')

if utility.has_collection('task_insights'):
    utility.drop_collection('task_insights')
    print('Dropped collection: task_insights')
else:
    print('Collection task_insights does not exist')

print('Collections will be recreated on next agent service restart')
connections.disconnect('default')
\"" || {
                error "Failed to drop collections"
                return 1
            }
            
            echo ""
            success "Collections dropped. Restart agent service to recreate with new schema."
        fi
    fi
}

# Handle databases menu (renamed from migration)
handle_databases() {
    local env backend
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    echo ""
    header "Databases" 70
    echo ""
    
    # For non-Docker environments, show a limited menu with Proxmox-compatible options
    if [[ "$backend" != "docker" ]]; then
        info "Environment: $env ($backend)"
        echo ""
        
        menu "Database Options (Proxmox)" \
            "Encrypt Existing Files (MinIO)" \
            "Reset Chat Insights Collection" \
            "Back to Main Menu"
        echo -e "  ${DIM}Press 's' to refresh status and return to main menu${NC}"
        
        echo ""
        read -p "$(echo -e "${BOLD}Select option [1-3]:${NC} ")" proxmox_choice
        
        case "$proxmox_choice" in
            1)
                # Jump to encryption case (reuse the logic)
                _run_encryption_migration "$env" "$backend"
                pause
                ;;
            2)
                # Reset chat insights - this works on Proxmox
                _run_reset_insights "$env" "$backend"
                pause
                ;;
            3|b|B|"")
                return 0
                ;;
            s|S)
                return $RETURN_TO_STATUS
                ;;
            *)
                error "Invalid selection."
                pause
                ;;
        esac
        return 0
    fi
    
    local migration_script="${REPO_ROOT}/scripts/migrations/migrate_to_separate_databases.py"
    
    if [[ ! -f "$migration_script" ]]; then
        error "Migration script not found: $migration_script"
        pause
        return 1
    fi
    
    # Determine how to run the migration script
    # We run it inside the authz-api container which has asyncpg installed
    local run_migration_cmd=""
    local pg_password container_prefix authz_container
    pg_password=$(grep -E "^POSTGRES_PASSWORD=" "${REPO_ROOT}/.env.local" 2>/dev/null | cut -d'=' -f2 || echo "devpassword")
    container_prefix=$(get_container_prefix)
    authz_container="${container_prefix}-authz-api"
    
    # Check if authz-api container is running
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "${authz_container}"; then
        # Run inside Docker container
        run_migration_cmd="docker exec -e POSTGRES_HOST=postgres -e POSTGRES_PORT=5432 -e POSTGRES_PASSWORD=${pg_password} -e SOURCE_PASSWORD=${pg_password} ${authz_container} python"
        info "Running migration inside Docker container (${authz_container})..."
    else
        # Try running locally
        if ! command -v python3 &>/dev/null; then
            error "Python 3 is required. Start Docker containers or install Python 3."
            pause
            return 1
        fi
        if ! python3 -c "import asyncpg" 2>/dev/null; then
            error "asyncpg is not installed. Start Docker containers to run migration."
            info "Run: make docker-up"
            pause
            return 1
        fi
        run_migration_cmd="python3"
        export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
        export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
        export POSTGRES_PASSWORD="${pg_password:-devpassword}"
        export SOURCE_PASSWORD="${pg_password:-devpassword}"
    fi
    
    # Get current embedding model for display
    local current_model current_dim
    current_model=$(get_current_embedding_model)
    current_dim=$(get_embedding_dimension "$current_model")
    
    echo ""
    menu "Database Options" \
        "Check Migration Status (dry run)" \
        "Verify Existing Migrations" \
        "Migrate AuthZ Service (busibox -> authz)" \
        "Migrate Ingest Service (busibox -> files)" \
        "Migrate All Services" \
        "Cleanup Source (remove migrated tables from busibox)" \
        "Change Embedding Model [current: ${current_model##*/} (${current_dim}d)]" \
        "Check Embedding Model Migration" \
        "Migrate Embeddings (Milvus documents)" \
        "Reset Chat Insights Collection" \
        "Rebuild Milvus" \
        "Encrypt Existing Files (MinIO)" \
        "Back to Main Menu"
    echo -e "  ${DIM}Press 's' to refresh status and return to main menu${NC}"
    
    echo ""
    read -p "$(echo -e "${BOLD}Select option [1-13]:${NC} ")" migration_choice
    
    # For Docker execution, we need to copy the script into the container or cat it
    local docker_script_path="/tmp/migrate_db.py"
    if [[ "$run_migration_cmd" == docker* ]]; then
        # Copy migration script to container
        docker cp "$migration_script" "${authz_container}:${docker_script_path}" 2>/dev/null || {
            error "Failed to copy migration script to container"
            pause
            return 1
        }
        run_migration_cmd="docker exec -e POSTGRES_HOST=postgres -e POSTGRES_PORT=5432 -e POSTGRES_PASSWORD=${pg_password} -e SOURCE_PASSWORD=${pg_password} ${authz_container} python $docker_script_path"
    else
        run_migration_cmd="python3 $migration_script"
    fi
    
    case "$migration_choice" in
        1)
            echo ""
            header "Migration Status (Dry Run)" 70
            echo ""
            info "Checking what migrations would be performed..."
            echo ""
            $run_migration_cmd --all --dry-run || true
            pause
            ;;
        2)
            echo ""
            header "Verify Existing Migrations" 70
            echo ""
            info "Verifying data in target databases..."
            echo ""
            $run_migration_cmd --verify-only || true
            pause
            ;;
        3)
            echo ""
            header "Migrate AuthZ Service" 70
            echo ""
            if confirm "Migrate authz tables from 'busibox' to 'authz' database?"; then
                $run_migration_cmd --service authz || true
            fi
            pause
            ;;
        4)
            echo ""
            header "Migrate Ingest Service" 70
            echo ""
            if confirm "Migrate ingest tables from 'busibox' to 'files' database?"; then
                $run_migration_cmd --service ingest || true
            fi
            pause
            ;;
        5)
            echo ""
            header "Migrate All Services" 70
            echo ""
            if confirm "Migrate ALL tables from 'busibox' to their dedicated databases?"; then
                $run_migration_cmd --all || true
            fi
            pause
            ;;
        6)
            echo ""
            header "Cleanup Source Database" 70
            echo ""
            warn "This will REMOVE migrated tables from the 'busibox' database!"
            warn "Only run this AFTER verifying migrations are complete."
            echo ""
            if confirm "Are you SURE you want to cleanup source tables?"; then
                if confirm "Last chance - this is destructive! Proceed?"; then
                    $run_migration_cmd --all --cleanup || true
                fi
            fi
            pause
            ;;
        7)
            # Change Embedding Model
            echo ""
            header "Change Embedding Model" 70
            echo ""
            info "Current model: ${current_model} (${current_dim} dimensions)"
            echo ""
            echo "Available models:"
            echo ""
            echo "  1) bge-small-en-v1.5  - 384 dimensions,  ~67 MB  (~150 MB RAM)"
            echo "     Best for: Local dev on memory-constrained systems (Apple Silicon + MLX)"
            echo ""
            echo "  2) bge-base-en-v1.5   - 768 dimensions,  ~210 MB (~500 MB RAM)"
            echo "     Best for: Balance of quality and memory usage"
            echo ""
            echo "  3) bge-large-en-v1.5  - 1024 dimensions, ~1.2 GB (~3 GB RAM)"
            echo "     Best for: Production, highest quality semantic search"
            echo ""
            echo "  b) Back (no change)"
            echo ""
            
            read -p "$(echo -e "${BOLD}Select model [1-3, b]:${NC} ")" model_choice
            
            local new_model new_dim
            case "$model_choice" in
                1)
                    new_model="BAAI/bge-small-en-v1.5"
                    new_dim="384"
                    ;;
                2)
                    new_model="BAAI/bge-base-en-v1.5"
                    new_dim="768"
                    ;;
                3)
                    new_model="BAAI/bge-large-en-v1.5"
                    new_dim="1024"
                    ;;
                b|B|"")
                    info "No changes made."
                    pause
                    return 0
                    ;;
                *)
                    error "Invalid selection."
                    pause
                    return 0
                    ;;
            esac
            
            if [[ "$new_model" == "$current_model" ]]; then
                info "Model is already set to ${new_model}. No changes needed."
                pause
                return 0
            fi
            
            echo ""
            warn "Changing embedding model from ${current_model##*/} to ${new_model##*/}"
            warn ""
            warn "This will:"
            echo "  1. Update FASTEMBED_MODEL and EMBEDDING_DIMENSION in .env.local"
            echo "  2. Restart the embedding-api container (to load new model)"
            echo "  3. Drop and recreate Milvus 'documents' collection (new dimension)"
            echo "  4. Queue all documents for re-embedding"
            echo ""
            warn "All existing embeddings will be regenerated!"
            echo ""
            
            if confirm "Proceed with embedding model change?"; then
                echo ""
                
                # Step 1: Update .env.local
                info "Step 1/4: Updating .env.local..."
                local env_file="${REPO_ROOT}/.env.local"
                
                # Update or add FASTEMBED_MODEL
                if grep -q "^FASTEMBED_MODEL=" "$env_file" 2>/dev/null; then
                    sed -i.bak "s|^FASTEMBED_MODEL=.*|FASTEMBED_MODEL=${new_model}|" "$env_file"
                else
                    echo "FASTEMBED_MODEL=${new_model}" >> "$env_file"
                fi
                
                # Update or add EMBEDDING_DIMENSION
                if grep -q "^EMBEDDING_DIMENSION=" "$env_file" 2>/dev/null; then
                    sed -i.bak "s|^EMBEDDING_DIMENSION=.*|EMBEDDING_DIMENSION=${new_dim}|" "$env_file"
                else
                    echo "EMBEDDING_DIMENSION=${new_dim}" >> "$env_file"
                fi
                
                rm -f "${env_file}.bak" 2>/dev/null
                success "Updated .env.local"
                
                # Step 2: Restart embedding-api
                info "Step 2/4: Restarting embedding-api (this may take a minute to download the model)..."
                local ingest_container="${container_prefix}-ingest-api"
                local embedding_container="${container_prefix}-embedding-api"
                local milvus_container="${container_prefix}-milvus"
                
                (cd "$REPO_ROOT" && BUSIBOX_HOST_PATH="$REPO_ROOT" docker compose -f docker-compose.yml --env-file .env.local up -d --force-recreate embedding-api) || {
                    error "Failed to restart embedding-api"
                    pause
                    return 1
                }
                
                # Wait for embedding-api to be healthy
                info "Waiting for embedding-api to load model and become healthy..."
                local retries=0
                while [ $retries -lt 60 ]; do
                    if docker exec "${embedding_container}" curl -sf http://localhost:8005/health 2>/dev/null | grep -q '"model_loaded":true'; then
                        success "Embedding API is healthy with new model!"
                        break
                    fi
                    sleep 3
                    ((retries++))
                    echo -ne "\r  Loading model... ${retries}/60 (up to 3 minutes)"
                done
                echo ""
                
                if [ $retries -eq 60 ]; then
                    error "Embedding API failed to become healthy after 3 minutes"
                    error "Check logs: docker logs ${embedding_container}"
                    pause
                    return 1
                fi
                
                # Step 3: Reset Milvus documents collection
                info "Step 3/4: Resetting Milvus 'documents' collection with new dimension..."
                
                # Stop milvus, delete data, restart
                (cd "$REPO_ROOT" && docker compose -f docker-compose.yml --env-file .env.local stop milvus) || {
                    error "Failed to stop Milvus"
                    pause
                    return 1
                }
                
                (cd "$REPO_ROOT" && docker compose -f docker-compose.yml --env-file .env.local rm -f milvus) || true
                docker volume rm busibox_milvus_data 2>/dev/null || true
                
                (cd "$REPO_ROOT" && BUSIBOX_HOST_PATH="$REPO_ROOT" docker compose -f docker-compose.yml --env-file .env.local up -d milvus) || {
                    error "Failed to start Milvus"
                    pause
                    return 1
                }
                
                # Wait for Milvus to be ready
                info "Waiting for Milvus to be ready..."
                retries=0
                while [ $retries -lt 30 ]; do
                    if docker exec "${milvus_container}" curl -sf http://localhost:9091/healthz >/dev/null 2>&1; then
                        success "Milvus is healthy!"
                        break
                    fi
                    sleep 2
                    ((retries++))
                    echo -ne "\r  Waiting... ${retries}/30"
                done
                echo ""
                
                if [ $retries -eq 30 ]; then
                    error "Milvus failed to become healthy"
                    pause
                    return 1
                fi
                
                # Run milvus-init to create collections with new dimension
                (cd "$REPO_ROOT" && docker compose -f docker-compose.yml --env-file .env.local run --rm milvus-init) || {
                    error "Failed to create Milvus schema"
                    pause
                    return 1
                }
                
                # Step 4: Queue documents for re-embedding
                info "Step 4/4: Queuing all documents for re-embedding..."
                
                # Restart services that depend on Milvus (they need to reconnect)
                (cd "$REPO_ROOT" && docker compose -f docker-compose.yml --env-file .env.local restart ingest-api search-api ingest-worker) || true
                
                # Wait for ingest-api to be healthy
                sleep 5
                
                docker exec "${ingest_container}" python -c "
import asyncio
import redis.asyncio as redis_async
import asyncpg
import os

async def requeue_all():
    conn = await asyncpg.connect(
        host=os.environ.get('POSTGRES_HOST', 'postgres'),
        port=int(os.environ.get('POSTGRES_PORT', 5432)),
        user=os.environ.get('POSTGRES_USER', 'busibox_user'),
        password=os.environ.get('POSTGRES_PASSWORD', 'devpassword'),
        database=os.environ.get('POSTGRES_DB', 'files'),
    )
    
    files = await conn.fetch('''
        SELECT f.file_id, f.user_id, f.storage_path, f.original_filename, f.mime_type
        FROM ingestion_files f
        JOIN ingestion_status s ON f.file_id = s.file_id
        WHERE s.stage = 'completed'
    ''')
    
    print(f'Found {len(files)} documents to re-embed')
    
    if len(files) == 0:
        print('No documents to process')
        await conn.close()
        return
    
    redis_client = redis_async.Redis(
        host=os.environ.get('REDIS_HOST', 'redis'),
        port=int(os.environ.get('REDIS_PORT', 6379)),
        decode_responses=True,
    )
    
    stream_name = os.environ.get('REDIS_STREAM', 'jobs:ingestion')
    
    for file_row in files:
        file_id = str(file_row['file_id'])
        user_id = str(file_row['user_id'])
        
        await conn.execute('''
            UPDATE ingestion_status
            SET stage = 'queued', progress = 0, updated_at = NOW()
            WHERE file_id = \$1
        ''', file_row['file_id'])
        
        await conn.execute('''
            UPDATE ingestion_files
            SET vector_count = 0, updated_at = NOW()
            WHERE file_id = \$1
        ''', file_row['file_id'])
        
        job_data = {
            'job_id': file_id,
            'file_id': file_id,
            'user_id': user_id,
            'storage_path': file_row['storage_path'],
            'original_filename': file_row['original_filename'],
            'mime_type': file_row['mime_type'],
            'reprocess': 'true',
            'start_stage': 'embedding',
        }
        
        await redis_client.xadd(stream_name, job_data)
        print(f'Queued: {file_row[\"original_filename\"]}')
    
    await redis_client.aclose()
    await conn.close()
    print(f'Successfully queued {len(files)} documents for re-embedding')

asyncio.run(requeue_all())
" || {
                    warn "Failed to queue documents (this is OK if you have no documents yet)"
                }
                
                echo ""
                success "Embedding model changed successfully!"
                echo ""
                info "New model: ${new_model} (${new_dim} dimensions)"
                info "Documents are being re-embedded in the background."
                info "Check progress: docker logs -f ${container_prefix}-ingest-worker"
            fi
            pause
            ;;
        8)
            # Check embedding model migration
            echo ""
            header "Check Embedding Model Migration" 70
            echo ""
            info "Checking if embedding model has changed..."
            echo ""
            info "This compares the configured embedding model in model_registry.yml"
            info "against the current Milvus collection dimensions."
            echo ""
            
            # Determine environment and backend
            local milvus_host ingest_host deploy_backend
            deploy_backend="${backend:-docker}"
            
            if [[ "$deploy_backend" == "docker" ]] || [[ "$env" == "development" ]] || [[ "$env" == "local" ]] || [[ "$env" == "demo" ]]; then
                # Docker environment - use container names with container prefix
                milvus_host="docker:${container_prefix}-milvus"
                ingest_host="docker:${container_prefix}-ingest-api"
                info "Using Docker environment"
            elif [[ "$env" == "staging" ]] || [[ "$env" == "test" ]]; then
                milvus_host="10.96.201.204"
                ingest_host="10.96.201.206"
                info "Using staging/test environment"
            else
                milvus_host="10.96.200.204"
                ingest_host="10.96.200.206"
                info "Using production environment"
            fi
            echo ""
            
            MILVUS_IP="$milvus_host" INGEST_IP="$ingest_host" bash "${REPO_ROOT}/provision/ansible/scripts/check-embedding-migration.sh" --check || true
            pause
            ;;
        9)
            # Migrate embeddings (Milvus)
            echo ""
            header "Migrate Embeddings (Milvus)" 70
            echo ""
            warn "This will DROP the existing Milvus 'documents' collection!"
            warn "All existing embeddings will be deleted."
            warn "You will need to re-ingest all documents after migration."
            echo ""
            
            # Determine environment and backend
            local milvus_host ingest_host deploy_backend
            deploy_backend="${backend:-docker}"
            
            if [[ "$deploy_backend" == "docker" ]] || [[ "$env" == "development" ]] || [[ "$env" == "local" ]] || [[ "$env" == "demo" ]]; then
                # Docker environment - use container names with container prefix
                milvus_host="docker:${container_prefix}-milvus"
                ingest_host="docker:${container_prefix}-ingest-api"
                info "Using Docker environment"
            elif [[ "$env" == "staging" ]] || [[ "$env" == "test" ]]; then
                milvus_host="10.96.201.204"
                ingest_host="10.96.201.206"
                info "Using staging/test environment"
            else
                milvus_host="10.96.200.204"
                ingest_host="10.96.200.206"
                info "Using production environment"
            fi
            echo ""
            
            if confirm "Are you sure you want to migrate embeddings?"; then
                echo ""
                MILVUS_IP="$milvus_host" INGEST_IP="$ingest_host" bash "${REPO_ROOT}/provision/ansible/scripts/check-embedding-migration.sh" --migrate || true
            fi
            pause
            ;;
        10)
            # Reset Chat Insights Collection
            echo ""
            header "Reset Chat Insights Collection" 70
            echo ""
            warn "This will DROP the 'chat_insights' Milvus collection!"
            warn "All existing insights will be deleted."
            warn "Insights will be regenerated when users click 'Generate' in the UI."
            echo ""
            
            # Determine environment and backend
            local milvus_host deploy_backend agent_container
            deploy_backend="${backend:-docker}"
            agent_container="${container_prefix}-agent-api"
            
            if [[ "$deploy_backend" == "docker" ]] || [[ "$env" == "development" ]] || [[ "$env" == "local" ]] || [[ "$env" == "demo" ]]; then
                # Docker environment
                milvus_host="docker:${container_prefix}-milvus"
                info "Using Docker environment"
            elif [[ "$env" == "staging" ]] || [[ "$env" == "test" ]]; then
                milvus_host="10.96.201.204"
                info "Using staging/test environment"
            else
                milvus_host="10.96.200.204"
                info "Using production environment"
            fi
            echo ""
            
            if confirm "Are you sure you want to reset the chat_insights collection?"; then
                echo ""
                info "Dropping and recreating chat_insights collection..."
                
                if [[ "$milvus_host" == docker:* ]]; then
                    # Docker environment - run Python in agent-api container
                    info "Running in container: $agent_container"
                    
                    docker exec "$agent_container" python -c "
from pymilvus import connections, utility

# Connect to Milvus
connections.connect('default', host='milvus', port=19530)

# Drop chat_insights collection if it exists
if utility.has_collection('chat_insights'):
    utility.drop_collection('chat_insights')
    print('Dropped collection: chat_insights')
else:
    print('Collection chat_insights does not exist')

# Drop task_insights collection if it exists  
if utility.has_collection('task_insights'):
    utility.drop_collection('task_insights')
    print('Dropped collection: task_insights')
else:
    print('Collection task_insights does not exist')

print('Collections will be recreated on next agent-api restart')
connections.disconnect('default')
" || {
                        error "Failed to drop collections"
                        pause
                        return 1
                    }
                    
                    echo ""
                    success "Collections dropped. Restarting agent-api..."
                    docker restart "$agent_container" || true
                    sleep 3
                    success "Done! Collections will be recreated with new schema."
                else
                    # Proxmox environment - SSH to milvus host
                    info "Connecting to $milvus_host..."
                    ssh "root@$milvus_host" "cd /root/busibox && python3 -c \"
from pymilvus import connections, utility

connections.connect('default', host='localhost', port=19530)

if utility.has_collection('chat_insights'):
    utility.drop_collection('chat_insights')
    print('Dropped collection: chat_insights')
else:
    print('Collection chat_insights does not exist')

if utility.has_collection('task_insights'):
    utility.drop_collection('task_insights')
    print('Dropped collection: task_insights')
else:
    print('Collection task_insights does not exist')

print('Collections will be recreated on next agent service restart')
connections.disconnect('default')
\"" || {
                        error "Failed to drop collections"
                        pause
                        return 1
                    }
                    
                    echo ""
                    success "Collections dropped. Restart agent service to recreate with new schema."
                fi
            fi
            pause
            ;;
        11)
            # Rebuild Milvus
            echo ""
            header "Rebuild Milvus" 70
            echo ""
            warn "This will COMPLETELY REBUILD Milvus from scratch!"
            warn ""
            warn "What this does:"
            echo "  1. Stop Milvus and delete all data (etcd, minio storage)"
            echo "  2. Restart Milvus with fresh empty collections"
            echo "  3. Queue ALL documents for re-embedding"
            echo ""
            warn "This is safe because:"
            echo "  - All document text is stored in PostgreSQL (ingestion_chunks)"
            echo "  - Embeddings can be regenerated from chunk text"
            echo "  - No data will be lost"
            echo ""
            warn "This will take a long time if you have many documents!"
            echo ""
            
            # Determine environment and backend
            local milvus_host ingest_host deploy_backend milvus_container ingest_container
            deploy_backend="${backend:-docker}"
            milvus_container="${container_prefix}-milvus"
            ingest_container="${container_prefix}-ingest-api"
            
            if [[ "$deploy_backend" == "docker" ]] || [[ "$env" == "development" ]] || [[ "$env" == "local" ]] || [[ "$env" == "demo" ]]; then
                milvus_host="docker:${milvus_container}"
                ingest_host="docker:${ingest_container}"
                info "Using Docker environment"
            elif [[ "$env" == "staging" ]] || [[ "$env" == "test" ]]; then
                milvus_host="10.96.201.204"
                ingest_host="10.96.201.206"
                info "Using staging/test environment"
            else
                milvus_host="10.96.200.204"
                ingest_host="10.96.200.206"
                info "Using production environment"
            fi
            echo ""
            
            if confirm "Are you ABSOLUTELY SURE you want to rebuild Milvus?"; then
                if confirm "This is your last chance to cancel. Proceed?"; then
                    echo ""
                    info "Starting Milvus rebuild..."
                    
                    if [[ "$milvus_host" == docker:* ]]; then
                        # Docker environment
                        # Only reset the milvus service and milvus_data volume
                        # etcd and milvus-minio store metadata, not vectors - leave them alone
                        
                        info "Step 1/5: Stopping Milvus..."
                        (cd "$REPO_ROOT" && docker compose -f docker-compose.yml --env-file .env.local stop milvus) || {
                            error "Failed to stop Milvus"
                            pause
                            return 1
                        }
                        
                        info "Step 2/5: Removing Milvus container and data volume..."
                        (cd "$REPO_ROOT" && docker compose -f docker-compose.yml --env-file .env.local rm -f milvus) || true
                        docker volume rm busibox_milvus_data 2>/dev/null || true
                        
                        info "Step 3/5: Starting fresh Milvus..."
                        # etcd and milvus-minio should already be running; just start milvus
                        (cd "$REPO_ROOT" && BUSIBOX_HOST_PATH="$REPO_ROOT" docker compose -f docker-compose.yml --env-file .env.local up -d milvus) || {
                            error "Failed to start Milvus"
                            pause
                            return 1
                        }
                        
                        info "Waiting for Milvus to be ready..."
                        # Wait for Milvus health check to pass
                        local retries=0
                        while [ $retries -lt 30 ]; do
                            if docker exec "${milvus_container}" curl -sf http://localhost:9091/healthz >/dev/null 2>&1; then
                                success "Milvus is healthy!"
                                break
                            fi
                            sleep 2
                            ((retries++))
                            echo -ne "\r  Waiting... ${retries}/30"
                        done
                        echo ""
                        
                        if [ $retries -eq 30 ]; then
                            error "Milvus failed to become healthy after 60 seconds"
                            pause
                            return 1
                        fi
                        
                        info "Step 4/5: Creating Milvus schema (collections)..."
                        # Run the milvus-init container to create schema
                        (cd "$REPO_ROOT" && docker compose -f docker-compose.yml --env-file .env.local run --rm milvus-init) || {
                            error "Failed to create Milvus schema"
                            pause
                            return 1
                        }
                        
                        info "Step 5/5: Queuing all documents for re-embedding..."
                        # Call the reprocess-all endpoint
                        docker exec "${ingest_container}" python -c "
import asyncio
import redis.asyncio as redis_async
import asyncpg
import os

async def requeue_all():
    # Connect to PostgreSQL
    conn = await asyncpg.connect(
        host=os.environ.get('POSTGRES_HOST', 'postgres'),
        port=int(os.environ.get('POSTGRES_PORT', 5432)),
        user=os.environ.get('POSTGRES_USER', 'postgres'),
        password=os.environ.get('POSTGRES_PASSWORD', 'devpassword'),
        database=os.environ.get('INGEST_DB', 'files'),
    )
    
    # Get all files that have completed processing
    # Schema: ingestion_files has file metadata, ingestion_status has processing status
    files = await conn.fetch('''
        SELECT f.file_id, f.user_id, f.storage_path, f.original_filename, f.mime_type
        FROM ingestion_files f
        JOIN ingestion_status s ON f.file_id = s.file_id
        WHERE s.stage = 'completed'
    ''')
    
    print(f'Found {len(files)} documents to re-embed')
    
    if len(files) == 0:
        print('No documents to process')
        await conn.close()
        return
    
    # Connect to Redis
    redis_client = redis_async.Redis(
        host=os.environ.get('REDIS_HOST', 'redis'),
        port=int(os.environ.get('REDIS_PORT', 6379)),
        decode_responses=True,
    )
    
    # Queue each file for reprocessing from embedding stage
    stream_name = os.environ.get('REDIS_STREAM', 'jobs:ingestion')
    
    for file_row in files:
        file_id = str(file_row['file_id'])
        user_id = str(file_row['user_id'])
        
        # Reset ingestion status in the ingestion_status table
        await conn.execute('''
            UPDATE ingestion_status
            SET stage = 'queued',
                progress = 0,
                updated_at = NOW()
            WHERE file_id = \$1
        ''', file_row['file_id'])
        
        # Reset vector count in ingestion_files
        await conn.execute('''
            UPDATE ingestion_files
            SET vector_count = 0,
                updated_at = NOW()
            WHERE file_id = \$1
        ''', file_row['file_id'])
        
        # Add job to queue
        job_data = {
            'job_id': file_id,
            'file_id': file_id,
            'user_id': user_id,
            'storage_path': file_row['storage_path'],
            'original_filename': file_row['original_filename'],
            'mime_type': file_row['mime_type'],
            'reprocess': 'true',
            'start_stage': 'embedding',
        }
        
        await redis_client.xadd(stream_name, job_data)
        print(f'Queued: {file_row[\"original_filename\"]}')
    
    await redis_client.aclose()
    await conn.close()
    print(f'Successfully queued {len(files)} documents for re-embedding')

asyncio.run(requeue_all())
" || {
                            error "Failed to queue documents for re-embedding"
                            pause
                            return 1
                        }
                        
                        # Restart ingest-worker to pick up the queued jobs
                        info "Restarting ingest-worker to process queue..."
                        (cd "$REPO_ROOT" && docker compose -f docker-compose.yml --env-file .env.local restart ingest-worker) || true
                        
                        success "Milvus rebuild complete!"
                        echo ""
                        info "Documents are now being re-embedded in the background."
                        info "Check ingest-worker logs for progress:"
                        echo "  docker logs -f ${container_prefix}-ingest-worker"
                        
                    else
                        # Proxmox environment
                        warn "Proxmox Milvus rebuild not yet implemented."
                        warn "Please run the following manually on the Milvus host ($milvus_host):"
                        echo ""
                        echo "  1. systemctl stop milvus-standalone"
                        echo "  2. rm -rf /var/lib/milvus/*"
                        echo "  3. systemctl start milvus-standalone"
                        echo "  4. Then run: make migration -> 'Migrate Embeddings'"
                    fi
                fi
            fi
            pause
            ;;
        12)
            # Encrypt existing files in MinIO - use the helper function
            _run_encryption_migration "$env" "$backend"
            pause
            ;;
        13|b|B|"")
            return 0
            ;;
        s|S)
            # Return to main status dashboard
            return $RETURN_TO_STATUS
            ;;
        *)
            error "Invalid selection."
            pause
            ;;
    esac
}

# Helper to run docker tests and save results
# Usage: run_docker_test "service" ["pytest-args"]
run_docker_test() {
    local service_key="$1"  # Full key like "ingest:unit" or "agent"
    local pytest_args="${2:-}"
    
    # Extract base service name (before colon if present)
    local base_service="${service_key%%:*}"
    
    local cmd="make test-docker SERVICE=$base_service"
    
    if [[ -n "$pytest_args" ]]; then
        cmd="$cmd ARGS='$pytest_args'"
    fi
    
    save_last_command "$cmd"
    if (cd "$REPO_ROOT" && ARGS="$pytest_args" make test-docker SERVICE="$base_service"); then
        save_test_result "$service_key" "passed"
        return 0
    else
        save_test_result "$service_key" "failed"
        return 1
    fi
}

handle_test() {
    # Main test menu loop
    while true; do
        clear
        local choice
        choice=$(show_test_main_menu)
        
        case "$choice" in
            back)
                return 0
                ;;
            status)
                # 's' key pressed - return to main status
                return $RETURN_TO_STATUS
                ;;
            pvt)
                handle_test_pvt
                ;;
            services)
                handle_test_services
                ;;
            apps)
                handle_test_apps
                ;;
            clear)
                echo ""
                if confirm "Clear all test results?"; then
                    clear_test_results
                    success "Test results cleared"
                fi
                pause
                ;;
        esac
    done
}

show_test_main_menu() {
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    # Display everything to stderr so it shows on screen
    {
        echo ""
        header "Test Menu" 70
        
        # Show test status summary
        echo ""
        echo -e "${BOLD}Test Status:${NC}"
        
        # PVT status
        local pvt_result=$(get_test_result "pvt")
        if [[ "$pvt_result" == "passed" ]]; then
            echo -e "  PVT: ${GREEN}✓ Passed${NC}"
        elif [[ "$pvt_result" == "failed" ]]; then
            echo -e "  PVT: ${RED}✗ Failed${NC}"
        else
            echo -e "  PVT: ${DIM}Not run${NC}"
        fi
        
        # Service tests status (only authz, ingest, search, agent)
        local failed_services=($(get_failed_services "services_only"))
        local passed_services=($(get_passed_services "services_only"))
        
        if [[ ${#failed_services[@]} -gt 0 ]]; then
            echo -e "  Services: ${RED}Failed: ${failed_services[*]}${NC}"
        fi
        if [[ ${#passed_services[@]} -gt 0 ]]; then
            echo -e "  Services: ${GREEN}Passed: ${passed_services[*]}${NC}"
        fi
        if [[ ${#failed_services[@]} -eq 0 && ${#passed_services[@]} -eq 0 ]]; then
            echo -e "  Services: ${DIM}Not run${NC}"
        fi
        
        # App tests status (ai-portal, agent-manager)
        local failed_apps=($(get_failed_apps))
        local passed_apps=($(get_passed_apps))
        
        if [[ ${#failed_apps[@]} -gt 0 ]]; then
            echo -e "  Apps: ${RED}Failed: ${failed_apps[*]}${NC}"
        fi
        if [[ ${#passed_apps[@]} -gt 0 ]]; then
            echo -e "  Apps: ${GREEN}Passed: ${passed_apps[*]}${NC}"
        fi
        if [[ ${#failed_apps[@]} -eq 0 && ${#passed_apps[@]} -eq 0 ]]; then
            echo -e "  Apps: ${DIM}Not run${NC}"
        fi
        
        echo ""
        menu "Select Test Category" \
            "PVT Tests (Post-Deployment Validation)" \
            "Service Tests (AuthZ, Ingest, Search, Agent)" \
            "App Tests (AI Portal, Agent Manager)" \
            "Clear Test Results" \
            "Back to Main Menu"
        echo -e "  ${DIM}Press 's' to refresh status and return to main menu${NC}"
    } >&2
    
    read -p "$(echo -e "${BOLD}Select option [1-5]:${NC} ")" choice
    
    # Return choice to stdout (for capture)
    case "$choice" in
        1) echo "pvt" ;;
        2) echo "services" ;;
        3) echo "apps" ;;
        4) echo "clear" ;;
        5|b|B) echo "back" ;;
        s|S) echo "status" ;;
        *) echo "back" ;;
    esac
}

handle_test_pvt() {
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    while true; do
        echo ""
        header "PVT Tests" 70
        echo ""
        info "Post-Deployment Validation Tests verify services are healthy after deployment"
        echo ""
        
        menu "PVT Test Options" \
            "Run All PVT Tests" \
            "Back to Test Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-2]:${NC} ")" choice
        
        case "$choice" in
            1)
                echo ""
                info "Running PVT tests (tests marked with @pytest.mark.pvt)..."
                case "$backend" in
                    docker)
                        # Run PVT tests for each service (test_pvt.py file in integration/)
                        local services=("authz" "ingest" "search" "agent")
                        local failed=0
                        local cmd
                        
                        for svc in "${services[@]}"; do
                            echo ""
                            info "Running PVT tests for $svc..."
                            if ! run_docker_test "$svc" "tests/integration/test_pvt.py"; then
                                ((failed++))
                            fi
                        done
                        
                        echo ""
                        if [[ $failed -eq 0 ]]; then
                            success "All PVT tests passed!"
                            save_test_result "pvt" "passed"
                        else
                            error "$failed service(s) failed PVT tests"
                            save_test_result "pvt" "failed"
                        fi
                        ;;
                    proxmox)
                        if bash "${SCRIPT_DIR}/test.sh" pvt; then
                            save_test_result "pvt" "passed"
                        else
                            save_test_result "pvt" "failed"
                        fi
                        ;;
                esac
                pause
                ;;
            2)
                return 0
                ;;
            *)
                return 0
                ;;
        esac
    done
}

handle_test_services() {
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    while true; do
        echo ""
        header "Service Tests" 70
        
        # Show test status
        local failed_services=($(get_failed_services))
        local passed_services=($(get_passed_services))
        
        if [[ ${#failed_services[@]} -gt 0 ]]; then
            echo ""
            warn "Failed: ${failed_services[*]}"
        fi
        if [[ ${#passed_services[@]} -gt 0 ]]; then
            echo ""
            success "Passed: ${passed_services[*]}"
        fi
        
        echo ""
        
        # Build menu options dynamically
        local menu_items=("Run All Services")
        
        # Add "Run Failed Services" if there are failures
        if [[ ${#failed_services[@]} -gt 0 ]]; then
            menu_items+=("Run Failed Services (${failed_services[*]})")
        fi
        
        menu_items+=(
            "Test AuthZ Service"
            "Test Ingest Service"
            "Test Search Service"
            "Test Agent Service"
            "Back to Test Menu"
        )
        
        menu "Service Test Options" "${menu_items[@]}"
        
        local max_option=$((${#menu_items[@]}))
        read -p "$(echo -e "${BOLD}Select option [1-${max_option}]:${NC} ")" choice
        
        # Calculate option numbers dynamically
        local opt=1
        local all_opt=$opt; ((opt++))
        local failed_opt=0
        if [[ ${#failed_services[@]} -gt 0 ]]; then
            failed_opt=$opt; ((opt++))
        fi
        local authz_opt=$opt; ((opt++))
        local ingest_opt=$opt; ((opt++))
        local search_opt=$opt; ((opt++))
        local agent_opt=$opt; ((opt++))
        local back_opt=$opt
        
        local cmd=""
        case "$choice" in
            $all_opt)
                echo ""
                case "$backend" in
                    docker)
                        run_docker_test "all"
                        ;;
                    proxmox)
                        bash "${SCRIPT_DIR}/test.sh" services all
                        ;;
                esac
                pause
                ;;
            $failed_opt)
                if [[ $failed_opt -ne 0 ]]; then
                    echo ""
                    info "Running failed services: ${failed_services[*]}"
                    echo ""
                    case "$backend" in
                        docker)
                            for svc in "${failed_services[@]}"; do
                                run_docker_test "$svc"
                            done
                            ;;
                        proxmox)
                            for svc in "${failed_services[@]}"; do
                                bash "${SCRIPT_DIR}/test.sh" services "$svc"
                            done
                            ;;
                    esac
                    pause
                fi
                ;;
            $authz_opt)
                echo ""
                case "$backend" in
                    docker)
                        run_docker_test "authz"
                        ;;
                    proxmox)
                        bash "${SCRIPT_DIR}/test.sh" services authz
                        ;;
                esac
                pause
                ;;
            $ingest_opt)
                handle_test_ingest
                ;;
            $search_opt)
                echo ""
                case "$backend" in
                    docker)
                        run_docker_test "search"
                        ;;
                    proxmox)
                        bash "${SCRIPT_DIR}/test.sh" services search
                        ;;
                esac
                pause
                ;;
            $agent_opt)
                handle_test_agent
                ;;
            $back_opt)
                return 0
                ;;
            *)
                return 0
                ;;
        esac
    done
}

# Handle Ingest service tests with unit/integration submenu
handle_test_ingest() {
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    while true; do
        echo ""
        header "Ingest Service Tests" 70
        
        # Show test status for ingest subtests
        local unit_result=$(get_test_result "ingest:unit")
        local integration_result=$(get_test_result "ingest:integration")
        local all_result=$(get_test_result "ingest")
        
        echo ""
        echo -e "${BOLD}Test Status:${NC}"
        if [[ "$unit_result" == "passed" ]]; then
            echo -e "  Unit: ${GREEN}✓ Passed${NC}"
        elif [[ "$unit_result" == "failed" ]]; then
            echo -e "  Unit: ${RED}✗ Failed${NC}"
        else
            echo -e "  Unit: ${DIM}Not run${NC}"
        fi
        
        if [[ "$integration_result" == "passed" ]]; then
            echo -e "  Integration: ${GREEN}✓ Passed${NC}"
        elif [[ "$integration_result" == "failed" ]]; then
            echo -e "  Integration: ${RED}✗ Failed${NC}"
        else
            echo -e "  Integration: ${DIM}Not run${NC}"
        fi
        
        if [[ "$all_result" == "passed" ]]; then
            echo -e "  All Tests: ${GREEN}✓ Passed${NC}"
        elif [[ "$all_result" == "failed" ]]; then
            echo -e "  All Tests: ${RED}✗ Failed${NC}"
        else
            echo -e "  All Tests: ${DIM}Not run${NC}"
        fi
        
        echo ""
        menu "Ingest Test Options" \
            "Run All Ingest Tests" \
            "Run Unit Tests Only" \
            "Run Integration Tests Only" \
            "Back to Service Tests"
        
        read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
        
        case "$choice" in
            1)
                echo ""
                case "$backend" in
                    docker)
                        run_docker_test "ingest"
                        ;;
                    proxmox)
                        bash "${SCRIPT_DIR}/test.sh" services ingest
                        ;;
                esac
                pause
                ;;
            2)
                echo ""
                info "Running unit tests..."
                case "$backend" in
                    docker)
                        run_docker_test "ingest:unit" "tests/unit"
                        ;;
                    proxmox)
                        bash "${SCRIPT_DIR}/test.sh" services ingest "tests/unit"
                        ;;
                esac
                pause
                ;;
            3)
                handle_test_ingest_integration
                ;;
            4)
                return 0
                ;;
            *)
                return 0
                ;;
        esac
    done
}

# Handle Ingest integration tests with individual file selection
handle_test_ingest_integration() {
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    while true; do
        echo ""
        header "Ingest Integration Tests" 70
        
        # Show test status for integration test files
        echo ""
        echo -e "${BOLD}Test Status:${NC}"
        
        local test_files=(
            "concurrent" "connectivity" "duplicates" "encryption_integration"
            "errors" "files" "full_pipeline" "health" "markdown_endpoints"
            "medium_pipeline" "multi_flow" "pipeline" "scope_enforcement"
            "services" "sse" "status" "upload"
        )
        
        local passed_count=0
        local failed_count=0
        
        for test in "${test_files[@]}"; do
            local result=$(get_test_result "ingest:integration:$test")
            if [[ "$result" == "passed" ]]; then
                ((passed_count++))
            elif [[ "$result" == "failed" ]]; then
                ((failed_count++))
            fi
        done
        
        if [[ $passed_count -gt 0 ]]; then
            echo -e "  ${GREEN}Passed: $passed_count${NC}"
        fi
        if [[ $failed_count -gt 0 ]]; then
            echo -e "  ${RED}Failed: $failed_count${NC}"
        fi
        if [[ $passed_count -eq 0 && $failed_count -eq 0 ]]; then
            echo -e "  ${DIM}Not run${NC}"
        fi
        
        echo ""
        menu "Ingest Integration Test Options" \
            "Run All Integration Tests" \
            "Run Failed Tests Only" \
            "─────────────────────" \
            "Test: concurrent" \
            "Test: connectivity" \
            "Test: duplicates" \
            "Test: encryption_integration" \
            "Test: errors" \
            "Test: files" \
            "Test: full_pipeline" \
            "Test: health" \
            "Test: markdown_endpoints" \
            "Test: medium_pipeline" \
            "Test: multi_flow" \
            "Test: pipeline" \
            "Test: scope_enforcement" \
            "Test: services" \
            "Test: sse" \
            "Test: status" \
            "Test: upload" \
            "─────────────────────" \
            "Back to Ingest Tests"
        
        read -p "$(echo -e "${BOLD}Select option [1-21]:${NC} ")" choice
        
        case "$choice" in
            1)
                echo ""
                info "Running all integration tests..."
                case "$backend" in
                    docker)
                        run_docker_test "ingest:integration" "tests/integration"
                        ;;
                    proxmox)
                        bash "${SCRIPT_DIR}/test.sh" services ingest "tests/integration"
                        ;;
                esac
                pause
                ;;
            2)
                echo ""
                local failed_tests=()
                for test in "${test_files[@]}"; do
                    if [[ "$(get_test_result "ingest:integration:$test")" == "failed" ]]; then
                        failed_tests+=("$test")
                    fi
                done
                
                if [[ ${#failed_tests[@]} -eq 0 ]]; then
                    warn "No failed tests to rerun"
                else
                    info "Running failed tests: ${failed_tests[*]}"
                    for test in "${failed_tests[@]}"; do
                        echo ""
                        case "$backend" in
                            docker)
                                run_docker_test "ingest:integration:$test" "tests/integration/test_${test}.py"
                                ;;
                            proxmox)
                                bash "${SCRIPT_DIR}/test.sh" services ingest "tests/integration/test_${test}.py"
                                ;;
                        esac
                    done
                fi
                pause
                ;;
            3)
                # Separator, do nothing
                ;;
            4|5|6|7|8|9|10|11|12|13|14|15|16|17|18|19|20)
                local test_idx=$((choice - 4))
                local test_name="${test_files[$test_idx]}"
                echo ""
                info "Running test: $test_name"
                case "$backend" in
                    docker)
                        run_docker_test "ingest:integration:$test_name" "tests/integration/test_${test_name}.py"
                        ;;
                    proxmox)
                        bash "${SCRIPT_DIR}/test.sh" services ingest "tests/integration/test_${test_name}.py"
                        ;;
                esac
                pause
                ;;
            21)
                return 0
                ;;
            *)
                return 0
                ;;
        esac
    done
}

# Handle Agent service tests with unit/integration submenu
handle_test_agent() {
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    while true; do
        echo ""
        header "Agent Service Tests" 70
        
        # Show test status for agent subtests
        local unit_result=$(get_test_result "agent:unit")
        local integration_result=$(get_test_result "agent:integration")
        local all_result=$(get_test_result "agent")
        
        echo ""
        echo -e "${BOLD}Test Status:${NC}"
        if [[ "$unit_result" == "passed" ]]; then
            echo -e "  Unit: ${GREEN}✓ Passed${NC}"
        elif [[ "$unit_result" == "failed" ]]; then
            echo -e "  Unit: ${RED}✗ Failed${NC}"
        else
            echo -e "  Unit: ${DIM}Not run${NC}"
        fi
        
        if [[ "$integration_result" == "passed" ]]; then
            echo -e "  Integration: ${GREEN}✓ Passed${NC}"
        elif [[ "$integration_result" == "failed" ]]; then
            echo -e "  Integration: ${RED}✗ Failed${NC}"
        else
            echo -e "  Integration: ${DIM}Not run${NC}"
        fi
        
        if [[ "$all_result" == "passed" ]]; then
            echo -e "  All Tests: ${GREEN}✓ Passed${NC}"
        elif [[ "$all_result" == "failed" ]]; then
            echo -e "  All Tests: ${RED}✗ Failed${NC}"
        else
            echo -e "  All Tests: ${DIM}Not run${NC}"
        fi
        
        echo ""
        menu "Agent Test Options" \
            "Run All Agent Tests" \
            "Run Unit Tests Only" \
            "Run Integration Tests Only" \
            "Back to Service Tests"
        
        read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
        
        case "$choice" in
            1)
                echo ""
                case "$backend" in
                    docker)
                        run_docker_test "agent"
                        ;;
                    proxmox)
                        bash "${SCRIPT_DIR}/test.sh" services agent
                        ;;
                esac
                pause
                ;;
            2)
                echo ""
                info "Running unit tests..."
                case "$backend" in
                    docker)
                        run_docker_test "agent:unit" "tests/unit"
                        ;;
                    proxmox)
                        bash "${SCRIPT_DIR}/test.sh" services agent "tests/unit"
                        ;;
                esac
                pause
                ;;
            3)
                handle_test_agent_integration
                ;;
            4)
                return 0
                ;;
            *)
                return 0
                ;;
        esac
    done
}

# Handle Agent integration tests with individual file selection
handle_test_agent_integration() {
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    while true; do
        echo ""
        header "Agent Integration Tests" 70
        
        # Show test status for integration test files
        echo ""
        echo -e "${BOLD}Test Status:${NC}"
        
        local test_files=(
            "api_agents" "api_conversations" "api_runs" "api_schedule"
            "api_scores" "api_streams" "api_workflows" "attachment_agent"
            "base_agent_document" "base_agent_web_search" "chat_agent"
            "chat_flow" "database_agent" "dispatcher_routing" "evaluator_crud"
            "insights_api" "personal_agents" "real_tools" "tool_crud"
            "ultimate_chat_flow" "weather_agent" "workflow_crud"
        )
        
        local passed_count=0
        local failed_count=0
        
        for test in "${test_files[@]}"; do
            local result=$(get_test_result "agent:integration:$test")
            if [[ "$result" == "passed" ]]; then
                ((passed_count++))
            elif [[ "$result" == "failed" ]]; then
                ((failed_count++))
            fi
        done
        
        if [[ $passed_count -gt 0 ]]; then
            echo -e "  ${GREEN}Passed: $passed_count${NC}"
        fi
        if [[ $failed_count -gt 0 ]]; then
            echo -e "  ${RED}Failed: $failed_count${NC}"
        fi
        if [[ $passed_count -eq 0 && $failed_count -eq 0 ]]; then
            echo -e "  ${DIM}Not run${NC}"
        fi
        
        echo ""
        menu "Agent Integration Test Options" \
            "Run All Integration Tests" \
            "Run Failed Tests Only" \
            "─────────────────────" \
            "Test: api_agents" \
            "Test: api_conversations" \
            "Test: api_runs" \
            "Test: api_schedule" \
            "Test: api_scores" \
            "Test: api_streams" \
            "Test: api_workflows" \
            "Test: attachment_agent" \
            "Test: base_agent_document" \
            "Test: base_agent_web_search" \
            "Test: chat_agent" \
            "Test: chat_flow" \
            "Test: database_agent" \
            "Test: dispatcher_routing" \
            "Test: evaluator_crud" \
            "Test: insights_api" \
            "Test: personal_agents" \
            "Test: real_tools" \
            "Test: tool_crud" \
            "Test: ultimate_chat_flow" \
            "Test: weather_agent" \
            "Test: workflow_crud" \
            "─────────────────────" \
            "Back to Agent Tests"
        
        read -p "$(echo -e "${BOLD}Select option [1-26]:${NC} ")" choice
        
        case "$choice" in
            1)
                echo ""
                info "Running all integration tests..."
                case "$backend" in
                    docker)
                        run_docker_test "agent:integration" "tests/integration"
                        ;;
                    proxmox)
                        bash "${SCRIPT_DIR}/test.sh" services agent "tests/integration"
                        ;;
                esac
                pause
                ;;
            2)
                echo ""
                local failed_tests=()
                for test in "${test_files[@]}"; do
                    if [[ "$(get_test_result "agent:integration:$test")" == "failed" ]]; then
                        failed_tests+=("$test")
                    fi
                done
                
                if [[ ${#failed_tests[@]} -eq 0 ]]; then
                    warn "No failed tests to rerun"
                else
                    info "Running failed tests: ${failed_tests[*]}"
                    for test in "${failed_tests[@]}"; do
                        echo ""
                        case "$backend" in
                            docker)
                                run_docker_test "agent:integration:$test" "tests/integration/test_${test}.py"
                                ;;
                            proxmox)
                                bash "${SCRIPT_DIR}/test.sh" services agent "tests/integration/test_${test}.py"
                                ;;
                        esac
                    done
                fi
                pause
                ;;
            3)
                # Separator, do nothing
                ;;
            4|5|6|7|8|9|10|11|12|13|14|15|16|17|18|19|20|21|22|23|24|25)
                local test_idx=$((choice - 4))
                local test_name="${test_files[$test_idx]}"
                echo ""
                info "Running test: $test_name"
                case "$backend" in
                    docker)
                        run_docker_test "agent:integration:$test" "tests/integration/test_${test_name}.py"
                        ;;
                    proxmox)
                        bash "${SCRIPT_DIR}/test.sh" services agent "tests/integration/test_${test_name}.py"
                        ;;
                esac
                pause
                ;;
            26)
                return 0
                ;;
            *)
                return 0
                ;;
        esac
    done
}

handle_test_apps() {
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    while true; do
        echo ""
        header "App Tests" 70
        echo ""
        
        menu "App Test Options" \
            "Run All App Tests" \
            "Test AI Portal" \
            "Test Agent Manager" \
            "Back to Test Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
        
        case "$choice" in
            1)
                echo ""
                warn "App tests not yet implemented"
                pause
                ;;
            2)
                echo ""
                warn "AI Portal tests not yet implemented"
                pause
                ;;
            3)
                echo ""
                warn "Agent Manager tests not yet implemented"
                pause
                ;;
            4)
                return 0
                ;;
            *)
                return 0
                ;;
        esac
    done
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
    echo "  - Development: Docker dev mode (volume mounts, npm-linked busibox-app)"
    echo "  - Demo:        Docker prod mode (apps from GitHub, for presentations)"
    echo "  - Staging:     10.96.201.x network (Docker or Proxmox)"
    echo "  - Production:  10.96.200.x network (Docker or Proxmox)"
    echo ""
    
    echo -e "${BOLD}Backends${NC}"
    echo "  - Docker:  Runs services in Docker containers"
    echo "  - Proxmox: Runs services in LXC containers with GPU support"
    echo ""
    echo "  Development and Demo are always Docker."
    echo "  Staging and Production can use either Docker or Proxmox."
    echo ""
    
    separator 70
    echo ""
    echo -e "${BOLD}Docker Commands (make targets)${NC}"
    echo ""
    echo "  ${CYAN}Build:${NC}"
    echo "    make docker-build              # Build all images"
    echo "    make docker-build SERVICE=X    # Build specific service"
    echo "    make docker-build ENV=demo     # Build with prod overlay"
    echo ""
    echo "  ${CYAN}Services:${NC}"
    echo "    make docker-up                 # Start all (development mode)"
    echo "    make docker-up ENV=demo        # Start all (demo/prod mode)"
    echo "    make docker-down               # Stop all services"
    echo "    make docker-restart            # Restart all services"
    echo "    make docker-ps                 # Show service status"
    echo "    make docker-logs               # View all logs"
    echo "    make docker-logs SERVICE=X     # View specific logs"
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
    
    # Initialize cache directory
    init_cache_dir
    
    # Kick off background status refresh (non-blocking)
    refresh_all_services_async "$env" "$backend" &
    
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
        local result=$?
        
        # Handle "return to status" from any submenu (s key)
        if [[ $result -eq $RETURN_TO_STATUS ]]; then
            # Force a status refresh
            info "Refreshing status..."
            run_quick_health_check "$env" "$backend"
        fi
        
        # Optional: Kick off background refresh for next menu display
        refresh_all_services_async "$env" "$backend" &
    done
}

# Run main function
main "$@"
