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
source "${REPO_ROOT}/scripts/lib/github.sh"

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
    
    # Check for undefined variable errors (common vault issue)
    if echo "$output" | grep -q "is undefined"; then
        local undefined_var=$(echo "$output" | grep -oE "'[a-z_]+' is undefined" | head -1 | grep -oE "'[^']+'" | tr -d "'")
        echo ""
        error "Ansible variable not defined: $undefined_var"
        echo ""
        if [[ "$undefined_var" =~ network_base_octets|base_domain ]]; then
            warn "This variable should be defined in local.yml (generated from state file)."
            info "Run: scripts/ansible/generate-local-config.sh $inv"
            info "Or ensure .busibox-state-{env} has NETWORK_BASE_OCTETS_STAGING and NETWORK_BASE_OCTETS_PRODUCTION"
        else
            warn "This variable should be defined in your vault file."
            info "Check: roles/secrets/vars/vault.${inv}.yml"
        fi
        echo ""
        info "Expected variables:"
        echo "  In local.yml (from state file):"
        echo "    - network_base_octets_staging (e.g., '10.96.201')"
        echo "    - network_base_octets_production (e.g., '10.96.200')"
        echo "    - base_domain (e.g., 'example.com')"
        echo "  In vault.{env}.yml (encrypted secrets):"
        echo "    - secrets.postgresql.password"
        echo "    - secrets.minio.root_user/root_password"
        echo "    - etc."
        echo ""
        cd "${REPO_ROOT}"
        # Don't return error - let verification continue to check other things
    elif echo "$output" | grep -q "ERROR!"; then
        # Show the error but continue
        echo "$output" | grep -A2 "ERROR!" | head -5
        warn "Ansible reported errors (see above)"
    else
        echo "$output"
    fi
    
    # Count successes and failures (grep -c returns count, but may fail if no matches)
    local success_count=0
    local unreachable_count=0
    success_count=$(echo "$output" | grep -c "SUCCESS" 2>/dev/null || true)
    unreachable_count=$(echo "$output" | grep -c "UNREACHABLE" 2>/dev/null || true)
    
    # Ensure we have integers
    success_count="${success_count:-0}"
    unreachable_count="${unreachable_count:-0}"
    # Remove any whitespace/newlines
    success_count=$(echo "$success_count" | tr -d '[:space:]')
    unreachable_count=$(echo "$unreachable_count" | tr -d '[:space:]')
    
    echo ""
    if [[ "$unreachable_count" -gt 0 ]]; then
        warn "$success_count host(s) reachable, $unreachable_count host(s) unreachable"
    elif [[ "$success_count" -gt 0 ]]; then
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
    
    # First, check if vault symlinks need to be set up
    check_and_setup_vault_links "$env"
    
    # Find the vault file - check inventory location first, then secrets role
    local vault_file=""
    local inv_vault="${REPO_ROOT}/provision/ansible/inventory/${env}/group_vars/all/vault.yml"
    local role_vault="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.yml"
    
    if [[ -f "$inv_vault" ]] || [[ -L "$inv_vault" ]]; then
        # Check if symlink is valid
        if [[ -L "$inv_vault" ]] && [[ ! -e "$inv_vault" ]]; then
            warn "Vault symlink exists but target is missing"
            info "Symlink: $inv_vault"
            info "Expected target: $role_vault"
            
            if [[ ! -f "$role_vault" ]]; then
                error "Missing vault file: $role_vault"
                info "Create from template:"
                echo "  cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml"
                echo "  ansible-vault encrypt roles/secrets/vars/vault.yml"
                return 1
            fi
        fi
        vault_file="$inv_vault"
        info "Using inventory vault: inventory/${env}/group_vars/all/vault.yml"
    elif [[ -f "$role_vault" ]]; then
        vault_file="$role_vault"
        info "Using role vault: roles/secrets/vars/vault.yml"
    else
        warn "No vault file found"
        info "Expected locations:"
        echo "  - inventory/${env}/group_vars/all/vault.yml (symlink)"
        echo "  - roles/secrets/vars/vault.yml (actual file)"
        info ""
        info "Create vault file from template:"
        echo "  cd provision/ansible"
        echo "  cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml"
        echo "  ansible-vault encrypt roles/secrets/vars/vault.yml"
        echo "  bash ../../scripts/vault/setup-vault-links.sh"
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

