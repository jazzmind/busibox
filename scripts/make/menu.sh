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
                # Check if backend is saved, otherwise we'll need to ask
                backend=$(get_backend "staging")
                if [[ -z "$backend" ]]; then
                    backend=$(select_backend)
                fi
                ;;
            prod|production)
                env="production"
                backend=$(get_backend "production")
                if [[ -z "$backend" ]]; then
                    backend=$(select_backend)
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
        read -t 3 -n 1 change_choice || true
        
        if [[ "$change_choice" == "c" ]]; then
            select_and_save_environment
        fi
    else
        # First run - need to select environment
        select_and_save_environment
    fi
}

# Select environment and backend, save to state
select_and_save_environment() {
    local result env backend
    
    result=$(select_environment_with_backend)
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
    
    run_health_check "$env" "$backend"
    display_health_check "$env" "$backend"
    
    pause
}

# Show the main menu
show_main_menu() {
    local env backend status last_cmd last_ago choice
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    status=$(get_install_status)
    last_cmd=$(get_last_command)
    last_ago=$(get_last_command_ago)
    
    clear
    box "Busibox Control Panel" 70
    
    # Status bar
    status_bar "$env" "$backend" "$status" 70
    
    # Quick actions (if we have a last command)
    if [[ -n "$last_cmd" ]]; then
        quick_menu "$last_cmd" "$last_ago"
    fi
    
    # Dynamic menu based on status
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
        deploy)
            handle_deploy
            ;;
        test)
            handle_test
            ;;
        change_env)
            select_and_save_environment
            # Re-run health check for new environment
            run_health_check "$env" "$backend"
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

handle_install() {
    local env backend
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    echo ""
    header "Install/Setup" 70
    
    case "$backend" in
        docker)
            info "Setting up Docker environment..."
            echo ""
            
            # Check if .env.local exists
            if [[ ! -f "${REPO_ROOT}/.env.local" ]]; then
                if [[ -f "${REPO_ROOT}/env.local.example" ]]; then
                    info "Creating .env.local from env.local.example..."
                    cp "${REPO_ROOT}/env.local.example" "${REPO_ROOT}/.env.local"
                    success ".env.local created"
                    warn "Edit .env.local to add your API keys (OPENAI_API_KEY, etc.)"
                fi
            else
                success ".env.local already exists"
            fi
            
            # Check/generate SSL certificates
            if [[ ! -f "${REPO_ROOT}/ssl/localhost.crt" ]] || [[ ! -f "${REPO_ROOT}/ssl/localhost.key" ]]; then
                info "Generating SSL certificates..."
                bash "${REPO_ROOT}/scripts/setup/generate-local-ssl.sh" || {
                    warn "SSL certificate generation failed (may need to run manually)"
                }
            else
                success "SSL certificates already exist"
            fi
            
            # Build Docker images
            echo ""
            if confirm "Build Docker images now?"; then
                local cmd="make docker-build"
                save_last_command "$cmd"
                (cd "$REPO_ROOT" && make docker-build)
            fi
            
            # Update status
            set_install_status "installed"
            success "Docker setup complete!"
            ;;
            
        proxmox)
            info "Running Proxmox setup..."
            echo ""
            
            # Check if on Proxmox host
            if ! command -v pct &>/dev/null; then
                warn "Not running on Proxmox host"
                info "For full Proxmox setup, run this on your Proxmox host"
                echo ""
                info "Alternatively, you can:"
                echo "  1. SSH to your Proxmox host"
                echo "  2. Clone this repository"
                echo "  3. Run: make setup"
                echo ""
            else
                # Run the existing setup script
                bash "${SCRIPT_DIR}/setup.sh"
            fi
            
            set_install_status "installed"
            ;;
    esac
    
    pause
}

handle_configure() {
    local env backend
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    echo ""
    header "Configure" 70
    
    # Run the existing configure script
    local cmd
    if [[ "$backend" == "docker" ]]; then
        cmd="bash ${SCRIPT_DIR}/configure.sh"
    else
        cmd="bash ${SCRIPT_DIR}/configure.sh"
    fi
    
    save_last_command "$cmd"
    bash "${SCRIPT_DIR}/configure.sh"
    
    # Update status
    set_install_status "configured"
    
    pause
}

handle_deploy() {
    local env backend
    
    env=$(get_environment)
    backend=$(get_backend "$env")
    
    echo ""
    header "Deploy" 70
    
    case "$backend" in
        docker)
            echo ""
            menu "Docker Deployment Options" \
                "Start All Services" \
                "Start Data Services Only (postgres, redis, milvus, minio)" \
                "Start API Services Only (authz, ingest, search, agent)" \
                "Restart All Services" \
                "Stop All Services" \
                "Back to Main Menu"
            
            read -p "$(echo -e "${BOLD}Select option [1-6]:${NC} ")" deploy_choice
            
            local cmd=""
            case "$deploy_choice" in
                1)
                    cmd="make docker-up"
                    save_last_command "$cmd"
                    (cd "$REPO_ROOT" && make docker-up)
                    set_install_status "deployed"
                    ;;
                2)
                    cmd="make docker-up SERVICE='postgres redis milvus minio'"
                    save_last_command "$cmd"
                    (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml --env-file .env.local up -d postgres redis milvus minio)
                    ;;
                3)
                    cmd="make docker-up SERVICE='authz-api ingest-api search-api agent-api'"
                    save_last_command "$cmd"
                    (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml --env-file .env.local up -d authz-api ingest-api search-api agent-api)
                    set_install_status "deployed"
                    ;;
                4)
                    cmd="make docker-restart"
                    save_last_command "$cmd"
                    (cd "$REPO_ROOT" && make docker-restart)
                    ;;
                5)
                    cmd="make docker-down"
                    save_last_command "$cmd"
                    (cd "$REPO_ROOT" && make docker-down)
                    set_install_status "configured"
                    ;;
                6)
                    return 0
                    ;;
            esac
            ;;
            
        proxmox)
            # Use existing deploy script
            bash "${SCRIPT_DIR}/deploy.sh"
            set_install_status "deployed"
            ;;
    esac
    
    pause
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
    echo ""
    header "Busibox Help" 70
    
    echo -e "${BOLD}Overview${NC}"
    echo "  Busibox is a multi-service AI platform with document ingestion,"
    echo "  vector search, and agent capabilities."
    echo ""
    
    echo -e "${BOLD}Environments${NC}"
    echo "  - Local (Docker): Development on your machine"
    echo "  - Test: Test environment (10.96.201.x network)"
    echo "  - Production: Production environment (10.96.200.x network)"
    echo ""
    
    echo -e "${BOLD}Backends${NC}"
    echo "  - Docker: Runs all services in Docker containers"
    echo "  - Proxmox: Runs services in LXC containers with GPU support"
    echo ""
    
    echo -e "${BOLD}Quick Commands${NC}"
    echo "  make                          # Interactive menu"
    echo "  make ENV=test                 # Start with test environment"
    echo "  make docker-up                # Start Docker services"
    echo "  make docker-down              # Stop Docker services"
    echo "  make test-docker SERVICE=all  # Run all tests"
    echo "  make help                     # Show full help"
    echo ""
    
    echo -e "${BOLD}State File${NC}"
    echo "  Environment preferences are saved to .busibox-state"
    echo "  This file tracks your environment, backend, and last command."
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
    
    # Run initial health check (quiet)
    run_health_check "$env" "$backend"
    
    # Main menu loop
    while true; do
        local selection
        selection=$(show_main_menu)
        
        handle_menu_selection "$selection"
    done
}

# Run main function
main "$@"
