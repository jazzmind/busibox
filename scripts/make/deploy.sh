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
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ANSIBLE_DIR="${REPO_ROOT}/provision/ansible"

# Source UI library
source "${REPO_ROOT}/scripts/lib/ui.sh"

# vLLM mode for test environment: "alias" (use production) or "deploy" (own container)
VLLM_MODE="alias"

# Track if IP aliasing has been configured
ALIAS_CONFIGURED=false

# Non-interactive mode flag
NON_INTERACTIVE=false

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

# Detect vault password method
get_vault_flags() {
    local vault_pass_file="$HOME/.vault_pass"
    
    if [ -f "$vault_pass_file" ]; then
        echo "--vault-password-file $vault_pass_file"
    else
        echo "--ask-vault-pass"
    fi
}

# Setup vLLM IP aliasing on Proxmox host
setup_vllm_alias() {
    local action="$1"  # "enable" or "disable" or "status"
    
    info "Configuring vLLM IP aliasing on Proxmox host..."
    echo ""
    
    # Get Proxmox host from inventory
    local proxmox_host
    proxmox_host=$(grep -E "^proxmox_host:" "${ANSIBLE_DIR}/inventory/production/group_vars/all/00-main.yml" 2>/dev/null | awk '{print $2}' || echo "proxmox")
    
    if [ -z "$proxmox_host" ] || [ "$proxmox_host" = "proxmox" ]; then
        # Try to get from ~/.ssh/config or use default
        proxmox_host="proxmox"
    fi
    
    # Check if we can reach the Proxmox host
    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "root@${proxmox_host}" "echo 'connected'" &>/dev/null; then
        error "Cannot connect to Proxmox host ($proxmox_host)"
        info "Make sure you can SSH to Proxmox: ssh root@${proxmox_host}"
        return 1
    fi
    
    # Copy and run the aliasing script
    local script_path="${REPO_ROOT}/provision/pct/host/setup-vllm-alias.sh"
    
    if [ ! -f "$script_path" ]; then
        error "Aliasing script not found: $script_path"
        return 1
    fi
    
    # Copy script to Proxmox host and execute
    scp -q "$script_path" "root@${proxmox_host}:/tmp/setup-vllm-alias.sh"
    ssh "root@${proxmox_host}" "chmod +x /tmp/setup-vllm-alias.sh && /tmp/setup-vllm-alias.sh $action"
    local result=$?
    
    if [ $result -eq 0 ]; then
        if [ "$action" = "enable" ]; then
            ALIAS_CONFIGURED=true
            success "vLLM IP aliasing configured successfully"
        elif [ "$action" = "disable" ]; then
            ALIAS_CONFIGURED=false
            success "vLLM IP aliasing disabled"
        fi
    else
        error "Failed to configure vLLM IP aliasing"
        return 1
    fi
    
    return 0
}

