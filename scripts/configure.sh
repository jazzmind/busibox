#!/usr/bin/env bash
#
# Busibox Configuration Script
#
# EXECUTION CONTEXT: Proxmox host (as root) for container configuration
#                    Or admin workstation for model configuration
# PURPOSE: Interactive configuration menu for models and containers
#
# USAGE:
#   make configure
#   OR
#   bash scripts/configure.sh
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Source UI library
source "${SCRIPT_DIR}/lib/ui.sh"

# Display welcome
clear
box "Busibox Configuration" 70
echo ""
info "Configure models, GPUs, and container settings"
echo ""

# Global environment variable (set by select_environment)
SELECTED_ENV=""

# ========================================================================
# Verification Functions
# ========================================================================

verify_ansible_connectivity() {
    local inv="$1"
    echo ""
    info "Testing Ansible connectivity to all hosts..."
    echo ""
    
    # Get vault flags
    local vault_flags=$(get_vault_flags)
    
    cd "${REPO_ROOT}/provision/ansible"
    if ansible -i "inventory/${inv}" all -m ping $vault_flags; then
        echo ""
        success "All hosts reachable"
        cd "${REPO_ROOT}"
        return 0
    else
        echo ""
        error "Some hosts unreachable"
        cd "${REPO_ROOT}"
        return 1
    fi
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

verify_vault_access() {
    local vault_pass_file="$HOME/.vault_pass"
    
    echo ""
    info "Testing vault access..."
    echo ""
    
    if [ -f "$vault_pass_file" ]; then
        success "Vault password file found: $vault_pass_file"
        
        cd "${REPO_ROOT}/provision/ansible"
        if ansible-vault view roles/secrets/vars/vault.yml --vault-password-file "$vault_pass_file" > /dev/null 2>&1; then
            success "Vault decryption successful"
            cd "${REPO_ROOT}"
            return 0
        else
            error "Vault decryption failed"
            cd "${REPO_ROOT}"
            return 1
        fi
    else
        warn "Vault password file not found at $vault_pass_file"
        info "You will be prompted for vault password during operations"
        return 0
    fi
}

verify_inventory_vars() {
    local inv="$1"
    
    echo ""
    info "Verifying inventory variables for $inv..."
    echo ""
    
    local errors=0
    local required_vars=(
        "network_base_octets"
        "pg_ip"
        "milvus_ip"
        "ingest_ip"
        "agent_ip"
        "apps_ip"
        "files_ip"
        "proxy_ip"
    )
    
    # Get vault flags
    local vault_flags=$(get_vault_flags)
    
    cd "${REPO_ROOT}/provision/ansible"
    
    for var in "${required_vars[@]}"; do
        local result=$(ansible -i "inventory/${inv}" localhost -m debug -a "var=$var" $vault_flags 2>/dev/null)
        if echo "$result" | grep -q "VARIABLE IS NOT DEFINED"; then
            list_item "error" "Missing variable: $var"
            ((errors++))
        else
            list_item "done" "$var defined"
        fi
    done
    
    cd "${REPO_ROOT}"
    
    echo ""
    if [ $errors -gt 0 ]; then
        error "Found $errors missing variable(s)"
        return 1
    else
        success "All required variables defined"
        return 0
    fi
}

verify_service_health() {
    local inv="$1"
    
    echo ""
    info "Checking service health for $inv..."
    echo ""
    
    # Get vault flags
    local vault_flags=$(get_vault_flags)
    
    cd "${REPO_ROOT}/provision/ansible"
    
    # Get IPs from inventory
    local pg_ip=$(ansible -i "inventory/${inv}" localhost -m debug -a "var=pg_ip" $vault_flags 2>/dev/null | grep "pg_ip" | awk -F'"' '{print $2}')
    local milvus_ip=$(ansible -i "inventory/${inv}" localhost -m debug -a "var=milvus_ip" $vault_flags 2>/dev/null | grep "milvus_ip" | awk -F'"' '{print $2}')
    local files_ip=$(ansible -i "inventory/${inv}" localhost -m debug -a "var=files_ip" $vault_flags 2>/dev/null | grep "files_ip" | awk -F'"' '{print $2}')
    local ingest_ip=$(ansible -i "inventory/${inv}" localhost -m debug -a "var=ingest_ip" $vault_flags 2>/dev/null | grep "ingest_ip" | awk -F'"' '{print $2}')
    
    cd "${REPO_ROOT}"
    
    # PostgreSQL
    echo -n "  PostgreSQL ($pg_ip): "
    if ssh root@$pg_ip 'systemctl is-active postgresql' > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${RED}✗ Not running${NC}"
    fi
    
    # Milvus
    echo -n "  Milvus ($milvus_ip): "
    if curl -sf "http://$milvus_ip:9091/healthz" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${YELLOW}⚠ Not responding (may not be deployed)${NC}"
    fi
    
    # MinIO
    echo -n "  MinIO ($files_ip): "
    if curl -sf "http://$files_ip:9000/minio/health/live" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${YELLOW}⚠ Not responding (may not be deployed)${NC}"
    fi
    
    # Ingest API
    echo -n "  Ingest API ($ingest_ip): "
    if curl -sf "http://$ingest_ip:8000/health" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${YELLOW}⚠ Not responding (may not be deployed)${NC}"
    fi
    
    # Search API
    echo -n "  Search API ($milvus_ip): "
    if curl -sf "http://$milvus_ip:8001/health" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${YELLOW}⚠ Not responding (may not be deployed)${NC}"
    fi
    
    echo ""
}

verify_all_configuration() {
    # Select environment if not already selected
    if [ -z "$SELECTED_ENV" ]; then
        SELECTED_ENV=$(select_environment)
    fi
    
    local inv="$SELECTED_ENV"
    
    header "Full Configuration Verification ($inv)" 70
    
    local errors=0
    
    echo ""
    echo -e "${BLUE}[1/4] Ansible Connectivity${NC}"
    separator
    verify_ansible_connectivity "$inv" || ((errors++))
    
    echo ""
    echo -e "${BLUE}[2/4] Vault Access${NC}"
    separator
    verify_vault_access || ((errors++))
    
    echo ""
    echo -e "${BLUE}[3/4] Inventory Variables${NC}"
    separator
    verify_inventory_vars "$inv" || ((errors++))
    
    echo ""
    echo -e "${BLUE}[4/4] Service Health${NC}"
    separator
    verify_service_health "$inv"
    
    echo ""
    separator 70
    if [ $errors -eq 0 ]; then
        success "All verification checks passed!"
    else
        warn "$errors check(s) had issues"
    fi
    separator 70
}

# Model Configuration Menu
model_configuration() {
    while true; do
        echo ""
        menu "Model Configuration" \
            "Download/Manage LLM Models" \
            "Update Model Config (analyze downloaded models)" \
            "Configure vLLM Model Routing (GPU assignments)" \
            "Back to Main Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
        
        case $choice in
            1)
                # Download/Manage Models submenu
                while true; do
                    echo ""
                    menu "Download/Manage LLM Models" \
                        "Download Models from Registry" \
                        "Cleanup Orphaned Models (not in registry)" \
                        "Remove Duplicate Models (save disk space)" \
                        "Back to Model Configuration"
                    
                    read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" subchoice
                    
                    case $subchoice in
                        1)
                            header "Download LLM Models" 70
                            echo ""
                            info "This will download models from model_registry.yml to Proxmox host"
                            echo ""
                            
                            if ! check_proxmox; then
                                error "This operation requires Proxmox host"
                                pause
                                continue
                            fi
                            
                            if confirm "Download models from registry?"; then
                                bash "${REPO_ROOT}/provision/pct/host/setup-llm-models.sh" || {
                                    error "Model download failed"
                                }
                            fi
                            pause
                            ;;
                        2)
                            header "Cleanup Orphaned Models" 70
                            echo ""
                            info "This will remove models NOT in registry (with confirmation)"
                            echo ""
                            
                            if ! check_proxmox; then
                                error "This operation requires Proxmox host"
                                pause
                                continue
                            fi
                            
                            if confirm "Run cleanup to remove orphaned models?"; then
                                bash "${REPO_ROOT}/provision/pct/host/setup-llm-models.sh" --cleanup || {
                                    error "Model cleanup failed"
                                }
                            fi
                            pause
                            ;;
                        3)
                            header "Remove Duplicate Models" 70
                            echo ""
                            info "This will find and remove duplicate models stored in multiple locations"
                            info "Keeps the standard hub/ version and removes old root copies"
                            echo ""
                            warn "This can free up significant disk space!"
                            echo ""
                            
                            if ! check_proxmox; then
                                error "This operation requires Proxmox host"
                                pause
                                continue
                            fi
                            
                            if confirm "Run deduplication to remove duplicate models?"; then
                                bash "${REPO_ROOT}/provision/pct/host/setup-llm-models.sh" --deduplicate || {
                                    error "Model deduplication failed"
                                }
                            fi
                            pause
                            ;;
                        4)
                            break
                            ;;
                        *)
                            error "Invalid selection. Please enter 1-4."
                            ;;
                    esac
                done
                ;;
            2)
                header "Update Model Configuration" 70
                echo ""
                info "This will analyze downloaded models and update model_config.yml"
                echo ""
                
                if confirm "Run model configuration update?"; then
                    bash "${REPO_ROOT}/provision/pct/host/update-model-config.sh" || {
                        error "Model configuration update failed"
                    }
                fi
                pause
                ;;
            3)
                header "Configure vLLM Model Routing" 70
                echo ""
                info "This will configure which models run on which GPUs"
                echo ""
                
                if ! check_proxmox; then
                    error "This operation requires Proxmox host"
                    pause
                    continue
                fi
                
                if confirm "Run interactive model routing configuration?"; then
                    bash "${REPO_ROOT}/provision/pct/host/configure-vllm-model-routing.sh" --interactive || {
                        error "Model routing configuration failed"
                    }
                fi
                pause
                ;;
            4)
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-4."
                ;;
        esac
    done
}

