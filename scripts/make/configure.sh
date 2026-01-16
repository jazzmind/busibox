#!/usr/bin/env bash
#
# Busibox Configuration Script
#
# EXECUTION CONTEXT: Admin workstation or Proxmox host
# PURPOSE: Interactive configuration menu for models, containers, and apps
#
# USAGE:
#   make configure
#   OR
#   bash scripts/make/configure.sh
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"

# Get environment from state
ENV=$(get_environment)
BACKEND=$(get_backend "$ENV")

# ========================================================================
# Verification Functions (Proxmox)
# ========================================================================

get_vault_flags() {
    local vault_pass_file="$HOME/.vault_pass"
    if [ -f "$vault_pass_file" ]; then
        echo "--vault-password-file $vault_pass_file"
    else
        echo "--ask-vault-pass"
    fi
}

verify_ansible_connectivity() {
    local inv="$1"
    echo ""
    info "Testing Ansible connectivity to all hosts..."
    echo ""
    
    # Check if inventory exists
    if [[ ! -d "${REPO_ROOT}/provision/ansible/inventory/${inv}" ]]; then
        error "Inventory directory not found: inventory/${inv}"
        return 1
    fi
    
    local vault_flags=$(get_vault_flags)
    cd "${REPO_ROOT}/provision/ansible"
    
    local output exit_code=0
    output=$(ansible -i "inventory/${inv}" all -m ping $vault_flags 2>&1) || exit_code=$?
    
    # Check for common errors
    if echo "$output" | grep -q "ERROR! Decryption failed"; then
        error "Vault decryption failed. Check your vault password."
        cd "${REPO_ROOT}"
        return 1
    fi
    
    if echo "$output" | grep -q "ERROR!"; then
        # Show the error but continue
        echo "$output" | grep -A2 "ERROR!" | head -5
        warn "Ansible reported errors (see above)"
    else
        echo "$output"
    fi
    
    local success_count=$(echo "$output" | grep -c "SUCCESS" || echo "0")
    local unreachable_count=$(echo "$output" | grep -c "UNREACHABLE" || echo "0")
    
    echo ""
    if [ "$unreachable_count" -gt 0 ]; then
        warn "$success_count host(s) reachable, $unreachable_count host(s) unreachable"
    elif [ "$success_count" -gt 0 ]; then
        success "All $success_count host(s) reachable"
    else
        warn "No hosts responded"
    fi
    
    cd "${REPO_ROOT}"
    return 0
}

verify_vault_access() {
    local vault_pass_file="$HOME/.vault_pass"
    local env="${ENV:-staging}"
    
    echo ""
    info "Testing vault access..."
    echo ""
    
    # Find the vault file - check inventory location first, then secrets role
    local vault_file=""
    local inv_vault="${REPO_ROOT}/provision/ansible/inventory/${env}/group_vars/all/vault.yml"
    local role_vault="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.yml"
    
    if [[ -f "$inv_vault" ]]; then
        vault_file="$inv_vault"
        info "Using inventory vault: inventory/${env}/group_vars/all/vault.yml"
    elif [[ -f "$role_vault" ]]; then
        vault_file="$role_vault"
        info "Using role vault: roles/secrets/vars/vault.yml"
    else
        warn "No vault file found"
        info "Expected locations:"
        echo "  - inventory/${env}/group_vars/all/vault.yml"
        echo "  - roles/secrets/vars/vault.yml"
        return 1
    fi
    
    # Check if vault file is encrypted
    if ! head -1 "$vault_file" | grep -q '^\$ANSIBLE_VAULT'; then
        warn "Vault file exists but is not encrypted"
        return 0
    fi
    
    if [ -f "$vault_pass_file" ]; then
        success "Vault password file found: $vault_pass_file"
        
        cd "${REPO_ROOT}/provision/ansible"
        local output
        if output=$(ansible-vault view "$vault_file" --vault-password-file "$vault_pass_file" 2>&1); then
            success "Vault decryption successful"
            cd "${REPO_ROOT}"
            return 0
        else
            error "Vault decryption failed"
            echo "  ${DIM}$output${NC}"
            cd "${REPO_ROOT}"
            return 1
        fi
    else
        warn "Vault password file not found at $vault_pass_file"
        info "You will be prompted for vault password during operations"
        info "Tip: Create it with: echo 'your-password' > ~/.vault_pass && chmod 600 ~/.vault_pass"
        return 0
    fi
}