# Select vLLM mode for test environment
select_vllm_mode() {
    echo ""
    box "vLLM Configuration" 70
    echo ""
    info "For TEST environment, you can either:"
    echo ""
    echo -e "  ${CYAN}1)${NC} Alias to production vLLM ${GREEN}(default, saves resources)${NC}"
    echo -e "     Test services will use production vLLM endpoints"
    echo -e "     ${DIM}(Configures IP aliasing on Proxmox host)${NC}"
    echo ""
    echo -e "  ${CYAN}2)${NC} Deploy test vLLM container ${YELLOW}(isolated testing)${NC}"
    echo -e "     Requires GPU resources and significant memory"
    echo ""
    echo -e "  ${CYAN}3)${NC} Check current aliasing status"
    echo ""
    
    read -p "$(echo -e "${BOLD}Select option [1-3, default=1]:${NC} ")" vllm_choice
    echo ""
    
    case "$vllm_choice" in
        2)
            VLLM_MODE="deploy"
            warn "Test environment will deploy its own vLLM container"
            info "This requires GPU resources and significant memory"
            
            # Offer to disable aliasing if it was previously enabled
            if confirm "Disable vLLM aliasing (if enabled)?"; then
                setup_vllm_alias "disable" || true
            fi
            ;;
        3)
            setup_vllm_alias "status"
            echo ""
            # Re-run selection
            select_vllm_mode
            return
            ;;
        *)
            VLLM_MODE="alias"
            success "Test environment will use production vLLM"
            echo ""
            
            # Set up IP aliasing
            if confirm "Configure IP aliasing on Proxmox host now?"; then
                if setup_vllm_alias "enable"; then
                    info "Test LiteLLM can now be deployed"
                else
                    warn "IP aliasing failed - LiteLLM deployment may fail"
                    if ! confirm "Continue anyway?"; then
                        VLLM_MODE="deploy"
                        warn "Switched to deploy mode"
                    fi
                fi
            else
                info "You can configure IP aliasing later from the LLM Services menu"
            fi
            ;;
    esac
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
                    local vault_flags="$(get_vault_flags)"
                    info "Deploying vLLM 8000 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" -l vllm site.yml --tags vllm_8000 $vault_flags || {
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
                    local vault_flags="$(get_vault_flags)"
                    info "Deploying vLLM 8001 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" -l vllm site.yml --tags vllm_8001 $vault_flags || {
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
                    local vault_flags="$(get_vault_flags)"
                    info "Deploying vLLM 8002 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" -l vllm site.yml --tags vllm_8002 $vault_flags || {
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
                    local vault_flags="$(get_vault_flags)"
                    info "Deploying vLLM 8003 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" -l vllm site.yml --tags vllm_8003 $vault_flags || {
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
                    local vault_flags="$(get_vault_flags)"
                    info "Deploying vLLM 8004 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" -l vllm site.yml --tags vllm_8004 $vault_flags || {
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
                    local vault_flags="$(get_vault_flags)"
                    info "Deploying vLLM 8005 to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" -l vllm site.yml --tags vllm_8005 $vault_flags || {
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

# Deploy a single app with branch/release selection
deploy_single_app() {
    local app_name="$1"
    local app_display="$2"
    local env="$3"
    
    while true; do
        clear
        box "Deploy $app_display - $env" 70
        echo ""
        info "Select deployment method"
        echo ""
        
        echo -e "  ${CYAN}1)${NC} Deploy from Branch (default: main)"
        echo -e "  ${CYAN}2)${NC} Deploy from Release (default: latest)"
        echo -e "  ${CYAN}3)${NC} Cancel"
        echo ""
        
        read -p "Select option [1-3]: " method_choice
        echo ""
        
        case "$method_choice" in
            1)
                read -p "Enter branch name [main]: " branch_name
                branch_name="${branch_name:-main}"
                
                if confirm "Deploy $app_display from branch '$branch_name' to $env?"; then
                    cd "$ANSIBLE_DIR"
                    local vault_flags="$(get_vault_flags)"
                    info "Deploying $app_display from branch '$branch_name' to $env environment..."
                    echo ""
                    ansible-playbook -i "inventory/${env}/hosts.yml" site.yml --tags apps \
                        --extra-vars "deploy_app=${app_name}" \
                        --extra-vars "deploy_branch=${branch_name}" \
                        --extra-vars "deploy_from_branch=true" \
                        $vault_flags || {
                        error "Deployment failed"
                    }
                    cd "$REPO_ROOT"
                    echo ""
                    success "Deployment completed successfully!"
                fi
                return 0
                ;;
            2)
                read -p "Enter release tag [latest]: " release_tag
                release_tag="${release_tag:-latest}"
                
                if confirm "Deploy $app_display from release '$release_tag' to $env?"; then
                    cd "$ANSIBLE_DIR"
                    local vault_flags="$(get_vault_flags)"
                    info "Deploying $app_display from release '$release_tag' to $env environment..."
                    echo ""
                    
                    if [ "$release_tag" = "latest" ]; then
                        # Use standard release deployment (deploywatch gets latest)
                        ansible-playbook -i "inventory/${env}/hosts.yml" site.yml --tags apps \
                            --extra-vars "deploy_app=${app_name}" \
                            $vault_flags || {
                            error "Deployment failed"
                        }
                    else
                        # Deploy specific release tag (use branch deployment method with tag)
                        ansible-playbook -i "inventory/${env}/hosts.yml" site.yml --tags apps \
                            --extra-vars "deploy_app=${app_name}" \
                            --extra-vars "deploy_branch=${release_tag}" \
                            --extra-vars "deploy_from_branch=true" \
                            $vault_flags || {
                            error "Deployment failed"
                        }
                    fi
                    cd "$REPO_ROOT"
                    echo ""
                    success "Deployment completed successfully!"
                fi
                return 0
                ;;
            3)
                return 0
                ;;
            *)
                error "Invalid choice. Please select 1-3."
                pause
                # Loop continues
                ;;
        esac
    done
}

# Core Services submenu (files, database, vectorstore)
core_services_menu() {
    local env="$1"
    
    while true; do
        clear
        box "Core Services - $env" 70
        echo ""
        info "Select core service to deploy"
        echo ""
        
        echo -e "  ${CYAN}1)${NC} Deploy All Core Services"
        echo -e "  ${CYAN}2)${NC} Deploy Authz (RLS token service)"
        echo -e "  ${CYAN}3)${NC} Deploy Files (MinIO)"
        echo -e "  ${CYAN}4)${NC} Deploy Database (PostgreSQL)"
        echo -e "  ${CYAN}5)${NC} Deploy Vectorstore (Milvus)"
        echo -e "  ${CYAN}6)${NC} Back"
        echo ""
        
        read -p "Select option [1-6]: " choice
        echo ""
        
        case "$choice" in
            1)
                if confirm "Deploy ALL core services (files, pg, milvus, authz) to $env?"; then
                    deploy_service "files" "$env" && \
                    deploy_service "pg" "$env" && \
                    deploy_service "milvus" "$env" && \
                    deploy_service "authz" "$env"
                fi
                pause
                ;;
            2)
                if confirm "Deploy Authz (RLS token service) to $env?"; then
                    deploy_service "authz" "$env"
                fi
                pause
                ;;
            3)
                if confirm "Deploy Files (MinIO) to $env?"; then
                    deploy_service "files" "$env"
                fi
                pause
                ;;
            4)
                if confirm "Deploy Database (PostgreSQL) to $env?"; then
                    deploy_service "pg" "$env"
                fi
                pause
                ;;
            5)
                if confirm "Deploy Vectorstore (Milvus) to $env?"; then
                    deploy_service "milvus" "$env"
                fi
                pause
                ;;
            6)
                return 0
                ;;
            *)
                error "Invalid choice"
                pause
                ;;
        esac
    done
}