# Check and setup vault symlinks if needed
check_and_setup_vault_links() {
    local env="$1"
    local ansible_dir="${REPO_ROOT}/provision/ansible"
    local role_vault="${ansible_dir}/roles/secrets/vars/vault.yml"
    local inv_vault_dir="${ansible_dir}/inventory/${env}/group_vars/all"
    local inv_vault="${inv_vault_dir}/vault.yml"
    
    # Check if the role vault exists
    if [[ ! -f "$role_vault" ]]; then
        # Can't set up symlinks without the source file
        return 0
    fi
    
    # Check if inventory vault symlink exists and is valid
    if [[ -L "$inv_vault" ]]; then
        # Symlink exists - check if it's valid
        if [[ -e "$inv_vault" ]]; then
            # Valid symlink, nothing to do
            return 0
        else
            # Broken symlink - recreate it
            info "Fixing broken vault symlink for ${env}..."
            rm -f "$inv_vault"
        fi
    elif [[ -f "$inv_vault" ]]; then
        # Regular file exists - don't replace it
        return 0
    fi
    
    # Need to create symlink
    info "Setting up vault symlink for ${env}..."
    mkdir -p "$inv_vault_dir"
    
    # Create relative symlink
    cd "$inv_vault_dir"
    ln -sf "../../../../roles/secrets/vars/vault.yml" "vault.yml"
    cd "${REPO_ROOT}"
    
    if [[ -L "$inv_vault" ]] && [[ -e "$inv_vault" ]]; then
        success "Vault symlink created for ${env}"
    else
        warn "Failed to create vault symlink for ${env}"
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
    local data_ip="${network_base}.206"
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
    check_service "AuthZ API" "$authz_ip" "curl -sf --connect-timeout 3 'http://$authz_ip:8010/health/live'" || true
    check_service "Data API" "$data_ip" "curl -sf --connect-timeout 3 'http://$data_ip:8002/health'" || true
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
    echo -e "${BLUE}[1/4] Vault Links${NC}"
    separator
    verify_vault_links || ((errors++))
    
    echo ""
    echo -e "${BLUE}[2/4] Vault Access${NC}"
    separator
    verify_vault_access || ((errors++))
    
    echo ""
    echo -e "${BLUE}[3/4] Ansible Connectivity${NC}"
    separator
    verify_ansible_connectivity "$ENV" || ((errors++))
    
    echo ""
    echo -e "${BLUE}[4/4] Service Health${NC}"
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

# Verify vault links are set up for both staging and production
verify_vault_links() {
    echo ""
    info "Checking vault symlinks..."
    echo ""
    
    local ansible_dir="${REPO_ROOT}/provision/ansible"
    local role_vault="${ansible_dir}/roles/secrets/vars/vault.yml"
    local issues=0
    
    # Check if the role vault exists
    if [[ ! -f "$role_vault" ]]; then
        error "Main vault file missing: roles/secrets/vars/vault.yml"
        info "Create from template:"
        echo "  cd provision/ansible"
        echo "  cp roles/secrets/vars/vault.example.yml roles/secrets/vars/vault.yml"
        echo "  ansible-vault encrypt roles/secrets/vars/vault.yml"
        return 1
    fi
    
    success "Main vault file exists: roles/secrets/vars/vault.yml"
    
    # Check symlinks for both environments
    for env in staging production; do
        local inv_vault="${ansible_dir}/inventory/${env}/group_vars/all/vault.yml"
        
        if [[ -L "$inv_vault" ]]; then
            if [[ -e "$inv_vault" ]]; then
                echo -e "  ${GREEN}✓${NC} ${env}: symlink OK"
            else
                echo -e "  ${RED}✗${NC} ${env}: broken symlink"
                info "Fixing symlink for ${env}..."
                check_and_setup_vault_links "$env"
                ((issues++))
            fi
        elif [[ -f "$inv_vault" ]]; then
            echo -e "  ${YELLOW}○${NC} ${env}: regular file (not symlinked)"
        else
            echo -e "  ${RED}✗${NC} ${env}: missing vault symlink"
            info "Creating symlink for ${env}..."
            check_and_setup_vault_links "$env"
            
            # Verify it was created
            if [[ -L "$inv_vault" ]] && [[ -e "$inv_vault" ]]; then
                echo -e "  ${GREEN}✓${NC} ${env}: symlink created"
            else
                ((issues++))
            fi
        fi
    done
    
    echo ""
    
    if [[ $issues -eq 0 ]]; then
        success "Vault links verified"
        return 0
    else
        warn "Some vault links had issues"
        return 1
    fi
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
    # Check if busibox-frontend monorepo is available locally
    local BUSIBOX_FRONTEND_DIR="${BUSIBOX_FRONTEND_DIR:-$(cd "${REPO_ROOT}/../busibox-frontend" 2>/dev/null && pwd || echo "")}"
    local BUSIBOX_PORTAL_DIR="${BUSIBOX_FRONTEND_DIR:+${BUSIBOX_FRONTEND_DIR}/apps/portal}"
    local has_local_repos=true
    
    if [[ -z "$BUSIBOX_PORTAL_DIR" ]] || [[ ! -d "$BUSIBOX_PORTAL_DIR" ]]; then
        has_local_repos=false
    fi
    
    while true; do
        echo ""
        
        if [[ "$has_local_repos" == "false" ]]; then
            warn "busibox-frontend monorepo not found locally"
            info "On Proxmox, app configuration must be run from the apps container"
            echo ""
            echo "To configure apps, SSH to the apps container and run:"
            echo "  ${CYAN}cd /srv/apps/busibox-portal && npx tsx scripts/activate-user.ts${NC}"
            echo "  ${CYAN}cd /srv/apps/busibox-portal && npx tsx scripts/fix-builtin-apps.ts${NC}"
            echo ""
            echo "Or set BUSIBOX_FRONTEND_DIR environment variable if busibox-frontend is elsewhere."
            echo ""
            
            menu "App Configuration (Limited - No Local Repos)" \
                "Register AuthZ Clients (works without local repos)" \
                "Back"
            
            local choice=""
            read -p "$(echo -e "${BOLD}Select option [1-2]:${NC} ")" choice
            
            case "${choice:-}" in
                1)
                    header "Register AuthZ Clients" 70
                    if confirm "Register AuthZ clients?"; then
                        # Register clients even without local repos
                        bash "${REPO_ROOT}/scripts/setup/configure-apps.sh" --authz 2>&1 || warn "Some steps may have failed (expected without local repos)"
                    fi
                    pause
                    ;;
                2|b|B|"")
                    return 0
                    ;;
            esac
        else
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
        fi
    done
}