verify_service_health() {
    local inv="$1"
    
    echo ""
    info "Checking service health for $inv..."
    echo ""
    
    # Use hardcoded network bases instead of ansible vars to avoid vault issues
    local network_base
    case "$inv" in
        production) network_base="10.96.200" ;;
        staging) network_base="10.96.201" ;;
        local|docker) 
            info "For local/docker, use 'make docker-ps' to check service status"
            return 0
            ;;
        *)
            warn "Unknown environment: $inv"
            return 1
            ;;
    esac
    
    # Standard IP assignments (based on network layout)
    # These are the common IP endings for each service
    local proxy_ip="${network_base}.200"
    local agent_ip="${network_base}.202"
    local postgres_ip="${network_base}.203"
    local milvus_ip="${network_base}.204"
    local minio_ip="${network_base}.205"
    local ingest_ip="${network_base}.206"
    local litellm_ip="${network_base}.207"
    local authz_ip="${network_base}.210"
    
    local running=0
    local not_running=0
    
    check_service() {
        local name="$1"
        local ip="$2"
        local check_cmd="$3"
        
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
    
    # Quick connectivity check first
    echo -n "  Network ($proxy_ip): "
    if ping -c 1 -W 2 "$proxy_ip" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Reachable${NC}"
    else
        echo -e "${RED}✗ Not reachable${NC}"
        warn "Cannot reach $inv network. Check VPN or network connectivity."
        return 1
    fi
    
    echo ""
    
    # Check services via HTTP health endpoints (doesn't require SSH)
    check_service "Proxy/Nginx" "$proxy_ip" "curl -sf --connect-timeout 3 -k 'https://$proxy_ip/health' || curl -sf --connect-timeout 3 'http://$proxy_ip:80'" || true
    check_service "AuthZ API" "$authz_ip" "curl -sf --connect-timeout 3 'http://$authz_ip:8010/health'" || true
    check_service "Ingest API" "$ingest_ip" "curl -sf --connect-timeout 3 'http://$ingest_ip:8002/health'" || true
    check_service "Search API" "$milvus_ip" "curl -sf --connect-timeout 3 'http://$milvus_ip:8003/health'" || true
    check_service "Agent API" "$agent_ip" "curl -sf --connect-timeout 3 'http://$agent_ip:8000/health'" || true
    check_service "LiteLLM" "$litellm_ip" "curl -sf --connect-timeout 3 'http://$litellm_ip:4000/health'" || true
    check_service "Milvus" "$milvus_ip" "curl -sf --connect-timeout 3 'http://$milvus_ip:9091/healthz'" || true
    check_service "MinIO" "$minio_ip" "curl -sf --connect-timeout 3 'http://$minio_ip:9000/minio/health/live'" || true
    
    echo ""
    if [ "$not_running" -gt 0 ]; then
        info "$running service(s) running, $not_running service(s) not responding"
    else
        success "All $running service(s) running"
    fi
    
    return 0
}

verify_all_configuration() {
    header "Full Configuration Verification ($ENV)" 70
    
    local errors=0
    
    echo ""
    echo -e "${BLUE}[1/3] Ansible Connectivity${NC}"
    separator
    verify_ansible_connectivity "$ENV" || ((errors++))
    
    echo ""
    echo -e "${BLUE}[2/3] Vault Access${NC}"
    separator
    verify_vault_access || ((errors++))
    
    echo ""
    echo -e "${BLUE}[3/3] Service Health${NC}"
    separator
    verify_service_health "$ENV"
    
    echo ""
    separator 70
    if [ $errors -eq 0 ]; then
        success "All verification checks passed!"
    else
        warn "$errors check(s) had issues"
    fi
    separator 70
}

# ========================================================================
# Model Configuration (Proxmox only)
# ========================================================================