# LLM Services submenu (vllm, litellm, colpali)
llm_services_menu() {
    local env="$1"
    
    while true; do
        clear
        box "LLM Services - $env" 70
        echo ""
        
        # Show vLLM mode for test environment
        if [ "$env" = "test" ]; then
            if [ "$VLLM_MODE" = "alias" ]; then
                echo -e "  ${GREEN}vLLM Mode: Aliased to Production${NC}"
                if [ "$ALIAS_CONFIGURED" = "true" ]; then
                    echo -e "  ${GREEN}IP Aliasing: Configured ✓${NC}"
                else
                    echo -e "  ${YELLOW}IP Aliasing: Not configured (may be needed)${NC}"
                fi
            else
                echo -e "  ${YELLOW}vLLM Mode: Deploy Test Container${NC}"
            fi
            echo ""
        fi
        
        info "Select LLM service to deploy"
        echo ""
        
        echo -e "  ${CYAN}1)${NC} Deploy All LLM Services"
        echo -e "  ${CYAN}2)${NC} Deploy vLLM & Models"
        echo -e "  ${CYAN}3)${NC} Deploy ColPali (visual embeddings)"
        echo -e "  ${CYAN}4)${NC} Deploy LiteLLM (gateway)"
        if [ "$env" = "test" ]; then
            echo -e "  ${CYAN}5)${NC} Configure IP Aliasing"
            echo -e "  ${CYAN}6)${NC} Change vLLM Mode"
            echo -e "  ${CYAN}7)${NC} Back"
        else
            echo -e "  ${CYAN}5)${NC} Back"
        fi
        echo ""
        
        local max_option=5
        [ "$env" = "test" ] && max_option=7
        
        read -p "Select option [1-$max_option]: " choice
        echo ""
        
        case "$choice" in
            1)
                if [ "$env" = "test" ] && [ "$VLLM_MODE" = "alias" ]; then
                    # Ensure IP aliasing is configured before deploying LiteLLM
                    if [ "$ALIAS_CONFIGURED" != "true" ]; then
                        warn "IP aliasing not yet configured"
                        if confirm "Configure IP aliasing now?"; then
                            setup_vllm_alias "enable" || {
                                error "IP aliasing failed - cannot deploy LiteLLM"
                                pause
                                continue
                            }
                        else
                            error "IP aliasing required for alias mode"
                            pause
                            continue
                        fi
                    fi
                    
                    if confirm "Deploy LLM services to $env? (vLLM aliased to production)"; then
                        info "Skipping vLLM/ColPali deployment (aliased to production)"
                        echo ""
                        deploy_service "litellm" "$env"
                    fi
                else
                    if confirm "Deploy ALL LLM services (vLLM, ColPali, LiteLLM) to $env?"; then
                        deploy_service "vllm" "$env" && \
                        deploy_service "colpali" "$env" && \
                        deploy_service "litellm" "$env"
                    fi
                fi
                pause
                ;;
            2)
                if [ "$env" = "test" ] && [ "$VLLM_MODE" = "alias" ]; then
                    warn "vLLM is aliased to production in test mode"
                    info "To deploy a test vLLM, change vLLM mode first (option 6)"
                    pause
                else
                    vllm_submenu "$env"
                fi
                ;;
            3)
                if [ "$env" = "test" ] && [ "$VLLM_MODE" = "alias" ]; then
                    warn "ColPali is aliased to production in test mode"
                    info "To deploy test ColPali, change vLLM mode first (option 6)"
                    pause
                else
                    if confirm "Deploy ColPali (visual embeddings) to $env?"; then
                        deploy_service "colpali" "$env"
                    fi
                    pause
                fi
                ;;
            4)
                # Ensure IP aliasing is configured before deploying LiteLLM in alias mode
                if [ "$env" = "test" ] && [ "$VLLM_MODE" = "alias" ]; then
                    if [ "$ALIAS_CONFIGURED" != "true" ]; then
                        warn "IP aliasing not yet configured"
                        if confirm "Configure IP aliasing now?"; then
                            setup_vllm_alias "enable" || {
                                error "IP aliasing failed - LiteLLM may not work correctly"
                                if ! confirm "Continue anyway?"; then
                                    pause
                                    continue
                                fi
                            }
                        fi
                    fi
                fi
                
                if confirm "Deploy LiteLLM (gateway) to $env?"; then
                    deploy_service "litellm" "$env"
                fi
                pause
                ;;
            5)
                if [ "$env" = "test" ]; then
                    # Configure IP aliasing submenu
                    echo ""
                    echo -e "  ${CYAN}1)${NC} Enable IP aliasing (test -> production)"
                    echo -e "  ${CYAN}2)${NC} Disable IP aliasing"
                    echo -e "  ${CYAN}3)${NC} Check aliasing status"
                    echo -e "  ${CYAN}4)${NC} Cancel"
                    echo ""
                    read -p "Select option [1-4]: " alias_choice
                    case "$alias_choice" in
                        1) setup_vllm_alias "enable" ;;
                        2) setup_vllm_alias "disable" ;;
                        3) setup_vllm_alias "status" ;;
                        *) ;;
                    esac
                    pause
                else
                    return 0
                fi
                ;;
            6)
                if [ "$env" = "test" ]; then
                    select_vllm_mode
                    pause
                else
                    error "Invalid choice"
                    pause
                fi
                ;;
            7)
                if [ "$env" = "test" ]; then
                    return 0
                else
                    error "Invalid choice"
                    pause
                fi
                ;;
            *)
                error "Invalid choice"
                pause
                ;;
        esac
    done
}