# ========================================================================
# Secrets Configuration (Proxmox only)
# ========================================================================

secrets_configuration() {
    while true; do
        echo ""
        menu "Secrets & Keys" \
            "Edit Ansible Vault (secrets)" \
            "View Vault Variables (masked)" \
            "Sync Vault with Example (update structure)" \
            "Generate .env.local from Vault" \
            "Back"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select option [1-5]:${NC} ")" choice
        
        case "${choice:-}" in
            1)
                header "Edit Ansible Vault" 70
                cd "${REPO_ROOT}/provision/ansible"
                ansible-vault edit roles/secrets/vars/vault.yml || error "Failed to edit vault"
                cd "${REPO_ROOT}"
                pause
                ;;
            2)
                header "View Vault Variables" 70
                cd "${REPO_ROOT}/provision/ansible"
                ansible-vault view roles/secrets/vars/vault.yml | grep -E "^[a-z_]+:" | sed 's/:.*$/: <masked>/' || error "Failed"
                cd "${REPO_ROOT}"
                pause
                ;;
            3)
                bash "${REPO_ROOT}/scripts/vault/sync-vault.sh" || error "Failed"
                pause
                ;;
            4)
                bash "${REPO_ROOT}/scripts/vault/generate-env-from-vault.sh" || error "Failed"
                pause
                ;;
            5|b|B|"")
                return 0
                ;;
        esac
    done
}

