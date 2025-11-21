#!/usr/bin/env bash
#
# Busibox Deployment Script
#
# EXECUTION CONTEXT: Admin workstation or Proxmox host
# PURPOSE: Interactive Ansible deployment wrapper
#
# USAGE:
#   make deploy
#   OR
#   bash scripts/deploy.sh
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ANSIBLE_DIR="${REPO_ROOT}/provision/ansible"

# Source UI library
source "${SCRIPT_DIR}/lib/ui.sh"

# Display welcome
clear
box "Busibox Deployment" 70
echo ""
info "Deploy services using Ansible"
echo ""

# Check if Ansible is available
check_ansible() {
    if ! command -v ansible-playbook &>/dev/null; then
        error "Ansible is not installed"
        echo ""
        info "Install Ansible:"
        echo "  ${CYAN}apt install -y ansible${NC}   # Debian/Ubuntu"
        echo "  ${CYAN}brew install ansible${NC}     # macOS"
        return 1
    fi
    
    success "Ansible is available"
    return 0
}

# Deploy service
deploy_service() {
    local service="$1"
    local env="$2"
    local extra_args="${3:-}"
    
    local inv="inventory/${env}"
    
    cd "$ANSIBLE_DIR"
    
    info "Deploying $service to $env environment..."
    echo ""
    
    # Use make targets for common services
    case "$service" in
        all)
            make all INV="$inv" $extra_args || {
                error "Deployment failed"
                return 1
            }
            ;;
        *)
            make "$service" INV="$inv" $extra_args || {
                error "Deployment failed"
                return 1
            }
            ;;
    esac
    
    cd "$REPO_ROOT"
    
    echo ""
    success "Deployment completed successfully!"
    return 0
}

# vLLM deployment submenu
vllm_submenu() {
    local env="$1"
    
    while true; do
        clear
        box "vLLM Deployment - $env" 70
        echo ""
        info "Select vLLM instance to deploy"
        echo ""
        
        echo -e "  ${CYAN}1)${NC} Deploy All vLLM Instances    (ports 8000-8005)"
        echo -e "  ${CYAN}2)${NC} Deploy vLLM 8000              (individual)"
        echo -e "  ${CYAN}3)${NC} Deploy vLLM 8001              (individual)"
        echo -e "  ${CYAN}4)${NC} Deploy vLLM 8002              (individual)"
        echo -e "  ${CYAN}5)${NC} Deploy vLLM 8003              (individual)"
        echo -e "  ${CYAN}6)${NC} Deploy vLLM 8004              (individual)"
        echo -e "  ${CYAN}7)${NC} Deploy vLLM 8005              (individual)"
        echo -e "  ${CYAN}8)${NC} Back to Main Menu"
        echo ""
        
        read -p "Select option [1-8]: " choice
        echo ""
        
        case "$choice" in
            1)
                if confirm "Deploy ALL vLLM instances (8000-8005) to $env?"; then
                    deploy_service "vllm" "$env"
                fi
                pause
                ;;
            2)
                if confirm "Deploy vLLM 8000 (single instance) to $env?"; then
                    cd "$ANSIBLE_DIR"
                    info "Deploying vLLM 8000 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" site.yml --tags vllm_8000 || {
                        error "Deployment failed"
                    }
                    cd "$REPO_ROOT"
                    echo ""
                    success "Deployment completed successfully!"
                fi
                pause
                ;;
            3)
                if confirm "Deploy vLLM 8001 (single instance) to $env?"; then
                    cd "$ANSIBLE_DIR"
                    info "Deploying vLLM 8001 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" site.yml --tags vllm_8001 || {
                        error "Deployment failed"
                    }
                    cd "$REPO_ROOT"
                    echo ""
                    success "Deployment completed successfully!"
                fi
                pause
                ;;
            4)
                if confirm "Deploy vLLM 8002 (single instance) to $env?"; then
                    cd "$ANSIBLE_DIR"
                    info "Deploying vLLM 8002 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" site.yml --tags vllm_8002 || {
                        error "Deployment failed"
                    }
                    cd "$REPO_ROOT"
                    echo ""
                    success "Deployment completed successfully!"
                fi
                pause
                ;;
            5)
                if confirm "Deploy vLLM 8003 (single instance) to $env?"; then
                    cd "$ANSIBLE_DIR"
                    info "Deploying vLLM 8003 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" site.yml --tags vllm_8003 || {
                        error "Deployment failed"
                    }
                    cd "$REPO_ROOT"
                    echo ""
                    success "Deployment completed successfully!"
                fi
                pause
                ;;
            6)
                if confirm "Deploy vLLM 8004 (single instance) to $env?"; then
                    cd "$ANSIBLE_DIR"
                    info "Deploying vLLM 8004 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" site.yml --tags vllm_8004 || {
                        error "Deployment failed"
                    }
                    cd "$REPO_ROOT"
                    echo ""
                    success "Deployment completed successfully!"
                fi
                pause
                ;;
            7)
                if confirm "Deploy vLLM 8005 (single instance) to $env?"; then
                    cd "$ANSIBLE_DIR"
                    info "Deploying vLLM 8005 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" site.yml --tags vllm_8005 || {
                        error "Deployment failed"
                    }
                    cd "$REPO_ROOT"
                    echo ""
                    success "Deployment completed successfully!"
                fi
                pause
                ;;
            8)
                return 0
                ;;
            *)
                error "Invalid choice"
                pause
                ;;
        esac
    done
}