# Container Configuration Menu
container_configuration() {
    # Check if on Proxmox
    if ! check_proxmox; then
        error "Container configuration requires Proxmox host"
        pause
        return 1
    fi
    
    while true; do
        echo ""
        menu "Container Configuration" \
            "Check Container Memory Allocation" \
            "Install NVIDIA Drivers in Container" \
            "Configure GPU Passthrough for Container" \
            "Configure GPU Allocation (All Containers)" \
            "Configure All GPUs for Container" \
            "Setup ZFS Storage" \
            "Add Data Mounts to Containers" \
            "Back to Main Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-8]:${NC} ")" choice
        
        case $choice in
            1)
                header "Check Container Memory" 70
                echo ""
                ENV=$(select_environment)
                echo ""
                
                bash "${REPO_ROOT}/provision/pct/host/check-container-memory.sh" "$ENV" || {
                    error "Memory check failed"
                }
                pause
                ;;
            2)
                header "Install NVIDIA Drivers" 70
                echo ""
                info "This will install NVIDIA drivers in a specific container"
                echo ""
                read -p "$(echo -e "${BOLD}Enter container ID:${NC} ")" container_id
                
                if [[ ! "$container_id" =~ ^[0-9]+$ ]]; then
                    error "Invalid container ID. Must be numeric."
                    pause
                    continue
                fi
                
                echo ""
                if confirm "Install NVIDIA drivers in container $container_id?"; then
                    bash "${REPO_ROOT}/provision/pct/host/install-nvidia-drivers.sh" "$container_id" || {
                        error "Driver installation failed"
                    }
                fi
                pause
                ;;
            3)
                header "Configure GPU Passthrough" 70
                echo ""
                info "This will configure GPU passthrough for a specific container"
                echo ""
                read -p "$(echo -e "${BOLD}Enter container ID:${NC} ")" container_id
                read -p "$(echo -e "${BOLD}Enter GPU(s) (e.g., 0 or 0,1,2 or 0-2):${NC} ")" gpus
                
                echo ""
                if confirm "Configure GPU(s) $gpus for container $container_id?"; then
                    bash "${REPO_ROOT}/provision/pct/host/configure-gpu-passthrough.sh" "$container_id" "$gpus" || {
                        error "GPU passthrough configuration failed"
                    }
                fi
                pause
                ;;
            4)
                header "Configure GPU Allocation" 70
                echo ""
                info "This will configure GPU allocation for ingest and vLLM containers"
                echo ""
                
                if confirm "Run interactive GPU allocation?"; then
                    bash "${REPO_ROOT}/provision/pct/host/configure-gpu-allocation.sh" --interactive || {
                        error "GPU allocation configuration failed"
                    }
                fi
                pause
                ;;
            5)
                header "Configure All GPUs for Container" 70
                echo ""
                info "This will pass ALL GPUs to a container and install drivers"
                echo ""
                read -p "$(echo -e "${BOLD}Enter container ID:${NC} ")" container_id
                
                echo ""
                if confirm "Configure all GPUs for container $container_id?"; then
                    bash "${REPO_ROOT}/provision/pct/host/configure-container-gpus.sh" "$container_id" || {
                        error "GPU configuration failed"
                    }
                fi
                pause
                ;;
            6)
                header "Setup ZFS Storage" 70
                echo ""
                info "This will setup ZFS datasets for persistent data"
                echo ""
                
                if confirm "Run ZFS storage setup?"; then
                    bash "${REPO_ROOT}/provision/pct/host/setup-zfs-storage.sh" || {
                        error "ZFS storage setup failed"
                    }
                fi
                pause
                ;;
            7)
                header "Add Data Mounts" 70
                echo ""
                ENV=$(select_environment)
                echo ""
                
                if confirm "Add data mounts for $ENV environment?"; then
                    bash "${REPO_ROOT}/provision/pct/host/add-data-mounts.sh" "$ENV" || {
                        error "Data mount configuration failed"
                    }
                fi
                pause
                ;;
            8)
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-8."
                ;;
        esac
    done
}