model_configuration() {
    while true; do
        echo ""
        menu "Model Configuration" \
            "Download/Manage LLM Models" \
            "Update Model Config (analyze downloaded models)" \
            "Configure vLLM Model Routing (GPU assignments)" \
            "Back"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
        
        case "${choice:-}" in
            1)
                while true; do
                    echo ""
                    menu "Download/Manage LLM Models" \
                        "Download Models from Registry" \
                        "Cleanup Orphaned Models" \
                        "Remove Duplicate Models" \
                        "Back"
                    
                    local subchoice=""
                    read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" subchoice
                    
                    case "${subchoice:-}" in
                        1)
                            header "Download LLM Models" 70
                            if ! check_proxmox; then
                                error "This operation requires Proxmox host"
                                pause
                                continue
                            fi
                            if confirm "Download models from registry?"; then
                                bash "${REPO_ROOT}/provision/pct/host/setup-llm-models.sh" || error "Failed"
                            fi
                            pause
                            ;;
                        2)
                            header "Cleanup Orphaned Models" 70
                            if ! check_proxmox; then
                                error "This operation requires Proxmox host"
                                pause
                                continue
                            fi
                            if confirm "Remove orphaned models?"; then
                                bash "${REPO_ROOT}/provision/pct/host/setup-llm-models.sh" --cleanup || error "Failed"
                            fi
                            pause
                            ;;
                        3)
                            header "Remove Duplicate Models" 70
                            if ! check_proxmox; then
                                error "This operation requires Proxmox host"
                                pause
                                continue
                            fi
                            if confirm "Remove duplicate models?"; then
                                bash "${REPO_ROOT}/provision/pct/host/setup-llm-models.sh" --deduplicate || error "Failed"
                            fi
                            pause
                            ;;
                        4|b|B|"")
                            break
                            ;;
                    esac
                done
                ;;
            2)
                header "Update Model Configuration" 70
                if confirm "Run model configuration update?"; then
                    bash "${REPO_ROOT}/provision/pct/host/update-model-config.sh" || error "Failed"
                fi
                pause
                ;;
            3)
                header "Configure vLLM Model Routing" 70
                if ! check_proxmox; then
                    error "This operation requires Proxmox host"
                    pause
                    continue
                fi
                if confirm "Run interactive model routing configuration?"; then
                    bash "${REPO_ROOT}/provision/pct/host/configure-vllm-model-routing.sh" --interactive || error "Failed"
                fi
                pause
                ;;
            4|b|B|"")
                return 0
                ;;
        esac
    done
}

# ========================================================================
# Container Configuration (Proxmox only)
# ========================================================================

container_configuration() {
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
            "Setup ZFS Storage" \
            "Back"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select option [1-6]:${NC} ")" choice
        
        case "${choice:-}" in
            1)
                header "Check Container Memory" 70
                bash "${REPO_ROOT}/provision/pct/host/check-container-memory.sh" "$ENV" || error "Failed"
                pause
                ;;
            2)
                header "Install NVIDIA Drivers" 70
                local container_id=""
                read -p "$(echo -e "${BOLD}Enter container ID:${NC} ")" container_id
                if [[ ! "${container_id:-}" =~ ^[0-9]+$ ]]; then
                    error "Invalid container ID"
                    continue
                fi
                if confirm "Install NVIDIA drivers in container $container_id?"; then
                    bash "${REPO_ROOT}/provision/pct/host/install-nvidia-drivers.sh" "$container_id" || error "Failed"
                fi
                pause
                ;;
            3)
                header "Configure GPU Passthrough" 70
                local container_id="" gpus=""
                read -p "$(echo -e "${BOLD}Enter container ID:${NC} ")" container_id
                read -p "$(echo -e "${BOLD}Enter GPU(s) (e.g., 0 or 0,1,2):${NC} ")" gpus
                if confirm "Configure GPU(s) $gpus for container $container_id?"; then
                    bash "${REPO_ROOT}/provision/pct/host/configure-gpu-passthrough.sh" "$container_id" "$gpus" || error "Failed"
                fi
                pause
                ;;
            4)
                header "Configure GPU Allocation" 70
                if confirm "Run interactive GPU allocation?"; then
                    bash "${REPO_ROOT}/provision/pct/host/configure-gpu-allocation.sh" --interactive || error "Failed"
                fi
                pause
                ;;
            5)
                header "Setup ZFS Storage" 70
                if confirm "Run ZFS storage setup?"; then
                    bash "${REPO_ROOT}/provision/pct/host/setup-zfs-storage.sh" || error "Failed"
                fi
                pause
                ;;
            6|b|B|"")
                return 0
                ;;
        esac
    done
}

# ========================================================================
# App Configuration (works for both Docker and Proxmox)
# ========================================================================