# APIs submenu (ingest, search, agent)
apis_menu() {
    local env="$1"
    
    while true; do
        clear
        box "API Services - $env" 70
        echo ""
        info "Select API service to deploy"
        echo ""
        
        echo -e "  ${CYAN}1)${NC} Deploy All APIs"
        echo -e "  ${CYAN}2)${NC} Deploy Ingest API"
        echo -e "  ${CYAN}3)${NC} Deploy Search API"
        echo -e "  ${CYAN}4)${NC} Deploy Agent API"
        echo -e "  ${CYAN}5)${NC} Back"
        echo ""
        
        read -p "Select option [1-5]: " choice
        echo ""
        
        case "$choice" in
            1)
                if confirm "Deploy ALL APIs (ingest, search, agent) to $env?"; then
                    deploy_service "ingest" "$env" && \
                    deploy_service "search-api" "$env" && \
                    deploy_service "agent" "$env"
                fi
                pause
                ;;
            2)
                if confirm "Deploy Ingest API to $env?"; then
                    deploy_service "ingest" "$env"
                fi
                pause
                ;;
            3)
                if confirm "Deploy Search API to $env?"; then
                    deploy_service "search-api" "$env"
                fi
                pause
                ;;
            4)
                if confirm "Deploy Agent API to $env?"; then
                    deploy_service "agent" "$env"
                fi
                pause
                ;;
            5)
                return 0
                ;;
            *)
                error "Invalid choice"
                pause
                ;;
        esac
    done
}

