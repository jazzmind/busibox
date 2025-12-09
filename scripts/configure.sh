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
    
    # Run ping and capture output (don't fail on unreachable hosts)
    local output
    output=$(ansible -i "inventory/${inv}" all -m ping $vault_flags 2>&1) || true
    
    echo "$output"
    
    # Count results
    local success_count=$(echo "$output" | grep -c "SUCCESS" || echo "0")
    local unreachable_count=$(echo "$output" | grep -c "UNREACHABLE" || echo "0")
    
    echo ""
    if [ "$unreachable_count" -gt 0 ]; then
        warn "$success_count host(s) reachable, $unreachable_count host(s) unreachable"
        info "Unreachable hosts may not be created yet - this is normal for initial setup"
    else
        success "All $success_count host(s) reachable"
    fi
    
    cd "${REPO_ROOT}"
    return 0  # Don't fail - unreachable hosts are expected during initial setup
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
    # Use correct variable names from inventory
    local required_vars=(
        "network_base_octets"
        "postgres_ip"
        "milvus_ip"
        "ingest_ip"
        "agent_ip"
        "apps_ip"
        "minio_ip"
        "proxy_ip"
    )
    
    # Get vault flags
    local vault_flags=$(get_vault_flags)
    
    cd "${REPO_ROOT}/provision/ansible"
    
    for var in "${required_vars[@]}"; do
        local result
        result=$(ansible -i "inventory/${inv}" localhost -m debug -a "var=$var" $vault_flags 2>/dev/null) || true
        if echo "$result" | grep -q "VARIABLE IS NOT DEFINED"; then
            list_item "error" "Missing variable: $var"
            errors=$((errors + 1))
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
    
    # Use ansible to evaluate variables (handles Jinja2 templates)
    # Returns just the IP address value
    get_var() {
        local var_name="$1"
        # Use ansible to evaluate the variable and extract just the value
        ansible -i "inventory/${inv}" localhost -m debug -a "var=$var_name" $vault_flags 2>/dev/null | \
            grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo ""
    }
    
    # Get IPs from inventory (using correct variable names)
    local postgres_ip=$(get_var "postgres_ip")
    local milvus_ip=$(get_var "milvus_ip")
    local minio_ip=$(get_var "minio_ip")
    local ingest_ip=$(get_var "ingest_ip")
    local agent_ip=$(get_var "agent_ip")
    
    cd "${REPO_ROOT}"
    
    local running=0
    local not_running=0
    
    # Helper function to check service
    check_service() {
        local name="$1"
        local ip="$2"
        local check_cmd="$3"
        
        if [ -z "$ip" ]; then
            echo -e "  $name: ${YELLOW}⚠ IP not configured${NC}"
            not_running=$((not_running + 1))
            return 0
        fi
        
        echo -n "  $name ($ip): "
        if eval "$check_cmd" > /dev/null 2>&1; then
            echo -e "${GREEN}✓ Running${NC}"
            running=$((running + 1))
        else
            echo -e "${YELLOW}⚠ Not responding${NC}"
            not_running=$((not_running + 1))
        fi
        return 0
    }
    
    # Check each service (use || true to prevent set -e from exiting)
    # Ports: PostgreSQL 5432, Milvus 9091 (health), MinIO 9000, Ingest API 8002, Search API 8003, Agent API 4111
    check_service "PostgreSQL" "$postgres_ip" "ssh -o ConnectTimeout=5 -o BatchMode=yes root@$postgres_ip 'systemctl is-active postgresql'" || true
    check_service "Milvus" "$milvus_ip" "curl -sf --connect-timeout 5 'http://$milvus_ip:9091/healthz'" || true
    check_service "MinIO" "$minio_ip" "curl -sf --connect-timeout 5 'http://$minio_ip:9000/minio/health/live'" || true
    check_service "Ingest API" "$ingest_ip" "curl -sf --connect-timeout 5 'http://$ingest_ip:8002/health'" || true
    check_service "Search API" "$milvus_ip" "curl -sf --connect-timeout 5 'http://$milvus_ip:8003/health'" || true
    check_service "Agent API" "$agent_ip" "curl -sf --connect-timeout 5 'http://$agent_ip:4111/health'" || true
    
    echo ""
    if [ "$not_running" -gt 0 ]; then
        info "$running service(s) running, $not_running service(s) not responding"
        info "Services may not be deployed yet - this is normal for initial setup"
    else
        success "All $running service(s) running"
    fi
    
    return 0  # Don't fail - services may not be deployed yet
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
            "Generate TOKEN_SERVICE Keys (agent-server)" \
            "Edit Ansible Vault (secrets)" \
            "View Vault Variables (masked)" \
            "Back to Main Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
        
        case $choice in
            1)
                bash "${REPO_ROOT}/scripts/generate-token-service-keys.sh" || {
                    error "TOKEN_SERVICE key generation failed"
                }
                pause
                ;;
            2)
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
            3)
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
            4)
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-4."
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

