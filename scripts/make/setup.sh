#!/usr/bin/env bash
#
# Busibox Setup Script
#
# Description:
#   Universal setup script that handles both Docker and Proxmox backends.
#   For Docker: Sets up local development environment
#   For Proxmox: Guides through LXC container creation and configuration
#
# Usage:
#   make setup
#   bash scripts/make/setup.sh
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"

# ============================================================================
# Docker Setup
# ============================================================================
setup_docker() {
    header "Docker Environment Setup" 70
    
    echo "This will set up your local Docker development environment."
    echo ""
    
    # Step 1: Check Docker
    echo ""
    info "Step 1: Checking Docker..."
    if ! command -v docker &>/dev/null; then
        error "Docker is not installed"
        echo ""
        echo "Please install Docker:"
        echo "  macOS:   brew install --cask docker"
        echo "  Ubuntu:  sudo apt install docker.io docker-compose-plugin"
        echo "  Windows: https://docs.docker.com/desktop/windows/install/"
        return 1
    fi
    
    if ! docker info &>/dev/null; then
        error "Docker daemon is not running"
        echo "Please start Docker Desktop or the Docker service"
        return 1
    fi
    success "Docker is installed and running"
    
    # Step 2: Create .env.local
    echo ""
    info "Step 2: Setting up environment file..."
    if [[ ! -f "${REPO_ROOT}/.env.local" ]]; then
        if [[ -f "${REPO_ROOT}/env.local.example" ]]; then
            cp "${REPO_ROOT}/env.local.example" "${REPO_ROOT}/.env.local"
            success "Created .env.local from template"
            warn "Edit .env.local to add your API keys (OPENAI_API_KEY, etc.)"
        else
            error "env.local.example not found"
            return 1
        fi
    else
        success ".env.local already exists"
    fi
    
    # Step 3: Generate SSL certificates
    echo ""
    info "Step 3: Setting up SSL certificates..."
    if [[ ! -f "${REPO_ROOT}/ssl/localhost.crt" ]] || [[ ! -f "${REPO_ROOT}/ssl/localhost.key" ]]; then
        if [[ -f "${REPO_ROOT}/scripts/setup/generate-local-ssl.sh" ]]; then
            bash "${REPO_ROOT}/scripts/setup/generate-local-ssl.sh"
            success "SSL certificates generated"
        else
            warn "SSL generation script not found - will be created on docker-build"
        fi
    else
        success "SSL certificates already exist"
    fi
    
    # Step 4: Build Docker images
    echo ""
    if confirm "Step 4: Build Docker images now?"; then
        info "Building Docker images (this may take a few minutes)..."
        (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml --env-file .env.local build)
        success "Docker images built successfully"
    else
        info "Skipping image build. Run 'make docker-build' when ready."
    fi
    
    # Step 5: Start services (optional)
    echo ""
    if confirm "Step 5: Start Docker services now?"; then
        info "Starting Docker services..."
        (cd "$REPO_ROOT" && docker compose -f docker-compose.local.yml --env-file .env.local up -d)
        success "Docker services started"
        echo ""
        info "Check status with: make docker-ps"
        info "View logs with: make docker-logs"
    else
        info "Skipping service start. Run 'make docker-up' when ready."
    fi
    
    # Update state
    set_install_status "installed"
    
    echo ""
    header "Docker Setup Complete" 70
    echo ""
    summary "Setup Complete" \
        "Docker environment configured" \
        ".env.local created" \
        "SSL certificates ready"
    echo ""
    info "Next steps:"
    echo "  1. Edit .env.local with your API keys"
    echo "  2. Run 'make docker-up' to start services"
    echo "  3. Run 'make test-docker SERVICE=authz' to verify"
    echo ""
}