# Apps deployment submenu
deploy_apps_menu() {
    local env="$1"
    
    while true; do
        clear
        box "Apps Deployment - $env" 70
        echo ""
        info "Select application to deploy"
        echo ""
        
        echo -e "  ${CYAN}1)${NC} Deploy All Apps (latest release)"
        echo -e "  ${CYAN}2)${NC} Update Nginx Routing"
        echo -e "  ${CYAN}3)${NC} Deploy AI Portal"
        echo -e "  ${CYAN}4)${NC} Deploy Agent Manager (agent-client)"
        echo -e "  ${CYAN}5)${NC} Deploy Doc Intelligence (doc-intel)"
        echo -e "  ${CYAN}6)${NC} Deploy Foundation Manager"
        echo -e "  ${CYAN}7)${NC} Deploy Project Analysis"
        echo -e "  ${CYAN}8)${NC} Deploy Innovation Manager"
        echo -e "  ${CYAN}9)${NC} Deploy OpenWebUI"
        echo -e "  ${CYAN}10)${NC} Back"
        echo ""
        
        read -p "Select option [1-10]: " choice
        echo ""
        
        case "$choice" in
            1)
                if confirm "Deploy ALL apps (latest release) to $env?"; then
                    deploy_service "apps" "$env"
                fi
                pause
                ;;
            2)
                if confirm "Update Nginx routing configuration for $env?"; then
                    deploy_service "nginx" "$env"
                fi
                pause
                ;;
            3)
                deploy_single_app "ai-portal" "AI Portal" "$env"
                pause
                ;;
            4)
                deploy_single_app "agent-client" "Agent Manager" "$env"
                pause
                ;;
            5)
                deploy_single_app "doc-intel" "Doc Intelligence" "$env"
                pause
                ;;
            6)
                deploy_single_app "foundation" "Foundation Manager" "$env"
                pause
                ;;
            7)
                deploy_single_app "project-analysis" "Project Analysis" "$env"
                pause
                ;;
            8)
                deploy_single_app "innovation" "Innovation Manager" "$env"
                pause
                ;;
            9)
                if confirm "Deploy OpenWebUI to $env?"; then
                    deploy_service "openwebui" "$env"
                fi
                pause
                ;;
            10)
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
        
        # Show vLLM mode for test environment
        local menu_title="Deploy Services - $env Environment"
        if [ "$env" = "test" ]; then
            if [ "$VLLM_MODE" = "alias" ]; then
                menu_title="Deploy Services - $env (vLLM: Production)"
            else
                menu_title="Deploy Services - $env (vLLM: Test Container)"
            fi
        fi
        
        menu "$menu_title" \
            "Deploy All Services" \
            "Deploy Core Services (authz, files, database, vectorstore)" \
            "Deploy LLM Services (vllm, litellm, colpali)" \
            "Deploy APIs (ingest, search, agent)" \
            "Deploy Apps" \
            "Verify Deployment (Health Checks)" \
            "Back to Main Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-7]:${NC} ")" choice
        
        case $choice in
            1)
                if confirm "Deploy ALL services to $env?"; then
                    deploy_service "all" "$env"
                fi
                pause
                ;;
            2)
                core_services_menu "$env"
                ;;
            3)
                llm_services_menu "$env"
                ;;
            4)
                apis_menu "$env"
                ;;
            5)
                deploy_apps_menu "$env"
                ;;
            6)
                verify_deployment "$env"
                pause
                ;;
            7)
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-7."
                ;;
        esac
    done
}

