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
            "Model Configuration" \
            "Container Configuration" \
            "Secrets & Configuration" \
            "Exit"
        
        read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
        
        case $choice in
            1)
                model_configuration
                ;;
            2)
                container_configuration
                ;;
            3)
                secrets_configuration
                ;;
            4)
                echo ""
                info "Exiting..."
                exit 0
                ;;
            *)
                error "Invalid selection. Please enter 1-4."
                ;;
        esac
    done
}

# Run main menu
main_menu

exit 0