# ============================================================================
# Proxmox Setup
# ============================================================================
setup_proxmox() {
    header "Proxmox Environment Setup" 70
    
    # Check if we're on Proxmox
    if ! command -v pct &>/dev/null; then
        warn "Not running on Proxmox host"
        echo ""
        echo "For Proxmox setup, you have two options:"
        echo ""
        echo "  1. Run this script on your Proxmox host:"
        echo "     ssh root@proxmox"
        echo "     cd /path/to/busibox"
        echo "     make setup"
        echo ""
        echo "  2. Use Docker instead (works anywhere):"
        echo "     make ENV=local"
        echo ""
        
        if confirm "Would you like to switch to Docker setup instead?"; then
            set_backend "$(get_environment)" "docker"
            setup_docker
            return $?
        fi
        return 1
    fi
    
    # Check if running as root
    if [[ $EUID -ne 0 ]]; then
        error "Proxmox setup must be run as root"
        echo "Please run: sudo make setup"
        return 1
    fi
    
    success "Running on Proxmox host as root"
    echo ""
    
    # The detailed Proxmox setup is in provision/pct
    # This provides a simplified wrapper
    
    echo "This will set up Busibox on Proxmox LXC containers."
    echo ""
    echo "Steps:"
    echo "  1. Configure Proxmox host (SSH keys, templates, etc.)"
    echo "  2. Create LXC containers"
    echo "  3. Configure GPU passthrough (if available)"
    echo "  4. Run initial Ansible deployment"
    echo ""
    
    if ! confirm "Ready to begin?"; then
        info "Setup cancelled"
        return 0
    fi
    
    # Run the detailed setup script if it exists
    local detailed_setup="${REPO_ROOT}/provision/pct/setup-all.sh"
    if [[ -f "$detailed_setup" ]]; then
        bash "$detailed_setup"
    else
        # Fallback to step-by-step
        echo ""
        info "Running Proxmox host setup..."
        
        # Check for required scripts
        local host_setup="${REPO_ROOT}/provision/pct/host/setup-proxmox-host.sh"
        if [[ -f "$host_setup" ]]; then
            if confirm "Run host configuration?"; then
                bash "$host_setup"
            fi
        fi
        
        local container_create="${REPO_ROOT}/provision/pct/containers/create_lxc_base.sh"
        if [[ -f "$container_create" ]]; then
            echo ""
            if confirm "Create LXC containers?"; then
                echo "Select environment:"
                echo "  1) Staging (containers 300-310)"
                echo "  2) Production (containers 200-210)"
                read -p "Choice [1-2]: " env_choice
                
                local mode="staging"
                [[ "$env_choice" == "2" ]] && mode="production"
                
                bash "$container_create" "$mode"
            fi
        fi
    fi
    
    # Update state
    set_install_status "installed"
    
    echo ""
    header "Proxmox Setup Complete" 70
    echo ""
    info "Next steps:"
    echo "  1. Run 'make configure' to configure services"
    echo "  2. Run 'make deploy' to deploy with Ansible"
    echo "  3. Run 'make test' to verify deployment"
    echo ""
}

# ============================================================================
# Main
# ============================================================================
main() {
    clear
    box "Busibox Setup" 70
    echo ""
    
    # Get current environment and backend
    local env backend
    env=$(get_environment)
    backend=$(get_backend "$env" 2>/dev/null || echo "")
    
    # If no environment set, ask
    if [[ -z "$env" ]]; then
        local result
        result=$(select_environment_with_backend)
        env="${result%%:*}"
        backend="${result#*:}"
        set_environment "$env"
        set_backend "$env" "$backend"
    fi
    
    # If no backend set for non-local environments, ask
    if [[ "$env" != "local" ]] && [[ -z "$backend" ]]; then
        backend=$(select_backend)
        set_backend "$env" "$backend"
    fi
    
    # Local always uses Docker
    if [[ "$env" == "local" ]]; then
        backend="docker"
        set_backend "$env" "$backend"
    fi
    
    success "Environment: $env ($backend)"
    echo ""
    
    # Run appropriate setup
    case "$backend" in
        docker)
            setup_docker
            ;;
        proxmox)
            setup_proxmox
            ;;
        *)
            error "Unknown backend: $backend"
            return 1
            ;;
    esac
}

main "$@"