# Docker service selection submenu
docker_select_service() {
    local action="$1"  # "build", "start", "restart", "stop", "logs"
    local compose_file="${REPO_ROOT}/docker-compose.local.yml"
    local env_file="${REPO_ROOT}/.env.local"
    
    # Capitalize first letter (compatible with bash 3.x / zsh / macOS)
    local action_title="$(echo "$action" | awk '{print toupper(substr($0,1,1)) substr($0,2)}')"
    
    while true; do
        echo ""
        menu "Select Service to ${action_title}" \
            "authz-api" \
            "ingest-api" \
            "search-api" \
            "agent-api" \
            "nginx" \
            "litellm" \
            "postgres" \
            "redis" \
            "minio" \
            "milvus" \
            "Back"
        
        read -p "$(echo -e "${BOLD}Select option [1-11]:${NC} ")" choice
        
        local service_name=""
        case $choice in
            1) service_name="authz-api" ;;
            2) service_name="ingest-api" ;;
            3) service_name="search-api" ;;
            4) service_name="agent-api" ;;
            5) service_name="nginx" ;;
            6) service_name="litellm" ;;
            7) service_name="postgres" ;;
            8) service_name="redis" ;;
            9) service_name="minio" ;;
            10) service_name="milvus" ;;
            11) return 0 ;;
            *) error "Invalid selection"; continue ;;
        esac
        
        echo ""
        case "$action" in
            build)
                info "Building $service_name..."
                docker compose -f "$compose_file" --env-file "$env_file" build "$service_name"
                success "Build complete!"
                ;;
            start)
                info "Starting $service_name..."
                docker compose -f "$compose_file" --env-file "$env_file" up -d "$service_name"
                success "Service started!"
                ;;
            restart)
                info "Restarting $service_name..."
                docker compose -f "$compose_file" --env-file "$env_file" restart "$service_name"
                success "Service restarted!"
                ;;
            stop)
                info "Stopping $service_name..."
                docker compose -f "$compose_file" --env-file "$env_file" stop "$service_name"
                success "Service stopped!"
                ;;
            logs)
                info "Showing logs for $service_name (press Ctrl+C to stop)..."
                docker compose -f "$compose_file" logs -f "$service_name" || true
                ;;
        esac
        pause
    done
}

# Docker deployment menu
docker_deploy_menu() {
    while true; do
        echo ""
        menu "Docker Deployment - Local Development" \
            "Build All Services" \
            "Build Specific Service" \
            "Start All Services" \
            "Start Specific Service" \
            "Restart All Services" \
            "Restart Specific Service" \
            "Stop All Services" \
            "Stop Specific Service" \
            "View Service Status" \
            "View All Logs" \
            "View Service Logs" \
            "Clean Up (remove containers & volumes)" \
            "Exit"
        
        read -p "$(echo -e "${BOLD}Select option [1-13]:${NC} ")" choice
        
        local compose_file="${REPO_ROOT}/docker-compose.local.yml"
        local env_file="${REPO_ROOT}/.env.local"
        
        # Create .env.local if it doesn't exist
        if [[ ! -f "$env_file" ]] && [[ -f "${REPO_ROOT}/env.local.example" ]]; then
            warn "Creating .env.local from env.local.example..."
            cp "${REPO_ROOT}/env.local.example" "$env_file"
        fi
        
        case $choice in
            1)
                header "Build All Docker Services" 70
                echo ""
                info "Building all Docker images..."
                echo ""
                docker compose -f "$compose_file" --env-file "$env_file" build
                echo ""
                success "Build complete!"
                pause
                ;;
            2)
                docker_select_service "build"
                ;;
            3)
                header "Start All Docker Services" 70
                echo ""
                info "Starting all Docker services..."
                echo ""
                docker compose -f "$compose_file" --env-file "$env_file" up -d
                echo ""
                success "Services started!"
                echo ""
                docker compose -f "$compose_file" ps
                pause
                ;;
            4)
                docker_select_service "start"
                ;;
            5)
                header "Restart All Docker Services" 70
                echo ""
                info "Restarting all Docker services..."
                echo ""
                docker compose -f "$compose_file" --env-file "$env_file" restart
                echo ""
                success "Services restarted!"
                pause
                ;;
            6)
                docker_select_service "restart"
                ;;
            7)
                header "Stop All Docker Services" 70
                echo ""
                if confirm "Stop all Docker services?"; then
                    docker compose -f "$compose_file" down
                    success "Services stopped!"
                fi
                pause
                ;;
            8)
                docker_select_service "stop"
                ;;
            9)
                header "Docker Service Status" 70
                echo ""
                docker compose -f "$compose_file" ps
                pause
                ;;
            10)
                header "Docker Service Logs" 70
                echo ""
                info "Showing all logs (press Ctrl+C to stop)..."
                echo ""
                docker compose -f "$compose_file" logs -f || true
                ;;
            11)
                docker_select_service "logs"
                ;;
            12)
                header "Clean Up Docker Environment" 70
                echo ""
                warn "This will remove all containers and volumes!"
                warn "All data (database, minio, etc.) will be LOST!"
                echo ""
                if confirm "Are you sure you want to clean up?" "n"; then
                    docker compose -f "$compose_file" down -v --remove-orphans
                    success "Cleanup complete!"
                fi
                pause
                ;;
            13)
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-13."
                ;;
        esac
    done
}