# Secrets Configuration Menu
secrets_configuration() {
    while true; do
        echo ""
        menu "Secrets & Configuration" \
            "Edit Ansible Vault (secrets)" \
            "View Vault Variables (masked)" \
            "Back to Main Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-3]:${NC} ")" choice
        
        case $choice in
            1)
                header "Edit Ansible Vault" 70
                echo ""
                info "Opening encrypted vault for editing"
                info "You will need the vault password"
                echo ""
                
                cd "${REPO_ROOT}/provision/ansible"
                ansible-vault edit roles/secrets/vars/vault.yml || {
                    error "Failed to edit vault"
                }
                cd "${REPO_ROOT}"
                
                pause
                ;;
            2)
                header "View Vault Variables" 70
                echo ""
                info "Showing vault structure (sensitive values masked)"
                echo ""
                
                cd "${REPO_ROOT}/provision/ansible"
                if ansible-vault view roles/secrets/vars/vault.yml | grep -E "^[a-z_]+:" | sed 's/:.*$/: <masked>/'; then
                    :
                else
                    error "Failed to view vault"
                fi
                cd "${REPO_ROOT}"
                
                pause
                ;;
            3)
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-3."
                ;;
        esac
    done
}

# Main menu
main_menu() {
    while true; do
        echo ""
        menu "Busibox Configuration" \
            "Verify Configuration (recommended first step)" \
            "Model Configuration" \
            "Container Configuration" \
            "Secrets & Configuration" \
            "Change Environment" \
            "Exit"
        
        read -p "$(echo -e "${BOLD}Select option [1-6]:${NC} ")" choice
        
        case $choice in
            1)
                verify_all_configuration
                pause
                ;;
            2)
                model_configuration
                ;;
            3)
                container_configuration
                ;;
            4)
                secrets_configuration
                ;;
            5)
                SELECTED_ENV=""
                SELECTED_ENV=$(select_environment)
                success "Changed to: $SELECTED_ENV"
                pause
                ;;
            6)
                echo ""
                info "Exiting..."
                exit 0
                ;;
            *)
                error "Invalid selection. Please enter 1-6."
                ;;
        esac
    done
}

# Select environment first
SELECTED_ENV=$(select_environment)
success "Selected environment: $SELECTED_ENV"
echo ""

# Run main menu
main_menu

exit 0