# ========================================================================
# Configure Dev Apps Directory
# ========================================================================

configure_dev_apps_dir() {
    header "Configure Dev Apps Directory" 70
    
    local current_dir
    current_dir=$(get_dev_apps_dir)
    
    echo ""
    info "Dev Apps Directory is used for local development with hot-reload."
    echo ""
    echo "When set, you can deploy apps in 'Local Development' mode from Busibox Portal."
    echo "The directory should contain your app source code as ${BOLD}actual subdirectories${NC}."
    echo ""
    warn "IMPORTANT: Docker cannot follow symlinks in volume mounts!"
    echo "Your apps must be real directories, not symlinks."
    echo ""
    echo "Example structure:"
    echo "  /Users/you/Code/"
    echo "    ├── estimator/        <- Your app with busibox.json"
    echo "    ├── busibox-analysis/ <- Another app"
    echo "    └── my-app/           <- etc."
    echo ""
    echo "Tip: Point DEV_APPS_DIR to your main code directory (e.g., ~/Code)"
    echo "where all your project folders live."
    echo ""
    
    if [[ -n "$current_dir" ]]; then
        success "Current setting: $current_dir"
        echo ""
        if [[ -d "$current_dir" ]]; then
            # List directories that contain busibox.json
            local apps_found=0
            echo "Apps found in this directory:"
            for d in "$current_dir"/*; do
                if [[ -d "$d" ]] && [[ -f "$d/busibox.json" ]]; then
                    echo "  ✓ $(basename "$d")"
                    apps_found=$((apps_found + 1))
                fi
            done
            if [[ $apps_found -eq 0 ]]; then
                warn "No apps with busibox.json found in $current_dir"
            fi
            echo ""
        else
            warn "Directory does not exist: $current_dir"
            echo ""
        fi
    else
        warn "Not configured"
        echo ""
    fi
    
    echo "Options:"
    echo "  1) Set/update directory path"
    echo "  2) Clear setting"
    echo "  3) Back"
    echo ""
    
    local choice=""
    read -p "$(echo -e "${BOLD}Select option [1-3]:${NC} ")" choice
    
    case "${choice:-}" in
        1)
            echo ""
            # Show default suggestion (parent of busibox)
            local default_dir
            default_dir="$(dirname "$REPO_ROOT")"
            
            read -p "$(echo -e "${BOLD}Enter directory path [${default_dir}]:${NC} ")" new_dir
            
            # Use default if empty
            new_dir="${new_dir:-$default_dir}"
            
            # Expand ~ to home directory
            new_dir="${new_dir/#\~/$HOME}"
            
            if [[ -z "$new_dir" ]]; then
                warn "No path entered"
                return
            fi
            
            if [[ ! -d "$new_dir" ]]; then
                warn "Directory does not exist: $new_dir"
                if confirm "Create it?"; then
                    mkdir -p "$new_dir" || { error "Failed to create directory"; return; }
                    success "Created: $new_dir"
                else
                    return
                fi
            fi
            
            # Validate it's an absolute path
            if [[ "$new_dir" != /* ]]; then
                new_dir="$(cd "$new_dir" 2>/dev/null && pwd)"
            fi
            
            # Save to state file
            set_dev_apps_dir "$new_dir"
            
            # Also update the .env file for Docker
            local env_file
            env_file=$(get_env_file_path 2>/dev/null || echo "${REPO_ROOT}/.env.dev")
            
            if [[ -f "$env_file" ]]; then
                # Update or add DEV_APPS_DIR in env file
                if grep -q "^DEV_APPS_DIR=" "$env_file" 2>/dev/null; then
                    # Update existing
                    sed -i.bak "s|^DEV_APPS_DIR=.*|DEV_APPS_DIR=${new_dir}|" "$env_file"
                    rm -f "${env_file}.bak"
                else
                    # Append
                    echo "" >> "$env_file"
                    echo "# Local Development Apps Directory" >> "$env_file"
                    echo "DEV_APPS_DIR=${new_dir}" >> "$env_file"
                fi
                
                # Also update DEV_APPS_DIR_HOST
                if grep -q "^DEV_APPS_DIR_HOST=" "$env_file" 2>/dev/null; then
                    sed -i.bak "s|^DEV_APPS_DIR_HOST=.*|DEV_APPS_DIR_HOST=${new_dir}|" "$env_file"
                    rm -f "${env_file}.bak"
                else
                    echo "DEV_APPS_DIR_HOST=${new_dir}" >> "$env_file"
                fi
            else
                # Create minimal env file
                echo "# Busibox Environment Configuration" > "$env_file"
                echo "" >> "$env_file"
                echo "# Local Development Apps Directory" >> "$env_file"
                echo "DEV_APPS_DIR=${new_dir}" >> "$env_file"
                echo "DEV_APPS_DIR_HOST=${new_dir}" >> "$env_file"
            fi
            
            success "Dev Apps Directory set to: $new_dir"
            echo ""
            info "Saved to:"
            echo "  - State file: $(basename "$BUSIBOX_STATE_FILE")"
            echo "  - Env file: $(basename "$env_file")"
            echo ""
            info "To apply this setting, recreate the deploy-api container:"
            echo "  make docker-down SERVICE=deploy-api && make docker-up SERVICE=deploy-api"
            ;;
        2)
            set_dev_apps_dir ""
            
            # Also remove from env file
            local env_file
            env_file=$(get_env_file_path 2>/dev/null || echo "${REPO_ROOT}/.env.dev")
            if [[ -f "$env_file" ]]; then
                sed -i.bak '/^DEV_APPS_DIR=/d' "$env_file"
                sed -i.bak '/^DEV_APPS_DIR_HOST=/d' "$env_file"
                rm -f "${env_file}.bak"
            fi
            
            success "Dev Apps Directory cleared from state and env file"
            ;;
        3|"")
            return
            ;;
    esac
    
    pause
}

# ========================================================================
# Configure GitHub Token
# ========================================================================

configure_github_token() {
    header "Configure GitHub Token" 70
    
    local current_token
    current_token=$(get_github_token 2>/dev/null || true)
    
    echo ""
    if [[ -n "$current_token" ]]; then
        # Mask the token for display
        local masked="${current_token:0:4}...${current_token: -4}"
        success "Current token: ${masked}"
        echo ""
        
        # Check if it's still valid
        if validate_github_token "$current_token" --quiet 2>/dev/null; then
            success "Token is valid"
        else
            warn "Token may be expired or have insufficient permissions"
        fi
        echo ""
    else
        warn "No GitHub token configured"
        echo ""
    fi
    
    echo "Options:"
    echo "  1) Set/update GitHub token"
    echo "  2) Validate current token"
    echo "  3) Clear token"
    echo "  4) Back"
    echo ""
    
    local choice=""
    read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
    
    case "${choice:-}" in
        1)
            echo ""
            # Use ensure_github_token which handles prompting and validation
            if ensure_github_token; then
                # Also update the .env file for Docker
                local env_file
                env_file=$(get_env_file_path 2>/dev/null || echo "${REPO_ROOT}/.env.local")
                
                # Update or add GITHUB_AUTH_TOKEN in env file
                if [[ -f "$env_file" ]]; then
                    if grep -q "^GITHUB_AUTH_TOKEN=" "$env_file" 2>/dev/null; then
                        # Update existing
                        sed -i.bak "s|^GITHUB_AUTH_TOKEN=.*|GITHUB_AUTH_TOKEN=${GITHUB_AUTH_TOKEN}|" "$env_file"
                        rm -f "${env_file}.bak"
                    else
                        # Append
                        echo "" >> "$env_file"
                        echo "# GitHub Authentication" >> "$env_file"
                        echo "GITHUB_AUTH_TOKEN=${GITHUB_AUTH_TOKEN}" >> "$env_file"
                    fi
                else
                    # Create minimal env file
                    echo "# Busibox Environment Configuration" > "$env_file"
                    echo "" >> "$env_file"
                    echo "# GitHub Authentication" >> "$env_file"
                    echo "GITHUB_AUTH_TOKEN=${GITHUB_AUTH_TOKEN}" >> "$env_file"
                fi
                
                echo ""
                success "Token saved to state and ${env_file}"
                info "Restart Docker services to apply: make docker-down && make docker-up"
            fi
            ;;
        2)
            echo ""
            if [[ -n "$current_token" ]]; then
                info "Validating token..."
                if validate_github_token "$current_token"; then
                    echo ""
                    success "Token is valid with all required permissions"
                fi
            else
                error "No token to validate"
            fi
            ;;
        3)
            # Clear from vault (source of truth for secrets)
            if command -v yq &>/dev/null && [[ -f "$VAULT_FILE" ]]; then
                write_vault_secret "secrets.github.personal_access_token" "" 2>/dev/null || true
            fi
            
            # Also remove from env file
            local env_file
            env_file=$(get_env_file_path 2>/dev/null || echo "${REPO_ROOT}/.env.local")
            if [[ -f "$env_file" ]]; then
                sed -i.bak '/^GITHUB_AUTH_TOKEN=/d' "$env_file"
                rm -f "${env_file}.bak"
            fi
            
            # Clear from environment
            unset GITHUB_AUTH_TOKEN
            
            success "GitHub token cleared"
            ;;
        4|"")
            return
            ;;
    esac
    
    pause
}

# ========================================================================
# Docker Configuration Menu
# ========================================================================

docker_menu() {
    while true; do
        clear
        box "Configuration - Local Docker" 70
        status_bar "$ENV" "$BACKEND" "$(get_install_status)" 70
        
        # Show current status
        local dev_apps_dir github_status
        dev_apps_dir=$(get_dev_apps_dir)
        
        # Check GitHub token status
        local github_token
        github_token=$(get_github_token 2>/dev/null || true)
        if [[ -n "$github_token" ]]; then
            github_status="${GREEN}✓ configured${NC}"
        else
            github_status="${RED}✗ not set${NC}"
        fi
        
        echo ""
        echo -e "  ${DIM}GitHub Token: ${NC}${github_status}"
        
        # Show Dev Apps Dir status (only relevant for development environment)
        if [[ "$ENV" == "development" ]]; then
            if [[ -n "$dev_apps_dir" ]]; then
                echo -e "  ${DIM}Dev Apps Dir: ${NC}${GREEN}${dev_apps_dir}${NC}"
            else
                echo -e "  ${DIM}Dev Apps Dir: ${NC}${YELLOW}not configured${NC}"
            fi
        fi
        
        echo ""
        menu "Docker Configuration" \
            "Configure GitHub Token (REQUIRED for builds)" \
            "App Configuration (admin, OAuth clients)" \
            "Configure Dev Apps Directory (local development)" \
            "Edit Ansible Vault (secrets)" \
            "View Vault Variables (masked)" \
            "Sync Vault with Example (update structure)" \
            "Generate .env.local from Vault" \
            "Back to Main Menu"
        
        local choice=""
        read -p "$(echo -e "${BOLD}Select option [1-8]:${NC} ")" choice
        
        case "${choice:-}" in
            1)
                configure_github_token
                ;;
            2)
                app_configuration
                ;;
            3)
                configure_dev_apps_dir
                ;;
            4)
                header "Edit Ansible Vault" 70
                cd "${REPO_ROOT}/provision/ansible"
                ansible-vault edit roles/secrets/vars/vault.yml || error "Failed to edit vault"
                cd "${REPO_ROOT}"
                pause
                ;;
            5)
                header "View Vault Variables" 70
                cd "${REPO_ROOT}/provision/ansible"
                ansible-vault view roles/secrets/vars/vault.yml | grep -E "^[a-z_]+:" | sed 's/:.*$/: <masked>/' || error "Failed"
                cd "${REPO_ROOT}"
                pause
                ;;
            6)
                bash "${REPO_ROOT}/scripts/vault/sync-vault.sh" || error "Failed"
                pause
                ;;
            7)
                bash "${REPO_ROOT}/scripts/vault/generate-env-from-vault.sh" || error "Failed"
                pause
                ;;
            8|b|B|"")
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
            "Secrets & Keys (vault, secrets)" \
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