app_configuration() {
    while true; do
        echo ""
        menu "App Configuration" \
            "Run All App Setup (recommended)" \
            "Activate Admin User" \
            "Fix Built-in Apps (Video, Chat, Documents)" \
            "Register AuthZ Clients" \
            "Back"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select option [1-5]:${NC} ")" choice
        
        case "${choice:-}" in
            1)
                header "Full App Configuration" 70
                info "This will:"
                echo "  1. Activate the admin user"
                echo "  2. Fix built-in apps"
                echo "  3. Register AuthZ clients"
                echo ""
                if confirm "Run full app configuration?"; then
                    bash "${REPO_ROOT}/scripts/setup/configure-apps.sh" --all || error "Failed"
                fi
                pause
                ;;
            2)
                header "Activate Admin User" 70
                if confirm "Activate admin user?"; then
                    bash "${REPO_ROOT}/scripts/setup/configure-apps.sh" --admin || error "Failed"
                fi
                pause
                ;;
            3)
                header "Fix Built-in Apps" 70
                if confirm "Fix built-in apps?"; then
                    bash "${REPO_ROOT}/scripts/setup/configure-apps.sh" --apps || error "Failed"
                fi
                pause
                ;;
            4)
                header "Register AuthZ Clients" 70
                if confirm "Register AuthZ clients?"; then
                    bash "${REPO_ROOT}/scripts/setup/configure-apps.sh" --authz || error "Failed"
                fi
                pause
                ;;
            5|b|B|"")
                return 0
                ;;
        esac
    done
}

# ========================================================================
# Secrets Configuration (Proxmox only)
# ========================================================================

secrets_configuration() {
    while true; do
        echo ""
        menu "Secrets & Keys" \
            "Generate TOKEN_SERVICE Keys (agent-server)" \
            "Edit Ansible Vault (secrets)" \
            "View Vault Variables (masked)" \
            "Back"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
        
        case "${choice:-}" in
            1)
                bash "${REPO_ROOT}/scripts/generate/generate-token-service-keys.sh" || error "Failed"
                pause
                ;;
            2)
                header "Edit Ansible Vault" 70
                cd "${REPO_ROOT}/provision/ansible"
                ansible-vault edit roles/secrets/vars/vault.yml || error "Failed to edit vault"
                cd "${REPO_ROOT}"
                pause
                ;;
            3)
                header "View Vault Variables" 70
                cd "${REPO_ROOT}/provision/ansible"
                ansible-vault view roles/secrets/vars/vault.yml | grep -E "^[a-z_]+:" | sed 's/:.*$/: <masked>/' || error "Failed"
                cd "${REPO_ROOT}"
                pause
                ;;
            4|b|B|"")
                return 0
                ;;
        esac
    done
}

# ========================================================================
# Docker Configuration Menu
# ========================================================================

docker_menu() {
    while true; do
        clear
        box "Configuration - Local Docker" 70
        status_bar "$ENV" "$BACKEND" "$(get_install_status)" 70
        
        echo ""
        menu "Docker Configuration" \
            "App Configuration (admin, OAuth clients)" \
            "Back to Main Menu"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select option [1-2]:${NC} ")" choice
        
        case "${choice:-}" in
            1)
                app_configuration
                ;;
            2|b|B|"")
                return 0
                ;;
        esac
    done
}

# ========================================================================
# Proxmox Configuration Menu
# ========================================================================

proxmox_menu() {
    while true; do
        clear
        box "Configuration - $ENV (Proxmox)" 70
        status_bar "$ENV" "$BACKEND" "$(get_install_status)" 70
        
        echo ""
        menu "Proxmox Configuration" \
            "Verify Configuration (connectivity, vault, services)" \
            "Model Configuration (LLM models, routing)" \
            "Container Configuration (GPU, storage)" \
            "App Configuration (admin, OAuth clients)" \
            "Secrets & Keys (TOKEN_SERVICE, vault)" \
            "Back to Main Menu"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select option [1-6]:${NC} ")" choice
        
        case "${choice:-}" in
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
                app_configuration
                ;;
            5)
                secrets_configuration
                ;;
            6|b|B|"")
                return 0
                ;;
        esac
    done
}

# ========================================================================
# Main
# ========================================================================

main() {
    # Check if environment is set
    if [[ -z "$ENV" ]]; then
        error "No environment selected. Run 'make' to select an environment first."
        exit 1
    fi
    
    # Show appropriate menu based on backend
    case "$BACKEND" in
        docker)
            docker_menu
            ;;
        proxmox)
            proxmox_menu
            ;;
        *)
            error "Unknown backend: $BACKEND"
            exit 1
            ;;
    esac
}

main