# Verify deployment
verify_deployment() {
    local env="$1"
    local inv="inventory/${env}"
    
    header "Verifying Deployment" 70
    
    cd "$ANSIBLE_DIR"
    
    echo ""
    info "Running health checks..."
    echo ""
    
    make verify INV="$inv" || {
        error "Verification failed"
        cd "$REPO_ROOT"
        return 1
    }
    
    cd "$REPO_ROOT"
    
    echo ""
    success "All services are healthy!"
    return 0
}

# Deployment menu
deployment_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Deploy Services - $env Environment" \
            "Deploy All Services" \
            "Deploy Core Services (files, pg, milvus)" \
            "Deploy ColPali (visual embeddings)" \
            "Deploy vLLM (LLM inference)" \
            "Deploy LiteLLM" \
            "Deploy Ingest Service" \
            "Deploy Search API" \
            "Deploy Agent API" \
            "Deploy Apps (AI Portal)" \
            "Deploy OpenWebUI" \
            "Verify Deployment (Health Checks)" \
            "Back to Main Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-12]:${NC} ")" choice
        
        case $choice in
            1)
                if confirm "Deploy ALL services to $env?"; then
                    deploy_service "all" "$env"
                fi
                pause
                ;;
            2)
                header "Deploying Core Services" 70
                echo ""
                if confirm "Deploy files, pg, and milvus to $env?"; then
                    deploy_service "files" "$env" && \
                    deploy_service "pg" "$env" && \
                    deploy_service "milvus" "$env"
                fi
                pause
                ;;
            3)
                if confirm "Deploy ColPali (visual embeddings) to $env?"; then
                    deploy_service "colpali" "$env"
                fi
                pause
                ;;
            4)
                vllm_submenu "$env"
                ;;
            5)
                if confirm "Deploy LiteLLM to $env?"; then
                    deploy_service "litellm" "$env"
                fi
                pause
                ;;
            6)
                if confirm "Deploy Ingest Service to $env?"; then
                    deploy_service "ingest" "$env"
                fi
                pause
                ;;
            7)
                if confirm "Deploy Search API to $env?"; then
                    deploy_service "search-api" "$env"
                fi
                pause
                ;;
            8)
                if confirm "Deploy Agent API to $env?"; then
                    deploy_service "agent" "$env"
                fi
                pause
                ;;
            9)
                if confirm "Deploy Apps to $env?"; then
                    deploy_service "apps" "$env"
                fi
                pause
                ;;
            10)
                if confirm "Deploy OpenWebUI to $env?"; then
                    deploy_service "openwebui" "$env"
                fi
                pause
                ;;
            11)
                verify_deployment "$env"
                pause
                ;;
            12)
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-12."
                ;;
        esac
    done
}

# Main menu
main() {
    # Check Ansible
    if ! check_ansible; then
        exit 1
    fi
    
    echo ""
    
    # Select environment
    ENV=$(select_environment)
    
    success "Selected environment: $ENV"
    
    # Show deployment menu
    deployment_menu "$ENV"
    
    echo ""
    box "Deployment Complete" 70
    echo ""
    
    summary "Next Steps" \
        "Run tests: ${CYAN}make test${NC}" \
        "View logs: ${CYAN}ssh root@<container-ip>${NC}" \
        "Check services: ${CYAN}systemctl status <service>${NC}"
    
    echo ""
}

# Run main function
main

exit 0

