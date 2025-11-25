#!/usr/bin/env bash
#
# Busibox Setup Script
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Interactive setup for Proxmox host and LXC container creation
#
# This script combines:
# 1. Proxmox host setup (NVIDIA drivers, ZFS, dependencies)
# 2. LXC container creation
#
# USAGE:
#   make setup
#   OR
#   bash scripts/setup.sh
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Source UI library
source "${SCRIPT_DIR}/lib/ui.sh"

# Display welcome
clear
box "Busibox Setup" 70
echo ""
info "This script will set up your Proxmox host and create LXC containers"
echo ""

# Check if running on Proxmox
if ! check_proxmox; then
    exit 1
fi

success "Running on Proxmox host"
echo ""

# Main setup flow
main() {
    # Step 1: Host Setup
    progress 1 3 "Proxmox Host Setup"
    echo ""
    info "Setting up Proxmox host (NVIDIA drivers, ZFS, dependencies)..."
    echo ""
    
    if confirm "Run Proxmox host setup?"; then
        bash "${REPO_ROOT}/provision/pct/host/setup-proxmox-host.sh" || {
            error "Host setup failed"
            exit 1
        }
        success "Host setup completed"
    else
        warn "Skipping host setup"
    fi
    
    echo ""
    pause
    
    # Step 2: Environment Selection
    progress 2 3 "Environment Selection"
    ENV=$(select_environment)
    
    success "Selected environment: $ENV"
    echo ""
    
    # Step 3: Container Creation
    progress 3 3 "LXC Container Creation"
    echo ""
    info "Creating LXC containers for $ENV environment..."
    echo ""
    
    if confirm "Create LXC containers?"; then
        # Ask about Ollama
        local OLLAMA_FLAG=""
        echo ""
        if confirm "Include optional Ollama container?" "n"; then
            OLLAMA_FLAG="--with-ollama"
            info "Will create containers with Ollama"
        else
            info "Will create containers without Ollama"
        fi
        
        echo ""
        info "Starting container creation..."
        echo ""
        
        bash "${REPO_ROOT}/provision/pct/containers/create_lxc_base.sh" "$ENV" $OLLAMA_FLAG || {
            error "Container creation failed"
            exit 1
        }
        
        success "Containers created successfully"
    else
        warn "Skipping container creation"
    fi
    
    # Summary
    echo ""
    echo ""
    box "Setup Complete!" 70
    echo ""
    
    summary "Completed Tasks" \
        "Proxmox host configured" \
        "LXC containers created for $ENV environment" \
        "Ready for deployment"
    
    echo ""
    header "Next Steps" 70
    echo ""
    list_item "info" "Configure models and GPUs:"
    echo "    ${CYAN}make configure${NC}"
    echo ""
    list_item "info" "Deploy services:"
    echo "    ${CYAN}make deploy${NC}"
    echo ""
    list_item "info" "Run tests:"
    echo "    ${CYAN}make test${NC}"
    echo ""
}

# Run main function
main

exit 0