# Main menu
main() {
    # Check for command-line arguments for non-interactive mode
    if [[ $# -ge 1 ]]; then
        # Non-interactive mode: scripts/make/deploy.sh <service> [environment]
        NON_INTERACTIVE=true
        local service="$1"
        local env="${2:-test}"
        
        echo ""
        echo "=============================================="
        echo "  Busibox Deployment - Non-Interactive"
        echo "=============================================="
        echo ""
        echo "Service: $service | Environment: $env"
        echo ""
        
        # Handle Docker environment
        if [[ "$env" == "docker" ]]; then
            local compose_file="${REPO_ROOT}/docker-compose.local.yml"
            local env_file="${REPO_ROOT}/.env.local"
            
            if [[ ! -f "$env_file" ]] && [[ -f "${REPO_ROOT}/env.local.example" ]]; then
                cp "${REPO_ROOT}/env.local.example" "$env_file"
            fi
            
            if [[ "$service" == "all" ]]; then
                docker compose -f "$compose_file" --env-file "$env_file" up -d --build
            else
                docker compose -f "$compose_file" --env-file "$env_file" up -d --build "$service"
            fi
            exit $?
        fi
        
        # Check Ansible for non-Docker deployments
        if ! check_ansible; then
            exit 1
        fi
        
        # Deploy the service
        deploy_service "$service" "$env"
        exit $?
    fi
    
    # Interactive mode (default if no arguments)
    clear
    box "Busibox Deployment" 70
    echo ""
    info "Deploy services using Ansible"
    echo ""
    
    # Select environment
    ENV=$(select_environment)
    
    success "Selected environment: $ENV"
    
    # Handle Docker environment separately
    if [[ "$ENV" == "docker" ]]; then
        docker_deploy_menu
        echo ""
        box "Deployment Complete" 70
        echo ""
        summary "Next Steps" \
            "Run tests: ${CYAN}make docker-test SERVICE=authz${NC}" \
            "View logs: ${CYAN}make docker-logs${NC}" \
            "Check status: ${CYAN}make docker-ps${NC}"
        echo ""
        exit 0
    fi
    
    # Check Ansible for non-Docker deployments
    if ! check_ansible; then
        exit 1
    fi
    
    echo ""
    
    # For test environment, default to alias mode (can be changed in LLM Services menu)
    # For production, always deploy its own vLLM
    if [ "$ENV" != "test" ]; then
        VLLM_MODE="deploy"
    fi
    # Note: Test env defaults to VLLM_MODE="alias" (set at top of script)
    # Can be changed via LLM Services > Change vLLM Mode option
    
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

# Run main function with command-line arguments
main "$@"

exit 0

